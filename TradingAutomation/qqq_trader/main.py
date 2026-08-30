"""
QQQ EOD Position Manager
Runs at 3:55 PM ET on NYSE trading days.
"""
import logging
import os
import sys
import time
from pathlib import Path

from alpaca.trading.enums import OrderStatus

from dotenv import load_dotenv
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

load_dotenv(Path(__file__).parent / ".env")

# Configure logging before any imports that use it
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("main")

# Add qqq_trader parent to path so strategy.py is importable
sys.path.insert(0, str(Path(__file__).parent.parent))

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.trading.client import TradingClient

from config import COMPUTE_TIME_ET, TIMEZONE, SELL_FILL_TIMEOUT_S
from data.fetcher import build_strategy_input, fetch_execution_price
from signal_engine.engine import run_strategy
from orders.sizer import calculate_target_shares
from orders.reconciler import (
    get_portfolio_equity, compute_delta, close_position, get_current_shares,
)
from orders.executor import submit_order
from utils.calendar import is_market_open_today
from utils.logger import log_run
from utils.notifier import notify_started, notify_completed, notify_error


def _poll_fill(order_id: str, trading_client, timeout: int) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        order = trading_client.get_order_by_id(order_id)
        if order.status == OrderStatus.FILLED:
            log.info("Sell order %s filled", order_id)
            return True
        if order.status in (OrderStatus.CANCELED, OrderStatus.EXPIRED):
            log.error("Sell order %s ended with status %s", order_id, order.status)
            return False
        time.sleep(2)
    log.warning("Sell order %s not filled within %ds — proceeding anyway", order_id, timeout)
    return False


def _make_clients():
    key    = os.environ["ALPACA_KEY"]
    secret = os.environ["ALPACA_SECRET"]
    trading = TradingClient(key, secret, paper=True)
    data    = StockHistoricalDataClient(key, secret)
    return trading, data


