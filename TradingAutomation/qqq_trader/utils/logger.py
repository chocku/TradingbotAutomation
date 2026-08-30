"""Append-only JSONL trade log — supports both local and S3."""
import json
import logging
import os
from datetime import datetime

from config import LOG_PATH, USE_S3

log = logging.getLogger(__name__)

# S3 support (optional)
if USE_S3:
    try:
        from utils.s3_utils import append_log_to_s3
    except ImportError:
        append_log_to_s3 = None
else:
    append_log_to_s3 = None


def log_run(
    ticker: str,
    allocation_pct: float,
    direction: str,
    execution_price: float,
    portfolio_equity: float,
    target_shares: int,
    current_shares: float,
    delta: int,
    order_id: str | None,
    signal_detail: dict | None = None,
    error: str | None = None,
    position_before: dict | None = None,
) -> None:
    qqq_equiv = allocation_pct * 3 if ticker == "TQQQ" else allocation_pct
    entry = {
        "ts":                 datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ticker":             ticker,
        "allocation_pct":     allocation_pct,
        "direction":          direction,
        "execution_price":    round(execution_price, 4),
        "portfolio_equity":   round(portfolio_equity, 2),
        "position_before":    position_before,
        "target_shares":      target_shares,
        "current_shares":     current_shares,
        "delta":              delta,
        "order_id":           order_id,
        "qqq_equiv_exposure": round(qqq_equiv, 4),
        "signal":             signal_detail,
        "error":              error,
    }
    # Write to S3 if enabled
    if USE_S3 and append_log_to_s3:
        try:
            append_log_to_s3(entry)
        except Exception as e:
            log.error("Failed to write to S3: %s — falling back to local", e)
            # Fall back to local if S3 fails
            os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
            with open(LOG_PATH, "a") as f:
                f.write(json.dumps(entry) + "\n")
    else:
        # Write locally
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "a") as f:
            f.write(json.dumps(entry) + "\n")

    # Human-readable strategy summary in the scheduler log
    if signal_detail:
        s = signal_detail
        log.info(
            "Strategy: %s | regime=%s | mr_score=%d/5 | overbought=%s",
            "BULL" if s.get("in_bull") else "BEAR",
            "BULL" if s.get("in_bull") else "BEAR",
            s.get("mr_score", 0),
            s.get("overbought"),
        )
        c = s.get("components", {})
        v = s.get("values", {})
        log.info(
            "  pullback=%s (close=%.2f vs SMA20=%.2f)  oversold=%s (RSI=%.1f)  "
            "two_down=%s  vix_fear=%s (VIX=%.2f)  bb_below=%s",
            c.get("pullback"), v.get("close"), v.get("sma_20"),
            c.get("oversold"), v.get("rsi_14"),
            c.get("two_down"),
            c.get("vix_fear"), v.get("vix"),
            c.get("bb_below"),
        )
        log.info(
            "  → %s %s%% (equity=%.2f  price=%.2f  target=%d  current=%.0f  delta=%+d)",
            ticker, int(allocation_pct * 100),
            portfolio_equity, execution_price,
            target_shares, current_shares, delta,
        )
    log.info("order_id=%s  error=%s", order_id, error)
