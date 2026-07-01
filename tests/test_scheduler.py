"""Tests for the scheduler wiring (#89)."""

from __future__ import annotations

import asyncio

from small_cap_stack.config import Settings
from small_cap_stack.scheduler import build_scheduler


async def _noop() -> None: ...


def _job_grace() -> dict[str, int | None]:
    async def collect() -> dict[str, int | None]:
        sch = build_scheduler(
            Settings(_env_file=None),  # type: ignore[call-arg]
            on_tick=_noop,
            on_scan_start=_noop,
            on_scan_end=_noop,
            on_eod_bars=_noop,
            on_eod_report=_noop,
            on_eod_backfill=_noop,
        )
        sch.start(paused=True)  # applies pending jobs so misfire_grace_time is readable
        try:
            return {j.id: j.misfire_grace_time for j in sch.get_jobs()}
        finally:
            sch.shutdown(wait=False)

    return asyncio.run(collect())


def test_daily_jobs_have_generous_misfire_grace() -> None:
    grace = _job_grace()
    expected = Settings(_env_file=None).cron_misfire_grace_sec  # type: ignore[call-arg]
    assert expected >= 60  # a brief block shouldn't skip a once-a-day critical job
    for jid in ("scan_start", "scan_end", "eod_bars", "eod_report", "eod_backfill"):
        assert grace[jid] == expected
    assert grace["tick"] == 1  # interval tick keeps the tight default (a late tick is harmless)
