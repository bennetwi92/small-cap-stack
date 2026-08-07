"""Shared test helpers (#523). Import as ``from tests.support import settings``.

A normal module rather than ``conftest.py`` — see the note at the top of ``tests/conftest.py``.
Fixtures go there; plain callables go here.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from small_cap_stack.capture import Bar
from small_cap_stack.config import Settings

#: The canonical bar-series anchor: 2026-06-29 14:00 UTC = **10:00 ET**, on a Monday session.
#:
#: Fifteen modules declared their own; the fourteen that meant *this* instant (eleven here, three
#: at 2026-07-01 for no recorded reason) now share it. `test_settings_wiring` keeps its own — see
#: the note there.
#:
#: What is actually load-bearing, measured by moving this constant and re-running:
#:
#: - **The clock time.** Out of the 04:00–11:59 window, 7 tests fail. This must stay in-window.
#: - **The date**, but only via `test_report._DAY`, which derives from it. Keep that derivation.
#:
#: What is *not*: being a trading day. The whole suite passes with this on a Saturday — nothing on
#: any tested path consults the calendar. Don't infer a guarantee here that isn't enforced.
T0 = datetime(2026, 6, 29, 14, 0, tzinfo=UTC)


def bar(i: int, o: float, h: float, low: float, c: float, vol: float = 1000.0) -> Bar:
    """The i-th 5-minute bar from :data:`T0`.

    Seven modules had this function byte-for-byte (two spelling the same default as ``1e3``). It is
    the vocabulary almost every engine test is written in, so a change to `Bar` used to mean a
    seven-file diff.
    """
    return Bar(start=T0 + timedelta(minutes=5 * i), open=o, high=h, low=low, close=c, volume=vol)


def settings(**overrides: Any) -> Settings:
    """A `Settings` that cannot see the developer's `.env` (#507).

    The one place the isolation argument is spelled. It was previously copy-pasted as
    `Settings(_env_file=None)  # type: ignore[call-arg]` at 33 sites and simply forgotten at six
    more — including `test_review_fixtures.py`, which holds the 25 signed-off golden engine cases.

    Pairs with `conftest.pytest_configure`, which strips the matching environment variables before
    collection: this blocks the file, that blocks `os.environ`, and only both together make a
    `Settings()` in a test mean the same thing on a laptop and in CI.
    """
    return Settings(_env_file=None, **overrides)  # type: ignore[call-arg]
