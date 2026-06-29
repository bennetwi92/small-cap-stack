"""Time-triggered jobs via APScheduler (AsyncIOScheduler), in US/Eastern."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import time

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from .config import Settings

Job = Callable[[], Awaitable[None]]


def build_scheduler(
    settings: Settings,
    *,
    on_tick: Job,
    on_scan_start: Job,
    on_scan_end: Job,
    on_eod_report: Job,
) -> AsyncIOScheduler:
    """Build a scheduler with the periodic tick + daily boundary jobs (not yet started).

    The `tick` interval job drives the real scan/capture loop (it self-gates by time window);
    the cron jobs just mark the window boundaries.
    """
    scheduler = AsyncIOScheduler(timezone=settings.timezone)

    def cron(t: time) -> CronTrigger:
        return CronTrigger(hour=t.hour, minute=t.minute, timezone=settings.timezone)

    scheduler.add_job(on_tick, IntervalTrigger(seconds=settings.tick_interval_sec), id="tick")
    scheduler.add_job(on_scan_start, cron(settings.scan_start), id="scan_start")
    scheduler.add_job(on_scan_end, cron(settings.scan_end), id="scan_end")
    scheduler.add_job(on_eod_report, cron(settings.eod_report), id="eod_report")
    return scheduler
