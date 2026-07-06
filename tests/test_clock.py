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


def test_end_bound_includes_the_whole_end_minute() -> None:
    # Ticks aren't minute-aligned: a tick anywhere in the 11:59 minute must still count. The old
    # exact-second bound dropped 11:59:00.001–11:59:59, killing the window's last minute (#163-C5).
    start, end = time(4, 0), time(11, 59)
    assert within_window(datetime(2026, 6, 29, 11, 59, 30, tzinfo=ET), start, end)
    assert within_window(datetime(2026, 6, 29, 11, 59, 59, tzinfo=ET), start, end)
    assert not within_window(datetime(2026, 6, 29, 12, 0, 0, tzinfo=ET), start, end)
    # The start bound stays exact (opens at 04:00 sharp).
    assert within_window(datetime(2026, 6, 29, 4, 0, 0, tzinfo=ET), start, end)
    assert not within_window(datetime(2026, 6, 29, 3, 59, 59, tzinfo=ET), start, end)
