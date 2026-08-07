"""Tests for the scheduler wiring (#89)."""

from __future__ import annotations

import asyncio

from small_cap_stack.scheduler import build_scheduler
from tests.support import settings


async def _noop() -> None: ...


def _job_grace() -> dict[str, int | None]:
    async def collect() -> dict[str, int | None]:
        sch = build_scheduler(
            settings(),
            on_tick=_noop,
            on_scan_start=_noop,
            on_scan_end=_noop,
            on_eod_bars=_noop,
            on_eod_report=_noop,
            on_eod_backfill=_noop,
            on_portfolio_refresh=_noop,
        )
        sch.start(paused=True)  # applies pending jobs so misfire_grace_time is readable
        try:
            return {j.id: j.misfire_grace_time for j in sch.get_jobs()}
        finally:
            sch.shutdown(wait=False)

    return asyncio.run(collect())


def test_daily_jobs_have_generous_misfire_grace() -> None:
    grace = _job_grace()
    expected = settings().cron_misfire_grace_sec
    assert expected >= 60  # a brief block shouldn't skip a once-a-day critical job
    for jid in ("scan_start", "scan_end", "eod_bars", "eod_report", "eod_backfill"):
        assert grace[jid] == expected
    assert grace["tick"] == 1  # interval tick keeps the tight default (a late tick is harmless)


def test_missed_job_listener_counts_and_is_registered() -> None:
    from apscheduler.events import EVENT_JOB_MISSED

    from small_cap_stack.monitoring import metric_value
    from small_cap_stack.scheduler import record_missed_job

    # The listener itself: a missed job (max_instances=1 skipping an over-budget tick) must leave
    # a countable trace (#321) — previously it was one unread APScheduler log line.
    before = metric_value("scs_jobs_missed_total")

    class _Event:
        job_id = "tick"

    record_missed_job(_Event())
    assert metric_value("scs_jobs_missed_total") == before + 1

    # And build_scheduler wires it to EVENT_JOB_MISSED.
    async def check() -> bool:
        sch = build_scheduler(
            settings(),
            on_tick=_noop,
            on_scan_start=_noop,
            on_scan_end=_noop,
            on_eod_bars=_noop,
            on_eod_report=_noop,
            on_eod_backfill=_noop,
            on_portfolio_refresh=_noop,
        )
        return any(
            cb is record_missed_job and mask & EVENT_JOB_MISSED for cb, mask in sch._listeners
        )

    assert asyncio.run(check()) is True


def test_the_morning_refresh_lands_in_the_one_free_slot_before_the_open() -> None:
    """The whole value of #458 is the slot, so it is asserted against the box's own schedule
    rather than pinned to a literal.

    The harvest hard-stops at `harvest_stop_et`, `eod_backfill` runs at 03:45 and the scan window
    opens at 04:00. The refresh has to sit strictly between the first two, or it is either racing
    a harvest still holding 1 GB or competing with the tracker's morning — and it must finish well
    before the open, since being visible *before* the market is the point.
    """
    s = settings()
    assert s.harvest_stop_et < s.portfolio_refresh_et < s.eod_backfill, (
        "the morning rebuild must run after the harvest stops and before eod_backfill"
    )
    assert s.portfolio_refresh_et < s.scan_start, "it must be done before the scan window opens"


def test_the_morning_refresh_is_scheduled_at_that_time() -> None:
    """A setting nothing reads is a comment. Pin the job to the configured hour."""
    from apscheduler.triggers.cron import CronTrigger

    s = settings()

    async def check() -> None:
        sch = build_scheduler(
            s,
            on_tick=_noop,
            on_scan_start=_noop,
            on_scan_end=_noop,
            on_eod_bars=_noop,
            on_eod_report=_noop,
            on_eod_backfill=_noop,
            on_portfolio_refresh=_noop,
        )
        sch.start(paused=True)  # applies pending jobs so the trigger is readable
        try:
            job = sch.get_job("portfolio_refresh")
            assert job is not None
            assert isinstance(job.trigger, CronTrigger)
            fields = {f.name: str(f) for f in job.trigger.fields}
            assert fields["hour"] == str(s.portfolio_refresh_et.hour)
            assert fields["minute"] == str(s.portfolio_refresh_et.minute)
            # A missed refresh costs a day of visibility, so it gets the same generous grace as the
            # other daily boundary jobs rather than APScheduler's 1-second default.
            assert job.misfire_grace_time == s.cron_misfire_grace_sec
        finally:
            sch.shutdown(wait=False)

    asyncio.run(check())
