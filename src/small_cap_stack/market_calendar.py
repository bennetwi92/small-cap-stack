"""US trading-calendar gate (#137): pure predicates over the NYSE (XNYS) session calendar.

``exchange_calendars`` is the calendar of record — offline and deterministic (sessions are
published years ahead), so the same predicate answers identically at runtime, in tests, and in
compute-on-read replay. It knows weekends, full holidays, ad-hoc historical closures, and
**early closes** (e.g. the 13:00 ET day after Thanksgiving). Kept current by automated
dependency updates (Dependabot); an *unscheduled* closure can be patched immediately via the
``calendar_closed_dates`` settings override without waiting for a library release. Rationale
recorded in ``research/decisions.md`` (2026-07-17).

The calendar object covers ~20 years back through ~1 year ahead — ample for the runtime gate
and replay over collected data. Querying beyond that raises, which is preferable to silently
guessing.
"""

from __future__ import annotations

from collections.abc import Collection
from datetime import date, time
from functools import lru_cache

import exchange_calendars as xcals

from .clock import ET_NAME

_FULL_CLOSE = time(16, 0)  # a session closing earlier than this is an early-close (half) day


@lru_cache(maxsize=1)
def _xnys() -> xcals.ExchangeCalendar:
    """The XNYS calendar, built once per process (~0.4s to construct)."""
    return xcals.get_calendar("XNYS")


def is_trading_day(d: date, *, extra_closed: Collection[date] = ()) -> bool:
    """True if XNYS holds a session on ``d`` and it isn't overridden closed.

    ``extra_closed`` is the manual-override hook (wired from ``Settings.calendar_closed_dates``)
    for an unscheduled closure the library can't know yet."""
    if d in extra_closed:
        return False
    return bool(_xnys().is_session(d.isoformat()))


def early_close_et(d: date, *, extra_closed: Collection[date] = ()) -> time | None:
    """The ET close time if ``d`` is a shortened session, else None (incl. non-trading days).

    The scan window (04:00–11:59 ET) is pre-market, so an early close never clips it; the EOD
    crons (16:20+) also remain valid — a 13:00 close only means the day's bars are complete
    sooner. This lookup exists so any consumer that *does* care about the close (reports,
    future order logic) asks the calendar instead of assuming 16:00."""
    if not is_trading_day(d, extra_closed=extra_closed):
        return None
    close: time = _xnys().session_close(d.isoformat()).tz_convert(ET_NAME).time()
    return close if close < _FULL_CLOSE else None
