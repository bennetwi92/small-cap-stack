"""The guards that keep a 45-night harvest from taking the tracker down with it (#431).

CLAUDE.md is unambiguous about this box: a CX23 is 2 vCPU / 4 GB, a **single-date** dashboard
backfill OOM-killed it on 2026-07-16 and took the CI runner offline for 5h37m (#264), and the
driver was a payload builder holding every collected day's bars at once (#273). A two-year harvest
is exactly that shape of job. So the harvest is defensive by construction, in three layers:

1. **Streaming** — one session, one symbol at a time. That lives in :mod:`.runner`; it is what
   makes peak memory independent of how many sessions are harvested.
2. **A window the job refuses to run outside** (:class:`RunWindow`, chosen by :func:`window_for`).
   The box's day is booked: ``eod_backfill`` 03:45 ET, the scan window 04:00–11:59 ET,
   ``eod_bars_fetch`` 16:20, ``eod_report`` 16:30. The harvest gets 12:30 → 03:00 ET and checkpoints
   itself out well clear of 03:45. The window spans the two EOD jobs;
   :func:`~.runner.effective_deadline` — not this guard — is what keeps the harvest out of them
   (#455), for the reason spelled out in point 3.
   Being *launched* at the right time is not the same as *refusing* to run at the wrong one:
   a systemd timer that fires late, a manual re-run, or a job that overruns its night all end up
   inside the scan window, and only the second kind of check catches those.
   On a day the market is **shut** the booking is almost empty — the scan and both EOD jobs are
   gated on the same calendar the tracker uses — so the window opens at 05:00 instead (#633).
3. **Host headroom checked before every session** (:class:`HostGuard`). This is the in-process
   half; the kernel-enforced half is ``MemoryMax=1G`` on the systemd scope (see
   ``deploy/scs-harvest.service``). Both are needed and neither substitutes: the guard stops the
   job *cleanly at a checkpoint* when the box is already tight, the cgroup cap kills it *before it
   can take the host down* if a regression makes it grow anyway. A promise is not a limit.

Nothing here pings the tracker's Healthchecks dead-man's switch: a stalled harvest must never page
as a tracker outage (#431).
"""

from __future__ import annotations

import resource
import shutil
import sys
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from ..clock import ET
from ..config import Settings
from ..market_calendar import is_trading_day
from ..monitoring import mem_available_mb


def peak_rss_mb() -> float:
    """This process's peak resident set, in MB — the early-warning signal for a leak.

    ``ru_maxrss`` is kilobytes on Linux and bytes on macOS; normalising here means the logged
    number means the same thing on the box and on a dev Mac.
    """
    raw = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    return raw / (1024.0 * 1024.0) if sys.platform == "darwin" else raw / 1024.0


@dataclass(frozen=True)
class RunWindow:
    """The nightly window the harvest may run in, as ET wall-clock times.

    Wraps midnight by design (17:00 → 03:00), which is the whole reason this is a type rather than
    a comparison: ``start <= t < stop`` is simply false for every instant of a wrapping window, and
    getting that backwards means the guard silently permits exactly the hours it exists to forbid.
    """

    # Defaults MIRROR `Settings.harvest_start_et`/`harvest_stop_et`, which are the source of truth
    # (CLAUDE.md). They exist only so tests can say `RunWindow()`; production always passes the
    # settings through `__main__._window`. `test_run_window_default_mirrors_settings` fails if they
    # drift — before #455 they had, and ~11 runner tests were exercising a window that no longer
    # existed anywhere.
    start: time = time(12, 30)
    stop: time = time(3, 0)

    @property
    def wraps(self) -> bool:
        return self.stop <= self.start

    def is_open(self, now: datetime) -> bool:
        """Is ``now`` (any tz — converted to ET) inside the window?"""
        t = now.astimezone(ET).time()
        return (t >= self.start or t < self.stop) if self.wraps else (self.start <= t < self.stop)

    def deadline(self, now: datetime) -> datetime:
        """The instant this run must be checkpointed and stopped by — the next ``stop`` in ET.

        Computed from ET wall clock rather than by adding a fixed duration, so the hard stop stays
        pinned to 03:00 across a DST change instead of drifting an hour into ``eod_backfill``.
        """
        et = now.astimezone(ET)
        candidate = datetime.combine(et.date(), self.stop, tzinfo=ET)
        if candidate <= et:
            candidate = datetime.combine(et.date() + timedelta(days=1), self.stop, tzinfo=ET)
        return candidate

    def length_hours(self) -> float:
        """How long the window is, in hours — correct across the midnight wrap."""
        span = (
            datetime.combine(date.min, self.stop) - datetime.combine(date.min, self.start)
        ).total_seconds() / 3600.0
        return span + 24.0 if self.wraps else span

    def describe(self) -> str:
        return f"{self.start.strftime('%H:%M')}–{self.stop.strftime('%H:%M')} ET"


def market_closed(s: Settings, day: date) -> bool:
    """Is ``day`` a weekend, a holiday, or an ad-hoc closure? The tracker's own gate (#137)."""
    return not is_trading_day(day, extra_closed=s.calendar_closed_dates)


def window_for(s: Settings, day: date) -> RunWindow:
    """The window a run STARTING on ``day`` gets — the near-whole day when the market is shut.

    Keyed on the start date rather than evaluated continuously, because a run is one process with
    one deadline: a Saturday 05:00 run legitimately continues to Sunday 03:00, and re-deciding the
    window at midnight would either cut it short or extend a Friday-night run into Saturday's.

    The stop is the same 03:00 every day (``portfolio_refresh`` 03:15 and ``eod_backfill`` 03:45 do
    not take weekends off); only the opening moves. See ``Settings.harvest_weekend_start_et``.
    """
    start = s.harvest_weekend_start_et if market_closed(s, day) else s.harvest_start_et
    return RunWindow(start=start, stop=s.harvest_stop_et)


@dataclass(frozen=True)
class HostHeadroom:
    """A point-in-time read of the box's headroom, and whether it clears the floors."""

    mem_available_mb: float | None
    disk_free_mb: float | None
    ok: bool
    reason: str  # "" when ok


@dataclass(frozen=True)
class HostGuard:
    """Memory + disk floors, checked before each session starts.

    Checked *between* sessions rather than continuously: a session is the unit that can be
    abandoned cleanly (its partition is written once, at the end), so stopping anywhere else would
    only throw away work already paid for in API calls. An unreadable metric (a dev Mac has no
    ``/proc/meminfo``) is treated as "no signal", not as a failure — the kernel cap is what makes
    that safe.
    """

    min_mem_available_mb: float
    min_disk_free_mb: float

    def check(self, path: str) -> HostHeadroom:
        mem = mem_available_mb()
        try:
            free_mb: float | None = shutil.disk_usage(path).free / (1024.0 * 1024.0)
        except OSError:
            free_mb = None
        if mem is not None and mem < self.min_mem_available_mb:
            reason = f"memory {mem:.0f}MB available < {self.min_mem_available_mb:.0f}MB"
            return HostHeadroom(mem, free_mb, False, reason)
        if free_mb is not None and free_mb < self.min_disk_free_mb:
            return HostHeadroom(
                mem, free_mb, False, f"disk {free_mb:.0f}MB free < {self.min_disk_free_mb:.0f}MB"
            )
        return HostHeadroom(mem, free_mb, True, "")
