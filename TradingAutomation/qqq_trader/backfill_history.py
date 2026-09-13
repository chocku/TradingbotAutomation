"""
One-time (or re-runnable) seed script for the QQQ/VIX parquet history files.

The strategy needs ~200 trading days of history to compute a valid SMA-200,
but these files are .gitignore'd and were never backed up anywhere, so a
fresh environment starts with none. This pulls several years of daily bars
from yfinance and writes them in the exact shape data/fetcher.py expects.
"""
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import pandas as pd
import yfinance as yf

from config import PARQUET_QQQ, PARQUET_VIX
from data.fetcher import _filter_trading_days

logging.basicConfig(level=logging.INFO, format="%(levelname)-8s %(message)s")
log = logging.getLogger("backfill_history")

LOOKBACK = "3y"  # comfortably more than the 200-day SMA needs


def backfill_qqq() -> None:
    hist = yf.Ticker("QQQ").history(period=LOOKBACK, interval="1d")
    if hist.empty:
        raise RuntimeError("yfinance returned no QQQ history")

    df = pd.DataFrame({
        "date": pd.to_datetime(hist.index).tz_localize(None).normalize(),
        "o": hist["Open"].values,
        "h": hist["High"].values,
        "l": hist["Low"].values,
        "c": hist["Close"].values,
        "v": hist["Volume"].values,
    })
    df = _filter_trading_days(df).sort_values("date").reset_index(drop=True)

    Path(PARQUET_QQQ).parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(PARQUET_QQQ, index=False, engine="pyarrow")
    log.info("Wrote %d rows to %s (range %s -> %s)",
              len(df), PARQUET_QQQ, df["date"].min().date(), df["date"].max().date())


def backfill_vix() -> None:
    hist = yf.Ticker("^VIX").history(period=LOOKBACK, interval="1d")
    if hist.empty:
        raise RuntimeError("yfinance returned no VIX history")

    df = pd.DataFrame({
        "date": pd.to_datetime(hist.index).tz_localize(None).normalize(),
        "vix_close": hist["Close"].values,
    })
    df = _filter_trading_days(df).sort_values("date").reset_index(drop=True)

    Path(PARQUET_VIX).parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(PARQUET_VIX, index=False, engine="pyarrow")
    log.info("Wrote %d rows to %s (range %s -> %s)",
              len(df), PARQUET_VIX, df["date"].min().date(), df["date"].max().date())


if __name__ == "__main__":
    backfill_qqq()
    backfill_vix()
    log.info("Backfill complete.")
