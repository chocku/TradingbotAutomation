"""Wrap strategy.py and expose StrategySignal."""
import sys
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import pandas as pd

# strategy.py lives one level up from qqq_trader/
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from strategy import compute_signals, get_position

log = logging.getLogger(__name__)


@dataclass
class StrategySignal:
    ticker:         str    # "QQQ" or "TQQQ"
    allocation_pct: float  # 0.0 = flat, 1.0 = 100% of portfolio
    direction:      str    # "long" or "flat"
    signal_ts:      datetime
    detail:         dict   # raw position dict from strategy for logging


def run_strategy(df: pd.DataFrame) -> StrategySignal:
    """
    Run the strategy on the prepared DataFrame and return a StrategySignal.
    df must already contain required columns (date, c, sma_20, sma_200, rsi_14, vix_close).
    """
    df_signals = compute_signals(df)
    pos        = get_position(df_signals.iloc[-1])

    asset          = pos["asset"]   # "QQQ" | "TQQQ" | "cash"
    allocation_pct = pos["size"]    # 0.0 – 1.0

    if asset == "cash":
        ticker    = "QQQ"
        direction = "flat"
        allocation_pct = 0.0
    else:
        ticker    = asset           # "QQQ" or "TQQQ"
        direction = "long"

    signal = StrategySignal(
        ticker=ticker,
        allocation_pct=allocation_pct,
        direction=direction,
        signal_ts=datetime.utcnow(),
        detail=pos,
    )

    log.info(
        "Strategy signal: ticker=%s allocation=%.0f%% direction=%s mr_score=%d in_bull=%s",
        signal.ticker, signal.allocation_pct * 100, signal.direction,
        pos["mr_score"], pos["in_bull"],
    )
    return signal


def describe_signal_detail(detail: dict) -> str:
    """One-line human-readable summary of why the strategy chose this signal."""
    regime = "BULL" if detail.get("in_bull") else "BEAR"
    c = detail.get("components", {}) or {}
    v = detail.get("values", {}) or {}

    active = [
        name for name, val in [
            ("pullback", c.get("pullback")),
            ("oversold", c.get("oversold")),
            ("two-day drop", c.get("two_down")),
            ("VIX fear", c.get("vix_fear")),
            ("below lower band", c.get("bb_below")),
        ] if val
    ]
    triggers = ", ".join(active) if active else "no dip triggers active"

    stats = []
    if v.get("rsi_14") is not None:
        stats.append(f"RSI {v['rsi_14']:.1f}")
    if v.get("vix") is not None:
        stats.append(f"VIX {v['vix']:.1f}")
    stats_str = f" ({', '.join(stats)})" if stats else ""

    return f"{regime} regime, {triggers}{stats_str}"
