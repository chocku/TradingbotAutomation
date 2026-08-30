"""Fetch market data from Alpaca (with yfinance fallback for VIX/today's bar)."""
import time
import logging
import pandas as pd
import numpy as np
from datetime import date, datetime

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import (
    StockLatestBarRequest, StockLatestTradeRequest,
    StockBarsRequest,
)
from alpaca.data.timeframe import TimeFrame

from config import (
    PARQUET_QQQ, PARQUET_VIX,
    DATA_RETRY_COUNT, DATA_RETRY_DELAY_SEC,
)

log = logging.getLogger(__name__)


# ── Indicators (mirrors compute.py logic) ─────────────────────────────────────

def _sma(close: pd.Series, w: int) -> pd.Series:
    return close.rolling(w, min_periods=w).mean()


def _rsi(close: pd.Series, w: int = 14) -> pd.Series:
    delta    = close.diff()
    gain     = delta.clip(lower=0)
    loss     = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / w, adjust=False, min_periods=w).mean()
    avg_loss = loss.ewm(alpha=1 / w, adjust=False, min_periods=w).mean()
    rs       = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy().sort_values("date").reset_index(drop=True)
    c = df["c"]
    df["sma_10"]  = _sma(c, 10)
    df["sma_20"]  = _sma(c, 20)
    df["sma_50"]  = _sma(c, 50)
    df["sma_200"] = _sma(c, 200)
    df["rsi_14"]  = _rsi(c, 14)
    return df


# ── NYSE trading day filter ───────────────────────────────────────────────────

def _filter_trading_days(df: pd.DataFrame) -> pd.DataFrame:
    """Remove any rows whose date is not a valid NYSE trading day (weekends + holidays)."""
    import pandas_market_calendars as mcal
    if df.empty:
        return df
    nyse       = mcal.get_calendar("NYSE")
    start      = df["date"].min().strftime("%Y-%m-%d")
    end        = df["date"].max().strftime("%Y-%m-%d")
    schedule   = nyse.schedule(start_date=start, end_date=end)
    valid_days = set(schedule.index.normalize())
    mask       = df["date"].isin(valid_days)
    removed    = (~mask).sum()
    if removed:
        log.info("Filtered %d non-trading day rows from parquet (weekends/holidays)", removed)
    return df[mask].reset_index(drop=True)


# ── Backfill previous day's actual close ─────────────────────────────────────

def backfill_previous_close(hist: pd.DataFrame,
                             data_client: StockHistoricalDataClient) -> pd.DataFrame:
    """
    1. Insert any missing trading day rows between the parquet's last date and today.
    2. Correct any existing rows written with intraday/proxy prices.
    Uses yfinance for accurate daily OHLCV (Alpaca free tier restricts historical bar access).
    Fetches up to 6 months so large gaps (e.g. after a long restart) are filled automatically.
    """
    import yfinance as yf
    today = pd.Timestamp(date.today())
    if hist.empty:
        return hist

    try:
        ticker  = yf.Ticker("QQQ")
        yf_hist = ticker.history(period="6mo", interval="1d")
        if yf_hist.empty:
            log.warning("yfinance returned empty data for backfill — skipping")
            return hist

        yf_hist.index = pd.to_datetime(yf_hist.index).normalize().tz_localize(None)
        hist = hist.copy()
        corrected = inserted = 0

        for yf_date, row in yf_hist.iterrows():
            yf_date = pd.Timestamp(yf_date).normalize()
            if yf_date >= today:
                continue  # today's bar is handled separately
            mask = hist["date"] == yf_date
            if mask.any():
                # Row exists — correct if stale
                old_c = float(hist.loc[mask, "c"].iloc[0])
                new_c = float(row["Close"])
                if abs(new_c - old_c) > 0.01:
                    hist.loc[mask, "o"] = float(row["Open"])
                    hist.loc[mask, "h"] = float(row["High"])
                    hist.loc[mask, "l"] = float(row["Low"])
                    hist.loc[mask, "c"] = new_c
                    hist.loc[mask, "v"] = float(row["Volume"])
                    log.info("Backfilled %s: close %.4f → %.4f", yf_date.date(), old_c, new_c)
                    corrected += 1
            else:
                # Row missing — insert it
                new_row = pd.DataFrame([{
                    "date": yf_date,
                    "o":    float(row["Open"]),
                    "h":    float(row["High"]),
                    "l":    float(row["Low"]),
                    "c":    float(row["Close"]),
                    "v":    float(row["Volume"]),
                }])
                hist = pd.concat([hist, new_row], ignore_index=True)
                log.info("Inserted missing row for %s: close=%.4f", yf_date.date(), float(row["Close"]))
                inserted += 1

        if corrected == 0 and inserted == 0:
            log.info("All recent closes already accurate — no backfill needed")
        else:
            log.info("Backfill complete: %d corrected, %d inserted", corrected, inserted)

        hist = hist.sort_values("date").reset_index(drop=True)

    except Exception as e:
        log.warning("Could not backfill previous closes — using stored values: %s", e)

    return hist


