"""Time-triggered jobs via APScheduler (AsyncIOScheduler), in US/Eastern."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import time

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from .config import Settings

Job = Callable[[], Awaitable[None]]


def build_scheduler(
    settings: Settings,
    *,
    on_scan_start: Job,
    on_scan_end: Job,
    on_eod_report: Job,
) -> AsyncIOScheduler:
    """Build a scheduler with the daily trading-window jobs registered (not yet started)."""
    scheduler = AsyncIOScheduler(timezone=settings.timezone)

    def cron(t: time) -> CronTrigger:
        return CronTrigger(hour=t.hour, minute=t.minute, timezone=settings.timezone)

    scheduler.add_job(on_scan_start, cron(settings.scan_start), id="scan_start")
    scheduler.add_job(on_scan_end, cron(settings.scan_end), id="scan_end")
    scheduler.add_job(on_eod_report, cron(settings.eod_report), id="eod_report")
    return scheduler
