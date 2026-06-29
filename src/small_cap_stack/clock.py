"""Time helpers. All scheduling/windows are in US/Eastern; storage is UTC elsewhere."""

from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

# The market timezone is a domain constant (US small-cap), not configuration — this module is the
# single source of truth for it, used by the gates, the windows, and the scheduler alike.
ET_NAME = "America/New_York"
ET = ZoneInfo(ET_NAME)


def now_et() -> datetime:
    """Current time in US/Eastern."""
    return datetime.now(ET)


def within_window(moment: datetime, start: time, end: time) -> bool:
    """True if ``moment``'s local time-of-day is in [start, end] (inclusive)."""
    t = moment.timetz().replace(tzinfo=None) if moment.tzinfo else moment.time()
    return start <= t <= end
