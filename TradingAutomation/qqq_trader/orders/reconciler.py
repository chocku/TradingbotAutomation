"""Determine current position and compute delta; handle instrument switches."""
import logging
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce

from config import MIN_SHARE_THRESHOLD

log = logging.getLogger(__name__)


def get_current_shares(ticker: str, trading_client: TradingClient) -> float:
    """
    Return current position size in shares.
    Returns 0.0 only for a genuine 'no position' (404).
    Re-raises on any other error so the pipeline halts rather than assuming flat.
    """
    try:
        pos = trading_client.get_open_position(ticker)
        return float(pos.qty)
    except Exception as e:
        msg = str(e).lower()
        if "position does not exist" in msg or "404" in msg or "no position" in msg:
            return 0.0
        raise


def get_portfolio_equity(trading_client: TradingClient) -> float:
    account = trading_client.get_account()
    return float(account.equity)


def get_current_ticker(trading_client: TradingClient) -> str | None:
    """Return the ticker of any open position (QQQ or TQQQ), or None if flat."""
    for ticker in ("TQQQ", "QQQ"):
        try:
            pos = trading_client.get_open_position(ticker)
            if float(pos.qty) != 0:
                return ticker
        except Exception as e:
            msg = str(e).lower()
            if "position does not exist" in msg or "404" in msg or "no position" in msg:
                continue
            raise
    return None


def close_position(ticker: str, trading_client: TradingClient) -> str | None:
    """
    Cancel any open orders for ticker, then submit a DAY sell for the full position.
    Returns order_id on success, None if no position. Raises on API failure.
    """
    from orders.executor import cancel_open_orders
    cancel_open_orders(ticker, trading_client)

    shares = get_current_shares(ticker, trading_client)
    if shares <= 0:
        return None
    req = MarketOrderRequest(
        symbol=ticker,
        qty=round(shares),
        side=OrderSide.SELL,
        time_in_force=TimeInForce.DAY,
    )
    order = trading_client.submit_order(req)
    log.info("Close order submitted: ticker=%s qty=%d order_id=%s", ticker, int(shares), order.id)
    return str(order.id)


def compute_delta(target_ticker: str, target_shares: int,
                  trading_client: TradingClient) -> tuple[int, str | None]:
    """
    Return (delta, switch_from_ticker).
    switch_from_ticker is non-None when we must close a position in a different ticker first.
    """
    current_ticker = get_current_ticker(trading_client)

    if current_ticker is not None and current_ticker != target_ticker:
        # Instrument switch needed
        current_shares = get_current_shares(current_ticker, trading_client)
        log.info(
            "Instrument switch: %s (%.0f shares) → %s (%d target shares)",
            current_ticker, current_shares, target_ticker, target_shares,
        )
        return target_shares, current_ticker

    current_shares = get_current_shares(target_ticker, trading_client)
    delta          = target_shares - int(current_shares)
    log.info(
        "Delta: target=%d current=%.0f delta=%d (ticker=%s)",
        target_shares, current_shares, delta, target_ticker,
    )
    return delta, None
