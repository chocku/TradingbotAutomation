"""Submit MOC orders to Alpaca paper account."""
import logging
import time
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest, GetOrdersRequest
from alpaca.trading.enums import OrderSide, TimeInForce, QueryOrderStatus, OrderStatus

from config import MIN_SHARE_THRESHOLD

log = logging.getLogger(__name__)

_TERMINAL_STATUSES = (OrderStatus.FILLED, OrderStatus.CANCELED, OrderStatus.EXPIRED, OrderStatus.REJECTED)


def _poll_order_status(order_id: str, trading_client: TradingClient, timeout: int = 10) -> str:
    """Best-effort check of whether the order filled shortly after submission."""
    deadline = time.time() + timeout
    status = "submitted"
    while time.time() < deadline:
        try:
            order = trading_client.get_order_by_id(order_id)
            status = order.status.value
            if order.status in _TERMINAL_STATUSES:
                break
        except Exception as e:
            log.warning("Could not poll status for order %s: %s", order_id, e)
            break
        time.sleep(1)
    return status


def cancel_open_orders(ticker: str, trading_client: TradingClient) -> int:
    """
    Cancel all open orders for ticker.
    Returns the number of orders cancelled.
    Shares held for a pending order block new submissions — always call this before submit_order.
    """
    try:
        open_orders = trading_client.get_orders(
            GetOrdersRequest(status=QueryOrderStatus.OPEN, symbols=[ticker])
        )
        cancelled = 0
        for order in open_orders:
            try:
                trading_client.cancel_order_by_id(order.id)
                log.info("Cancelled open order: ticker=%s order_id=%s side=%s qty=%s",
                         ticker, order.id, order.side, order.qty)
                cancelled += 1
            except Exception as e:
                log.warning("Could not cancel order %s: %s", order.id, e)
        if cancelled:
            log.info("Cancelled %d open order(s) for %s before submitting new order", cancelled, ticker)
        return cancelled
    except Exception as e:
        log.warning("Could not fetch open orders for %s: %s", ticker, e)
        return 0


def submit_order(ticker: str, delta: int, trading_client: TradingClient) -> tuple[str | None, str]:
    """
    Cancel any open orders for ticker, then submit a DAY order for abs(delta) shares.
    Returns (order_id, status). status is "not_submitted" if delta is below threshold,
    otherwise the order's status after a short best-effort poll (e.g. "filled", "accepted").
    Raises on API rejection (caller handles logging).
    """
    if abs(delta) < MIN_SHARE_THRESHOLD:
        log.info("Delta %d below threshold — no order submitted", delta)
        return None, "not_submitted"

    # Cancel any pending orders first so held shares don't block the new submission
    cancel_open_orders(ticker, trading_client)

    side = OrderSide.BUY if delta > 0 else OrderSide.SELL
    req  = MarketOrderRequest(
        symbol=ticker,
        qty=abs(delta),
        side=side,
        time_in_force=TimeInForce.DAY,
    )

    order  = trading_client.submit_order(req)
    log.info(
        "Order submitted: ticker=%s side=%s qty=%d order_id=%s",
        ticker, side.value, abs(delta), order.id,
    )
    status = _poll_order_status(str(order.id), trading_client)
    log.info("Order %s status after short poll: %s", order.id, status)
    return str(order.id), status
