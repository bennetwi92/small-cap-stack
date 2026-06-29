"""Tests for time-window helpers."""

from __future__ import annotations

from datetime import datetime, time

from small_cap_stack.clock import ET, within_window


def test_within_window_inclusive() -> None:
    start, end = time(4, 0), time(11, 59)
    assert within_window(datetime(2026, 6, 29, 4, 0, tzinfo=ET), start, end)
    assert within_window(datetime(2026, 6, 29, 8, 30, tzinfo=ET), start, end)
    assert within_window(datetime(2026, 6, 29, 11, 59, tzinfo=ET), start, end)


def test_outside_window() -> None:
    start, end = time(4, 0), time(11, 59)
    assert not within_window(datetime(2026, 6, 29, 3, 59, tzinfo=ET), start, end)
    assert not within_window(datetime(2026, 6, 29, 12, 0, tzinfo=ET), start, end)