# ── Today's bar ───────────────────────────────────────────────────────────────

def _fetch_bar_alpaca(data_client: StockHistoricalDataClient) -> dict:
    """Return today's QQQ bar. Use yfinance for accurate daily OHLCV; fall back to Alpaca latest bar."""
    import yfinance as yf
    try:
        ticker = yf.Ticker("QQQ")
        hist   = ticker.history(period="1d", interval="1d")
        if not hist.empty:
            row = hist.iloc[-1]
            bar = {
                "date": pd.Timestamp(date.today()),
                "o":    float(row["Open"]),
                "h":    float(row["High"]),
                "l":    float(row["Low"]),
                "c":    float(row["Close"]),
                "v":    float(row["Volume"]),
            }
            log.info("Fetched QQQ daily bar from yfinance: close=%.2f vol=%.0f", bar["c"], bar["v"])
            return bar
    except Exception as e:
        log.warning("yfinance daily bar failed: %s — falling back to Alpaca latest bar", e)

    # Fallback: Alpaca latest bar (IEX, free tier)
    bar_req = StockLatestBarRequest(symbol_or_symbols=["QQQ"])
    bar     = data_client.get_stock_latest_bar(bar_req)["QQQ"]
    log.warning("Using Alpaca latest minute bar as proxy (may be after-hours)")
    return {
        "date": pd.Timestamp(date.today()),
        "o":    bar.open,
        "h":    bar.high,
        "l":    bar.low,
        "c":    bar.close,
        "v":    bar.volume,
    }


def _fetch_bar_yfinance() -> dict:
    import yfinance as yf
    ticker = yf.Ticker("QQQ")
    hist   = ticker.history(period="1d", interval="1d")
    if hist.empty:
        raise RuntimeError("yfinance returned empty data for QQQ")
    row = hist.iloc[-1]
    return {
        "date": pd.Timestamp(date.today()),
        "o":    row["Open"],
        "h":    row["High"],
        "l":    row["Low"],
        "c":    row["Close"],
        "v":    row["Volume"],
    }


def fetch_today_bar(data_client: StockHistoricalDataClient) -> dict:
    """Fetch today's QQQ bar with retry + yfinance fallback."""
    last_err = None
    for attempt in range(1, DATA_RETRY_COUNT + 1):
        try:
            bar = _fetch_bar_alpaca(data_client)
            log.info("Fetched QQQ bar from Alpaca: close=%.2f", bar["c"])
            return bar
        except Exception as e:
            last_err = e
            log.warning("Alpaca bar fetch attempt %d failed: %s", attempt, e)
            if attempt < DATA_RETRY_COUNT:
                time.sleep(DATA_RETRY_DELAY_SEC * (2 ** (attempt - 1)))

    log.warning("Alpaca failed after %d retries — falling back to yfinance", DATA_RETRY_COUNT)
    try:
        bar = _fetch_bar_yfinance()
        log.info("Fetched QQQ bar from yfinance fallback: close=%.2f", bar["c"])
        return bar
    except Exception as e:
        raise RuntimeError(f"All data sources failed. Alpaca: {last_err}  yfinance: {e}")


