"""Shared test helpers (#523). Import as ``from tests.support import settings``.

A normal module rather than ``conftest.py`` — see the note at the top of ``tests/conftest.py``.
Fixtures go there; plain callables go here.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from small_cap_stack.capture import (
    Bar,
    Candidate,
    NewsItem,
    news_record,
    opportunity_record,
    scanner_hit_record,
)
from small_cap_stack.config import Settings
from small_cap_stack.portfolio import CandidateTrade
from small_cap_stack.storage import Store

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


# --- portfolio-test helpers, shared by the four `test_portfolio_*.py` modules (#529) ------

ET = ZoneInfo("America/New_York")
ET_UTC = UTC  # seeds store timestamps in UTC (the store's native tz), like test_report


def portfolio_settings(**overrides: object) -> Settings:
    # The forward projection (`portfolio.projection`) is a 500-path × 252-session Monte-Carlo run
    # for EVERY book in the payload, so leaving it at production settings turned this file from
    # 4.5s into 57s — an order of magnitude of CI, spent re-running a simulation none of these
    # tests assert anything about. It has its own module (`test_projection.py`) with its own
    # settings; here it is dialled down to the cheapest run that still produces a real block.
    # An explicit override still wins, so a test that *does* want the full thing can ask.
    defaults: dict[str, object] = {"portfolio_projection_paths": 8}
    defaults.update(overrides)
    return settings(**defaults)


def et_bar(o: float, h: float, low: float, c: float, *, minute: int = 0, hour: int = 8) -> Bar:
    # ET-aware; hour defaults to 08:00 (pre-market) so trigger-time checks pass unless overridden.
    start = datetime(2026, 7, 14, hour, minute, tzinfo=ET)
    return Bar(start=start, open=o, high=h, low=low, close=c, volume=1000.0)


def candidate(
    sym: str,
    minute: int,
    entry: float,
    stop: float,
    bars: list[Bar],
    *,
    float_shares: int | None = None,
    max_r: float | None = None,
    max_gain_pct: float | None = None,
) -> CandidateTrade:
    return CandidateTrade(
        trading_date=date(2026, 7, 14),
        symbol=sym,
        seg_id=f"2026-07-14:{sym}",
        run=1,
        trigger_at=datetime(2026, 7, 14, 8, minute, tzinfo=ET),
        entry_price=entry,
        entry_fill=entry,
        stop=stop,
        risk=entry - stop,
        entry_index=0,
        bars=tuple(bars),
        float_shares=float_shares,
        max_r=max_r,
        max_gain_pct=max_gain_pct,
    )


def seed_premarket(
    store: object,
    *,
    oid_time_utc: datetime,
    symbol: str = "AZI",
    price_scale: float = 1.0,
    float_shares: int | None = 8_000_000,
) -> None:
    """Seed a clean pre-market bull flag (AZI, triggers to ~2.8R) + a no-setup name (DUD).

    ``oid_time_utc`` is the first bar / first_hit; 12:00 UTC = 08:00 ET (EDT) → strictly pre-market;
    16:00 UTC = 12:00 ET → in-session, which the pre-market filter must reject.

    ``symbol`` seeds the identical setup under another ticker, so a caller can create candidates
    that trigger on the *same bar* — the tie the #381 ordering test needs.

    ``price_scale`` multiplies every price, keeping the setup's *shape* (all gates are percentage-
    based) while moving it in the price band — used to seed a sub-$2 name for the #386 floor.

    ``float_shares`` seeds the fundamentals row the candidate's float is read from (#390); pass
    None to seed no fundamentals at all, which is the 'source returned nothing' case."""

    assert isinstance(store, Store)
    day = oid_time_utc.date()
    t0 = oid_time_utc

    def bar_row(
        oid: str, sym: str, i: int, o: float, h: float, low: float, c: float, v: float = 1000.0
    ):  # type: ignore[no-untyped-def]
        return {
            "opportunity_id": oid,
            "symbol": sym,
            "bar_start_utc": t0 + timedelta(minutes=5 * i),
            "open": round(o * price_scale, 2),
            "high": round(h * price_scale, 2),
            "low": round(low * price_scale, 2),
            "close": round(c * price_scale, 2),
            "volume": v,
        }

    oid = f"{day.isoformat()}:{symbol}"
    store.append(
        "opportunities",
        [
            opportunity_row(
                oid,
                symbol,
                trading_date=day,
                first_seen=t0,
                con_id=1,
                rank=0,
            ),
        ],
        partition_date=day,
    )
    store.append(
        "bars",
        [
            bar_row(oid, symbol, 0, 5.0, 5.8, 4.6, 5.7),  # launch (green)
            bar_row(oid, symbol, 1, 5.7, 6.5, 5.6, 6.4, 2000),  # higher-high pole
            bar_row(oid, symbol, 2, 6.4, 6.1, 5.6, 5.7),  # flag (red)
            bar_row(oid, symbol, 3, 5.7, 7.64, 5.7, 7.5),  # trigger + Max R ~2.8
        ],
        partition_date=day,
    )
    store.append(
        "scanner_hits",
        [{"opportunity_id": oid, "symbol": symbol, "ts_utc": t0, "rank": 0}],
        partition_date=day,
    )
    if float_shares is not None:
        store.append(
            "fundamentals",
            [
                {
                    "opportunity_id": oid,
                    "symbol": symbol,
                    "ts_utc": t0,
                    "float_shares": float_shares,
                    "shares_outstanding": float_shares * 2,
                    "short_percent": 0.1,
                    "source": "fmp",
                }
            ],
            partition_date=day,
        )
