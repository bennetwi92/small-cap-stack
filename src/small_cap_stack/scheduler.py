"""Time-triggered jobs via APScheduler (AsyncIOScheduler), in US/Eastern."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import time

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from .clock import ET_NAME
from .config import Settings

Job = Callable[[], Awaitable[None]]


def build_scheduler(
    settings: Settings,
    *,
    on_tick: Job,
    on_scan_start: Job,
    on_scan_end: Job,
    on_eod_bars: Job,
    on_eod_report: Job,
    on_eod_backfill: Job,
) -> AsyncIOScheduler:
    """Build a scheduler with the periodic tick + daily boundary jobs (not yet started).

    The `tick` interval job drives the intraday discovery loop (it self-gates by time window);
    the cron jobs mark the window boundaries, batch-fetch the day's bars, then build the report.
    """
    scheduler = AsyncIOScheduler(timezone=ET_NAME)

    def cron(t: time) -> CronTrigger:
        return CronTrigger(hour=t.hour, minute=t.minute, timezone=ET_NAME)

    # Daily jobs get a generous misfire grace so a transient event-loop block doesn't skip the
    # day's bar batch / report (APScheduler's default is 1s). The interval tick keeps the tight
    # default — a late tick is harmless and coalesce=True prevents pile-up.
    grace = settings.cron_misfire_grace_sec
    scheduler.add_job(on_tick, IntervalTrigger(seconds=settings.tick_interval_sec), id="tick")
    scheduler.add_job(
        on_scan_start, cron(settings.scan_start), id="scan_start", misfire_grace_time=grace
    )
    scheduler.add_job(on_scan_end, cron(settings.scan_end), id="scan_end", misfire_grace_time=grace)
    scheduler.add_job(
        on_eod_bars, cron(settings.eod_bars_fetch), id="eod_bars", misfire_grace_time=grace
    )
    scheduler.add_job(
        on_eod_report, cron(settings.eod_report), id="eod_report", misfire_grace_time=grace
    )
    # Morning catch-up: back-fill bars for any recent day the EOD batch missed (#100).
    scheduler.add_job(
        on_eod_backfill, cron(settings.eod_backfill), id="eod_backfill", misfire_grace_time=grace
    )
    return scheduler
