"""NYSE market calendar check."""
import logging
from datetime import date
import pandas_market_calendars as mcal

log = logging.getLogger(__name__)


def is_market_open_today() -> bool:
    nyse  = mcal.get_calendar("NYSE")
    today = date.today().isoformat()
    sched = nyse.schedule(start_date=today, end_date=today)
    open_ = not sched.empty
    log.info("Market open today (%s): %s", today, open_)
    return open_
