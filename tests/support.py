"""Shared test helpers (#523). Import as ``from tests.support import settings``.

A normal module rather than ``conftest.py`` — see the note at the top of ``tests/conftest.py``.
Fixtures go there; plain callables go here.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from small_cap_stack.capture import (
    Bar,
    Candidate,
    NewsItem,
    news_record,
    opportunity_record,
    scanner_hit_record,
)
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


# --- store rows, built by the SAME functions production uses (#523 slice 3) -------------------
#
# Every test seeded `opportunities` by hand, and every one of them omitted `currency` and
# `exchange`; several seeded `news` without `ts_utc`. Production always writes those fields
# (`capture.opportunity_record` / `news_record`), so the suite was exercising a store shape that
# never occurs — a narrower parquet schema than the one the readers actually meet.
#
# These are deliberately THIN: they build the argument objects and delegate. The point is not to
# save typing, it is that a new column added to a record function reaches the fixtures for free
# instead of silently only existing in production.
#
# ⚠️ Not a shared `seed_day(...)`. The three seeders those literals live in are not three copies of
# one thing — `test_dashboard` seeds deliberate duplicates and a rank flip, `test_report` a clean
# flag plus a control, `test_portfolio` a pre-market window — and their differences ARE what each
# asserts. One seeder serving all three needs enough flags that the call site stops being readable.
# The duplication worth removing was in the row builders, not the composition.


def opportunity_row(
    oid: str,
    symbol: str,
    *,
    trading_date: date,
    first_seen: datetime | None = None,
    con_id: int = 1,
    rank: int = 0,
    exchange: str = "NASDAQ",
    currency: str = "USD",
) -> dict[str, Any]:
    """One `opportunities` row, shaped exactly as `capture.on_scan_tick` writes it."""
    candidate = Candidate(
        symbol=symbol, con_id=con_id, exchange=exchange, currency=currency, rank=rank
    )
    return opportunity_record(candidate, oid, first_seen or T0, trading_date)


def scanner_hit_row(
    oid: str, symbol: str, *, ts: datetime | None = None, rank: int = 0, con_id: int = 1
) -> dict[str, Any]:
    """One `scanner_hits` row, as production writes it."""
    candidate = Candidate(
        symbol=symbol, con_id=con_id, exchange="NASDAQ", currency="USD", rank=rank
    )
    return scanner_hit_record(oid, candidate, ts or T0)


def news_row(
    oid: str,
    symbol: str,
    *,
    time: str = "2026-06-29 13:45:00.0",
    provider: str = "DJ-N",
    headline: str = "h",
    article_id: str = "a1",
) -> dict[str, Any]:
    """One `news` row. The default `time` is a real IBKR timestamp string, so `ts_utc` parses —
    the hand-written rows used `"t"`, which parses to None and made every recency test blind."""
    return news_record(
        oid,
        symbol,
        NewsItem(time=time, provider=provider, headline=headline, article_id=article_id),
    )
