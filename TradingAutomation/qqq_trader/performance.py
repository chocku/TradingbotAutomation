"""Build a daily, normalized strategy-vs-QQQ performance history."""
from __future__ import annotations

import json
import os
from datetime import date, datetime
from pathlib import Path

import pandas as pd

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR.parent / "data"
STARTING_VALUE = 100_000.0


def _trade_date(trade: dict) -> str | None:
    try:
        ts = pd.Timestamp(trade["ts"])
        if ts.tzinfo is not None:
            ts = ts.tz_convert("America/New_York")
        return ts.strftime("%Y-%m-%d")
    except Exception:
        return None


def _prices(symbol: str, start: str, end: str) -> pd.Series:
    """Load local prices and fill recent gaps from yfinance."""
    path = DATA_DIR / ("QQQ_daily_full.parquet" if symbol == "QQQ" else "TQQQ_daily.parquet")
    pieces = []
    if path.exists():
        frame = pd.read_parquet(path)
        frame["date"] = pd.to_datetime(frame["date"]).dt.normalize()
        pieces.append(frame[["date", "c"]].rename(columns={"c": symbol}))

    # The local cache can lag the trade log; yfinance supplies the missing tail.
    try:
        import yfinance as yf
        fetched = yf.download(symbol, start=start, end=end, auto_adjust=False,
                               progress=False, threads=False)
        if not fetched.empty:
            close = fetched["Close"]
            if isinstance(close, pd.DataFrame):
                close = close.iloc[:, 0]
            fresh = close.rename(symbol).rename_axis("date").reset_index()
            fresh["date"] = pd.to_datetime(fresh["date"]).dt.normalize()
            pieces.append(fresh[["date", symbol]])
    except Exception:
        pass

    if not pieces:
        return pd.Series(dtype=float)
    merged = pd.concat(pieces, ignore_index=True).drop_duplicates("date", keep="last")
    merged = merged.sort_values("date").set_index("date")[symbol].astype(float)
    return merged


def build_performance(trades: list[dict]) -> dict:
    """Return JSON-serializable daily performance data from trade history."""
    dated = [(d, t) for t in trades if (d := _trade_date(t))]
    if not dated:
        return {"starting_value": STARTING_VALUE, "rows": [], "summary": {}}

    dated.sort(key=lambda x: x[0])
    first_date = dated[0][0]
    last_date = max(datetime.now().strftime("%Y-%m-%d"), dated[-1][0])
    qqq = _prices("QQQ", first_date, last_date)
    tqqq = _prices("TQQQ", first_date, last_date)
    if qqq.empty:
        return {"starting_value": STARTING_VALUE, "rows": [], "summary": {}}

    dates = sorted(qqq.index)
    first_dt = pd.Timestamp(first_date)
    dates = [d for d in dates if d >= first_dt]
    if len(dates) < 2:
        return {"starting_value": STARTING_VALUE, "rows": [], "summary": {}}

    # Last daily signal wins. Signals are effective for the following close-to-close day.
    signals = {}
    for d, trade in dated:
        signals[d] = trade
    signal_dates = sorted(signals)
    strategy_value = STARTING_VALUE
    qqq_value = STARTING_VALUE
    rows = []

    for idx in range(len(dates) - 1):
        d, next_d = dates[idx], dates[idx + 1]
        d_str = d.strftime("%Y-%m-%d")
        active = None
        for sd in signal_dates:
            if sd <= d_str:
                active = signals[sd]
            else:
                break
        if active is None or d not in qqq.index or next_d not in qqq.index:
            continue

        signal = active.get("signal") or {}
        ticker = active.get("ticker", "QQQ")
        allocation = float(active.get("allocation_pct", 0) or 0)
        qqq_ret = float(qqq.loc[next_d] / qqq.loc[d] - 1)
        if ticker == "TQQQ" and d in tqqq.index and next_d in tqqq.index:
            instrument_ret = float(tqqq.loc[next_d] / tqqq.loc[d] - 1)
        elif ticker == "QQQ":
            instrument_ret = qqq_ret
        else:
            instrument_ret = 0.0
        strategy_ret = allocation * instrument_ret
        strategy_value *= 1 + strategy_ret
        qqq_value *= 1 + qqq_ret
        rows.append({
            "date": d_str,
            "next_date": next_d.strftime("%Y-%m-%d"),
            "signal": f"{ticker} {allocation:.0%}" if allocation else "Cash",
            "regime": _market_state(signal),
            "mr_score": signal.get("mr_score", "—"),
            "condition": _condition_summary(signal, ticker, allocation),
            "strategy_return": round(strategy_ret * 100, 4),
            "qqq_return": round(qqq_ret * 100, 4),
            "alpha": round((strategy_ret - qqq_ret) * 100, 4),
            "strategy_value": round(strategy_value, 2),
            "qqq_value": round(qqq_value, 2),
        })

    if not rows:
        return {"starting_value": STARTING_VALUE, "rows": [], "summary": {}}
    last = rows[-1]
    summary = {
        "strategy_value": last["strategy_value"],
        "qqq_value": last["qqq_value"],
        "strategy_return": round((last["strategy_value"] / STARTING_VALUE - 1) * 100, 2),
        "qqq_return": round((last["qqq_value"] / STARTING_VALUE - 1) * 100, 2),
        "outperformance": round(last["strategy_value"] - last["qqq_value"], 2),
        "days": len(rows),
        "through": last["next_date"],
    }
    return {"starting_value": STARTING_VALUE, "rows": rows, "summary": summary}


def _condition_summary(signal: dict, ticker: str, allocation: float) -> str:
    """Human-readable, immutable explanation of the allocation in a log row."""
    regime = signal.get("regime", "")
    if regime == "CANDLE_CASH":
        return "Candle shock: >2% drop and below SMA10"
    if regime == "BEAR_CASH":
        return "Below SMA200: bear-market exit"
    if regime == "SMA50_PROT":
        return f"Below SMA50: {ticker} {allocation:.0%} protection mode"
    if signal.get("overbought"):
        return "Overbought: RSI or extended above SMA20"
    components = signal.get("components", {})
    active = [
        label for key, label in (
            ("pullback", "below SMA20"),
            ("oversold", "RSI < 40"),
            ("two_down", "two red closes"),
            ("vix_fear", "VIX > 20"),
            ("bb_below", "below BB mid"),
        ) if components.get(key)
    ]
    return ", ".join(active) if active else "No MR triggers: full bull regime"


def _market_state(signal: dict) -> str:
    """Translate internal regime codes into dashboard-friendly language."""
    labels = {
        "CANDLE_CASH": "Candle shock — cash",
        "BEAR_CASH": "Below SMA200 — cash",
        "SMA50_PROT": "Below SMA50 — TQQQ 25% protection",
        "BULL_FULL": "Full bull regime — TQQQ sized by MR score",
    }
    return labels.get(signal.get("regime"), "Unknown market state")


def save_local(data: dict) -> bool:
    """Cache locally when the runtime permits; S3 remains the durable source."""
    path = BASE_DIR / "logs" / "performance_daily.json"
    try:
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return True
    except OSError:
        return False