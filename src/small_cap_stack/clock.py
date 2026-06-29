"""Time helpers. All scheduling/windows are in US/Eastern; storage is UTC elsewhere."""

from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


def now_et() -> datetime:
    """Current time in US/Eastern."""
    return datetime.now(ET)


def within_window(moment: datetime, start: time, end: time) -> bool:
    """True if ``moment``'s local time-of-day is in [start, end] (inclusive)."""
    t = moment.timetz().replace(tzinfo=None) if moment.tzinfo else moment.time()
    return start <= t <= end
