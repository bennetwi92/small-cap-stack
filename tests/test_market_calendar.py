"""Tests for the trading-calendar gate (#137)."""

from __future__ import annotations

from datetime import date, time

from small_cap_stack.market_calendar import early_close_et, is_trading_day


def test_normal_trading_day() -> None:
    assert is_trading_day(date(2026, 7, 2)) is True  # a plain Thursday


def test_2026_07_03_holiday() -> None:
    # The incident day: Jul 4 2026 falls on a Saturday, so Independence Day was observed
    # Friday the 3rd — NYSE closed, yet the app ran a full "session" (#137).
    assert is_trading_day(date(2026, 7, 3)) is False


def test_weekend() -> None:
    assert is_trading_day(date(2026, 7, 4)) is False  # Saturday
    assert is_trading_day(date(2026, 7, 5)) is False  # Sunday


def test_manual_override_closes_a_session_day() -> None:
    d = date(2026, 7, 2)
    assert is_trading_day(d, extra_closed=(d,)) is False
    assert is_trading_day(d, extra_closed=(date(2026, 7, 1),)) is True  # other dates don't leak


def test_early_close_day() -> None:
    # The day after Thanksgiving 2026 is a 13:00 ET half day.
    assert early_close_et(date(2026, 11, 27)) == time(13, 0)
    assert is_trading_day(date(2026, 11, 27)) is True  # a half day still trades


def test_early_close_none_on_full_day_and_non_trading_day() -> None:
    assert early_close_et(date(2026, 7, 2)) is None  # full 16:00 session
    assert early_close_et(date(2026, 7, 4)) is None  # Saturday
    assert early_close_et(date(2026, 7, 3)) is None  # holiday
    d = date(2026, 11, 27)
    assert early_close_et(d, extra_closed=(d,)) is None  # overridden closed
