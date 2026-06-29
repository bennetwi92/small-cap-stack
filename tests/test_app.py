"""Tests for the application wiring (scheduler jobs + placeholder pipeline)."""

from __future__ import annotations

import asyncio

from small_cap_stack.app import Application
from small_cap_stack.config import Settings


def _settings() -> Settings:
    return Settings(_env_file=None)  # type: ignore[call-arg]


def test_scheduler_registers_window_jobs() -> None:
    app = Application(_settings())
    ids = {job.id for job in app.scheduler.get_jobs()}
    assert ids == {"scan_start", "scan_end", "eod_report"}


def test_placeholder_pipeline_runs_clean() -> None:
    app = Application(_settings())
    result = asyncio.run(app._run_pipeline())
    assert result.ok
    assert set(result.results) == {"scan", "gate", "capture"}
