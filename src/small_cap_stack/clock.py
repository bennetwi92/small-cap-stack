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
    """True if ``moment``'s local time-of-day is in the window, inclusive of the whole end MINUTE.

    The start bound is exact (the window opens at ``start``); the end bound is minute-granular, so
    ``end=11:59`` admits 11:59:00 through 11:59:59 — the strategy window runs 04:00 *through* 11:59
    ET. The old exact-second bound dropped a tick at e.g. 11:59:30 (ticks aren't minute-aligned),
    silently killing the last minute of the window (#163-C5)."""
    t = moment.timetz().replace(tzinfo=None) if moment.tzinfo else moment.time()
    return start <= t and (t.hour, t.minute) <= (end.hour, end.minute)
