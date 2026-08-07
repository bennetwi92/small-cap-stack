"""Tests for the trading-calendar gate (#137)."""

from __future__ import annotations

from datetime import date, time, timedelta

from small_cap_stack.market_calendar import early_close_et, is_trading_day, previous_session


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


# --- previous_session (#514) ------------------------------------------------------------

# Two copies of "the session before d" existed and only one asked the calendar. These pin the
# cases where a weekday-only walk gives the wrong answer.


def test_previous_session_on_a_plain_weekday() -> None:
    assert previous_session(date(2026, 7, 2)) == date(2026, 7, 1)


def test_previous_session_skips_the_weekend() -> None:
    # A weekend with no holiday attached — the case a weekday walk DID get right, and which
    # nothing else here covers. (2026-06-29 is a Monday; 2026-06-26 the Friday before.)
    assert previous_session(date(2026, 6, 29)) == date(2026, 6, 26)


def test_previous_session_skips_a_holiday() -> None:
    """The defect. Jul 4 2026 is a Saturday, so Independence Day was observed Friday the 3rd.

    Monday the 6th's prior session is **Thursday the 2nd**; a weekday-only walk stops on Friday
    the 3rd, a day the NYSE was shut."""
    assert previous_session(date(2026, 7, 6)) == date(2026, 7, 2)
    assert is_trading_day(date(2026, 7, 3)) is False  # what the old walk would have returned


def test_previous_session_skips_thanksgiving() -> None:
    """The holiday that isn't adjacent to a weekend, so nothing else masks the bug: Thursday
    2026-11-26 is closed, and Friday the 27th's prior session is Wednesday the 25th."""
    assert previous_session(date(2026, 11, 27)) == date(2026, 11, 25)


def test_previous_session_honours_a_manual_override() -> None:
    """An unscheduled closure patched via `calendar_closed_dates` must move the window too —
    that hook is the whole reason this asks the calendar rather than the weekday."""
    assert previous_session(date(2026, 7, 2), extra_closed=(date(2026, 7, 1),)) == date(2026, 6, 30)


def test_previous_session_gives_up_rather_than_guessing() -> None:
    """A fortnight of overrides returns None instead of an arbitrary date. Callers decide what
    that means; silently returning a closed day is what this function exists to stop."""
    d = date(2026, 7, 20)
    # Exactly at the bound: 13 closed days still finds the 14th, 14 gives up. Asserting the
    # boundary rather than "somewhere past it" means silently widening the lookback fails here.
    closed = lambda n: tuple(d - timedelta(days=k) for k in range(1, n))  # noqa: E731
    assert previous_session(d, extra_closed=closed(14)) is not None
    assert previous_session(d, extra_closed=closed(15)) is None