def run_pipeline(dry_run: bool = False, force: bool = False) -> None:
    log.info("=== Pipeline triggered%s ===", " [DRY RUN]" if dry_run else "")

    if not dry_run and not force and not is_market_open_today():
        log.info("Not a market day — exiting.")
        return

    try:
        trading_client, data_client = _make_clients()
    except Exception as e:
        log.critical("Failed to create Alpaca clients: %s", e)
        return

    # ── 1. Build strategy input ──────────────────────────────────────────────
    try:
        qqq_df = build_strategy_input(data_client)
    except Exception as e:
        log.critical("Data fetch failed — halting: %s", e)
        log_run("N/A", 0.0, "flat", 0.0, 0.0, 0, 0.0, 0, None, error=str(e))
        notify_error("data fetch", str(e))
        return

    # ── 2. Run strategy ──────────────────────────────────────────────────────
    try:
        signal = run_strategy(qqq_df)
    except Exception as e:
        log.critical("Strategy exception — halting (never default to a position): %s", e)
        log_run("N/A", 0.0, "flat", 0.0, 0.0, 0, 0.0, 0, None, error=str(e))
        notify_error("strategy", str(e))
        return

    # ── 3. Portfolio equity ──────────────────────────────────────────────────
    try:
        equity = get_portfolio_equity(trading_client)
    except Exception as e:
        log.critical("Failed to fetch account equity — halting: %s", e)
        log_run(signal.ticker, signal.allocation_pct, signal.direction,
                0.0, 0.0, 0, 0.0, 0, None, error=str(e))
        notify_error("portfolio equity fetch", str(e))
        return

    # ── 4. Execution price ───────────────────────────────────────────────────
    try:
        exec_price = fetch_execution_price(signal.ticker, data_client, qqq_df)
    except Exception as e:
        log.critical("Failed to fetch execution price — halting: %s", e)
        log_run(signal.ticker, signal.allocation_pct, signal.direction,
                0.0, equity, 0, 0.0, 0, None, error=str(e))
        notify_error("execution price fetch", str(e))
        return

    # ── Notify started ───────────────────────────────────────────────────────
    notify_started(signal.ticker, f"{int(signal.allocation_pct * 100)}%", signal.detail.get("mr_score", 0), equity)

    # ── 5. Size position ─────────────────────────────────────────────────────
    target_shares = calculate_target_shares(equity, signal.allocation_pct, exec_price)

    # ── 6. Instrument switch + delta ─────────────────────────────────────────
    delta, switch_from = compute_delta(signal.ticker, target_shares, trading_client)
    current_shares     = get_current_shares(signal.ticker, trading_client)

    # ── 6a. Skip rebalance when fully invested and staying in same ticker ────
    # At 100% allocation with no instrument switch, the portfolio IS the shares.
    # Rebalancing only creates unnecessary cash drag from rounding differences.
    if signal.allocation_pct == 1.0 and switch_from is None and current_shares > 0 and delta != 0:
        log.info(
            "Skipping rebalance at 100%% allocation — holding %d shares (delta %+d ignored)",
            int(current_shares), delta,
        )
        delta         = 0
        target_shares = int(current_shares)

    # Snapshot what was held before this trade fires
    if switch_from is not None:
        held_shares     = get_current_shares(switch_from, trading_client)
        position_before = {"ticker": switch_from, "shares": int(held_shares)}
    elif current_shares > 0:
        position_before = {"ticker": signal.ticker, "shares": int(current_shares)}
    else:
        position_before = None  # flat going in

    if switch_from is not None:
        log.info("Closing existing position in %s before opening %s", switch_from, signal.ticker)
        sell_id = None
        try:
            sell_id = close_position(switch_from, trading_client)
        except Exception as e:
            msg = f"Failed to close {switch_from} — aborting buy side: {e}"
            log.critical(msg)
            log_run(signal.ticker, signal.allocation_pct, signal.direction,
                    exec_price, equity, target_shares, current_shares, delta, None,
                    error=msg, position_before=position_before)
            return
        if sell_id:
            _poll_fill(sell_id, trading_client, SELL_FILL_TIMEOUT_S)
        # After close, current_shares in target ticker is 0
        current_shares = 0.0
        delta          = target_shares

    # ── 8. Execute buy ───────────────────────────────────────────────────────
    order_id = None
    error    = None
    if dry_run:
        log.info(
            "[DRY RUN] Would submit: ticker=%s delta=%+d side=%s — no order sent",
            signal.ticker, delta, "BUY" if delta > 0 else "SELL",
        )
    else:
        try:
            order_id = submit_order(signal.ticker, delta, trading_client)
        except Exception as e:
            error = str(e)
            log.error("Order rejected — not retrying: %s", e)
            notify_error("order submission", str(e))

    # ── 9. Log ───────────────────────────────────────────────────────────────
    log_run(
        ticker=signal.ticker,
        allocation_pct=signal.allocation_pct,
        direction=signal.direction,
        execution_price=exec_price,
        portfolio_equity=equity,
        target_shares=target_shares,
        current_shares=current_shares,
        delta=delta,
        order_id=order_id,
        signal_detail=signal.detail,
        error=error,
        position_before=position_before,
    )
    notify_completed(
        ticker=signal.ticker,
        alloc=f"{int(signal.allocation_pct * 100)}%",
        delta=delta,
        order_id=order_id,
        exec_price=exec_price,
        equity=equity,
        current_shares=current_shares,
        target_shares=target_shares,
    )
    log.info("=== Pipeline complete ===")
    try:
        from dashboard import build
        build()
    except Exception as e:
        log.warning("Dashboard generation failed: %s", e)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="QQQ EOD Position Manager")
    parser.add_argument("--now", action="store_true",
                        help="Run pipeline immediately (skip scheduler)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Run full pipeline but skip order submission and calendar check")
    parser.add_argument("--force", action="store_true",
                        help="Bypass calendar check and submit real orders (testing)")
    args = parser.parse_args()

    if args.dry_run:
        run_pipeline(dry_run=True)
    elif args.force:
        run_pipeline(force=True)
    elif args.now:
        run_pipeline()
    else:
        hour, minute = COMPUTE_TIME_ET.split(":")
        scheduler    = BlockingScheduler(timezone=TIMEZONE)
        scheduler.add_job(
            run_pipeline,
            trigger=CronTrigger(hour=int(hour), minute=int(minute), timezone=TIMEZONE),
            id="qqq_eod",
            name="QQQ EOD Position Manager",
        )
        log.info(
            "Scheduler started — will run at %s ET on market days", COMPUTE_TIME_ET
        )
        try:
            scheduler.start()
        except (KeyboardInterrupt, SystemExit):
            log.info("Scheduler stopped.")