# ── VIX ───────────────────────────────────────────────────────────────────────

def _fetch_vix_today_yfinance() -> float:
    import yfinance as yf
    vix  = yf.Ticker("^VIX")
    hist = vix.history(period="2d", interval="1d")
    if hist.empty:
        raise RuntimeError("yfinance returned empty data for ^VIX")
    return float(hist["Close"].iloc[-1])


def fetch_vix_today() -> float:
    """Fetch today's VIX via yfinance (Alpaca does not serve VIX in free tier)."""
    try:
        vix = _fetch_vix_today_yfinance()
        log.info("Fetched VIX: %.2f", vix)
        return vix
    except Exception as e:
        raise RuntimeError(f"Failed to fetch VIX: {e}")


# ── Strategy input builder ────────────────────────────────────────────────────

def build_strategy_input(data_client: StockHistoricalDataClient) -> pd.DataFrame:
    """
    Load historical QQQ, append today's bar, compute indicators, merge VIX.
    Returns a DataFrame ready for strategy.compute_signals().
    """
    # Load historical data (raw OHLCV)
    hist = pd.read_parquet(PARQUET_QQQ)
    hist["date"] = pd.to_datetime(hist["date"]).dt.normalize()

    # Correct previous day's proxy close with actual closing price
    hist = backfill_previous_close(hist, data_client)

    # Load VIX history
    vix_hist = pd.read_parquet(PARQUET_VIX)
    vix_hist["date"] = pd.to_datetime(vix_hist["date"]).dt.normalize()
    if "vix_close" not in vix_hist.columns:
        # column might be named "close" in raw file
        close_col = [c for c in vix_hist.columns if "close" in c.lower()][0]
        vix_hist = vix_hist.rename(columns={close_col: "vix_close"})

    # Fetch today's bar
    today_bar  = fetch_today_bar(data_client)
    today_vix  = fetch_vix_today()
    today_date = pd.Timestamp(date.today())

    # Keep only valid NYSE trading days (strips weekends + holidays)
    hist = _filter_trading_days(hist)
    hist = hist[hist["date"] != today_date]

    # Append today
    today_row = pd.DataFrame([today_bar])
    today_row["date"] = pd.to_datetime(today_row["date"])
    df = pd.concat([hist, today_row], ignore_index=True)

    # Compute indicators on full series
    df = _add_indicators(df)

    # Merge VIX
    vix_today = pd.DataFrame([{"date": today_date, "vix_close": today_vix}])
    vix_all   = pd.concat(
        [vix_hist[["date", "vix_close"]], vix_today], ignore_index=True
    ).drop_duplicates("date", keep="last")

    df = df.merge(vix_all[["date", "vix_close"]], on="date", how="left")
    df["vix_close"] = df["vix_close"].ffill()

    df = df.sort_values("date").reset_index(drop=True)
    log.info(
        "Strategy input built: %d rows, last date=%s",
        len(df), df["date"].iloc[-1].date(),
    )

    # Persist today's bar back to the parquet (trading days only)
    raw_cols     = ["date", "o", "h", "l", "c", "v"]
    cols_present = [c for c in raw_cols if c in df.columns]
    save_df      = _filter_trading_days(df[cols_present].copy())
    save_df.to_parquet(PARQUET_QQQ, index=False, engine="pyarrow")
    log.info("Updated %s with %d rows", PARQUET_QQQ, len(save_df))

    return df


# ── Execution price ───────────────────────────────────────────────────────────

def fetch_execution_price(ticker: str, data_client: StockHistoricalDataClient,
                          qqq_df: pd.DataFrame) -> float:
    """
    Return the price used to size the position.
    For QQQ: use the close already fetched.
    For TQQQ: fetch latest trade from Alpaca.
    """
    if ticker == "QQQ":
        return float(qqq_df.iloc[-1]["c"])

    trade_req = StockLatestTradeRequest(symbol_or_symbols=["TQQQ"])
    trade     = data_client.get_stock_latest_trade(trade_req)["TQQQ"]
    log.info("Fetched TQQQ price: %.2f", trade.price)
    return float(trade.price)
