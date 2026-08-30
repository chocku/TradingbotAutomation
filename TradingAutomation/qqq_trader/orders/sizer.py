"""Calculate target share count from equity, allocation, and price."""
import logging
from config import FRACTIONAL_SHARES

log = logging.getLogger(__name__)


def calculate_target_shares(equity: float, allocation_pct: float,
                             execution_price: float) -> int:
    raw = equity * allocation_pct / execution_price
    shares = int(raw) if not FRACTIONAL_SHARES else raw
    log.info(
        "Sizer: equity=%.2f alloc=%.0f%% price=%.2f → %s shares",
        equity, allocation_pct * 100, execution_price, shares,
    )
    return shares
