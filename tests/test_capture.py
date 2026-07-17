"""Tests for raw capture (#14, #62): discovery on the tick, bars in an end-of-day batch."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from pathlib import Path

from small_cap_stack.capture import (
    Bar,
    CaptureService,
    NewsItem,
    news_record,
    opportunity_id,
    parse_news_ts,
)
from small_cap_stack.config import Settings
from small_cap_stack.fundamentals import Fundamentals
from small_cap_stack.scanner import Candidate
from small_cap_stack.storage import Store

_TRADING_DATE = date(2026, 6, 29)


def _settings(**o: object) -> Settings:
    return Settings(_env_file=None, **o)  # type: ignore[call-arg]


def _bar(minute: int) -> Bar:
    return Bar(
        start=datetime(2026, 6, 29, 13, minute, tzinfo=UTC),
        open=1.0,
        high=2.0,
        low=0.5,
        close=1.5,
        volume=1000.0,
    )


class FakeBars:
    def __init__(self, bars: list[Bar]) -> None:
        self.bars = bars
        self.calls = 0
        self.symbols: list[str] = []

    async def fetch_day_bars(
        self, candidate: Candidate, *, trading_date: date, end: datetime | None = None
    ) -> list[Bar]:
        self.calls += 1
        self.symbols.append(candidate.symbol)
        return list(self.bars)


class FakeNews:
    def __init__(self, items: list[NewsItem]) -> None:
        self.items = items

    async def fetch_news(
        self, candidate: Candidate, *, lookback_days: int, limit: int
    ) -> list[NewsItem]:
        return list(self.items)


def _candidate(symbol: str = "AZI") -> Candidate:
    return Candidate(rank=0, symbol=symbol, con_id=42, exchange="NASDAQ")


def _svc(store: Store, bars: object, news: FakeNews) -> CaptureService:
    return CaptureService(store=store, bars=bars, news=news, settings=_settings())  # type: ignore[arg-type]


def test_opportunity_id_format() -> None:
    assert opportunity_id(date(2026, 6, 29), "azi") == "2026-06-29:AZI"


def test_scan_tick_records_discovery_but_no_bars(tmp_path: Path) -> None:
    store = Store(tmp_path)
    svc = _svc(
        store,
        FakeBars([_bar(30), _bar(35)]),
        FakeNews([NewsItem("2026-06-29 12:00:00", "DJ-N", "Big news", "a1")]),
    )
    now = datetime(2026, 6, 29, 13, 40, tzinfo=UTC)
    asyncio.run(svc.on_scan_tick([_candidate()], now))

    assert store.read("opportunities").height == 1
    assert store.read("scanner_hits").height == 1
    assert store.read("news").height == 1
    assert store.read("bars").is_empty()  # bars are NOT captured on the tick anymore


def test_eod_batch_writes_day_bars(tmp_path: Path) -> None:
    store = Store(tmp_path)
    svc = _svc(store, FakeBars([_bar(30), _bar(35), _bar(40)]), FakeNews([]))
    now = datetime(2026, 6, 29, 9, 40, tzinfo=UTC)
    asyncio.run(svc.on_scan_tick([_candidate("AAA"), _candidate("BBB")], now))
    assert store.read("bars").is_empty()

    asyncio.run(svc.capture_day_bars(_TRADING_DATE))
    bars = store.read("bars")
    assert bars.height == 6  # 3 bars x 2 opportunities
    assert set(bars["symbol"].to_list()) == {"AAA", "BBB"}


def test_eod_batch_dedups_duplicate_opportunities(tmp_path: Path) -> None:
    # A duplicate opportunities row (a mid-day restart re-opened the name) must not fire a
    # redundant historical request / duplicate bar write for the same symbol.
    store = Store(tmp_path)
    bars = FakeBars([_bar(30), _bar(35)])
    svc = _svc(store, bars, FakeNews([]))
    asyncio.run(svc.on_scan_tick([_candidate("AAA")], datetime(2026, 6, 29, 9, 40, tzinfo=UTC)))
    dup = store.read("opportunities").row(0, named=True)  # same opportunity_id, appended again
    store.append("opportunities", [dup], partition_date=_TRADING_DATE)

    asyncio.run(svc.capture_day_bars(_TRADING_DATE))
    assert bars.calls == 1  # fetched once, not once per duplicate row
    assert store.read("bars").height == 2  # one symbol's bars, not doubled


def test_eod_batch_skips_opportunities_that_already_have_bars(tmp_path: Path) -> None:
    # A retry of the EOD batch (after a partial failure) must only re-fetch opportunities still
    # lacking bars — not re-issue historical requests for ones already stored (#163-C2 pacing).
    store = Store(tmp_path)
    bars = FakeBars([_bar(30), _bar(35)])
    svc = _svc(store, bars, FakeNews([]))
    asyncio.run(svc.on_scan_tick([_candidate("AAA")], datetime(2026, 6, 29, 9, 40, tzinfo=UTC)))

    asyncio.run(svc.capture_day_bars(_TRADING_DATE))
    assert bars.calls == 1  # first pass fetches the one opportunity
    asyncio.run(svc.capture_day_bars(_TRADING_DATE))
    assert bars.calls == 1  # retry skips it — no redundant request


def test_second_tick_does_not_reopen_but_logs_hit(tmp_path: Path) -> None:
    store = Store(tmp_path)
    svc = _svc(store, FakeBars([_bar(30)]), FakeNews([]))
    now = datetime(2026, 6, 29, 9, 40, tzinfo=UTC)
    asyncio.run(svc.on_scan_tick([_candidate()], now))
    asyncio.run(svc.on_scan_tick([_candidate()], now))

    assert store.read("opportunities").height == 1  # not reopened
    assert store.read("scanner_hits").height == 2  # both appearances logged


def test_hydration_prevents_reopen_after_restart(tmp_path: Path) -> None:
    store = Store(tmp_path)
    now = datetime(2026, 6, 29, 9, 40, tzinfo=UTC)
    asyncio.run(_svc(store, FakeBars([]), FakeNews([])).on_scan_tick([_candidate()], now))
    assert store.read("opportunities").height == 1

    # A fresh service (simulating a mid-day restart) must rehydrate its open-set from storage
    # and NOT re-open the already-known opportunity.
    fresh = _svc(store, FakeBars([]), FakeNews([]))
    asyncio.run(fresh.on_scan_tick([_candidate()], now))
    assert store.read("opportunities").height == 1  # still not reopened
    assert store.read("scanner_hits").height == 2


def test_hydration_and_day_opportunities_scoped_to_requested_date(tmp_path: Path) -> None:
    # #322: both reads are dt=-scoped, leaning on partition == trading_date column (verified live).
    store = Store(tmp_path)
    other = date(2026, 6, 26)
    store.append(
        "opportunities",
        [
            {
                "opportunity_id": "2026-06-26:OLD",
                "symbol": "OLD",
                "con_id": 9,
                "trading_date": other,
                "first_seen_utc": datetime(2026, 6, 26, 13, 0, tzinfo=UTC),
                "first_rank": 0,
            }
        ],
        partition_date=other,
    )
    svc = _svc(store, FakeBars([]), FakeNews([]))
    now = datetime(2026, 6, 29, 9, 40, tzinfo=UTC)
    asyncio.run(svc.on_scan_tick([_candidate()], now))

    fresh = _svc(store, FakeBars([]), FakeNews([]))
    fresh._ensure_hydrated(_TRADING_DATE)
    assert fresh._open == {"2026-06-29:AZI"}  # the other date's row never leaks in

    day = svc._day_opportunities(_TRADING_DATE)
    assert day["opportunity_id"].to_list() == ["2026-06-29:AZI"]

    # A date with no partition reads as a zero-column frame — the is_empty() guards must fire
    # before any column is referenced.
    empty = _svc(store, FakeBars([]), FakeNews([]))
    empty._ensure_hydrated(date(2026, 6, 28))
    assert empty._open == set()
    assert svc._day_opportunities(date(2026, 6, 28)).is_empty()


class BoomBars:
    """A bar source that raises for one symbol but works for the rest."""

    def __init__(self, bad_symbol: str, good: list[Bar]) -> None:
        self.bad_symbol = bad_symbol
        self.good = good

    async def fetch_day_bars(
        self, candidate: Candidate, *, trading_date: date, end: datetime | None = None
    ) -> list[Bar]:
        if candidate.symbol == self.bad_symbol:
            raise RuntimeError("ib timeout")
        return list(self.good)


def test_one_symbol_failure_does_not_stall_the_batch(tmp_path: Path) -> None:
    store = Store(tmp_path)
    svc = _svc(store, BoomBars("BAD", [_bar(30)]), FakeNews([]))
    now = datetime(2026, 6, 29, 9, 40, tzinfo=UTC)
    asyncio.run(svc.on_scan_tick([_candidate("BAD"), _candidate("GOOD")], now))

    asyncio.run(svc.capture_day_bars(_TRADING_DATE))
    bars = store.read("bars")
    assert bars.height == 1  # only GOOD's bar persisted
    assert bars["symbol"].to_list() == ["GOOD"]


def test_parse_news_ts() -> None:
    assert parse_news_ts("2026-06-29 13:45:00.0") == datetime(2026, 6, 29, 13, 45, tzinfo=UTC)
    assert parse_news_ts("2026-06-29 13:45:00") == datetime(2026, 6, 29, 13, 45, tzinfo=UTC)
    assert parse_news_ts("garbage") is None
    assert parse_news_ts("") is None


def test_news_record_carries_utc_timestamp() -> None:
    rec = news_record("2026-06-29:AZI", "AZI", NewsItem("2026-06-29 13:45:00.0", "DJ-N", "h", "a1"))
    assert rec["ts_utc"] == datetime(2026, 6, 29, 13, 45, tzinfo=UTC)
    assert rec["time"] == "2026-06-29 13:45:00.0"  # raw string kept for provenance


def test_eod_news_refetch_appends_with_timestamp(tmp_path: Path) -> None:
    # A story that breaks after first sighting is captured by the EOD re-fetch (#97).
    store = Store(tmp_path)
    svc = _svc(store, FakeBars([]), FakeNews([NewsItem("2026-06-29 12:00:00", "DJ-N", "n", "a2")]))
    asyncio.run(svc.on_scan_tick([_candidate()], datetime(2026, 6, 29, 9, 40, tzinfo=UTC)))
    before = store.read("news").height

    asyncio.run(svc.capture_day_news(_TRADING_DATE))
    news = store.read("news")
    assert news.height > before  # re-fetch appended (duplicates deduped later on read)
    assert "ts_utc" in news.columns


def test_backfill_fetches_only_opportunities_missing_bars(tmp_path: Path) -> None:
    # AAA already has bars (EOD batch succeeded for it); BBB has none (it was missed). The catch-up
    # must fetch ONLY BBB — no redundant IBKR request for a symbol that already has bars (#100).
    store = Store(tmp_path)
    bars = FakeBars([_bar(30)])
    svc = _svc(store, bars, FakeNews([]))
    now = datetime(2026, 6, 29, 9, 40, tzinfo=UTC)
    asyncio.run(svc.on_scan_tick([_candidate("AAA"), _candidate("BBB")], now))
    # Seed bars for AAA only.
    store.append(
        "bars",
        [
            {
                "opportunity_id": "2026-06-29:AAA",
                "symbol": "AAA",
                "bar_start_utc": _bar(30).start,
                "open": 1.0,
                "high": 2.0,
                "low": 0.5,
                "close": 1.5,
                "volume": 1000.0,
            }
        ],
        partition_date=_TRADING_DATE,
    )

    added = asyncio.run(svc.capture_missing_bars(_TRADING_DATE))
    assert added is True
    assert bars.symbols == ["BBB"]  # only the missing symbol was fetched
    assert set(store.read("bars")["symbol"].to_list()) == {"AAA", "BBB"}


def test_backfill_is_noop_when_all_bars_present(tmp_path: Path) -> None:
    store = Store(tmp_path)
    bars = FakeBars([_bar(30)])
    svc = _svc(store, bars, FakeNews([]))
    asyncio.run(svc.on_scan_tick([_candidate("AAA")], datetime(2026, 6, 29, 9, 40, tzinfo=UTC)))
    asyncio.run(svc.capture_day_bars(_TRADING_DATE))
    calls_after_eod = bars.calls

    assert asyncio.run(svc.capture_missing_bars(_TRADING_DATE)) is False
    assert bars.calls == calls_after_eod  # nothing re-fetched


def test_backfill_recent_scans_multiple_days(tmp_path: Path) -> None:
    store = Store(tmp_path)
    bars = FakeBars([_bar(30)])
    svc = _svc(store, bars, FakeNews([]))
    # An opportunity on 2026-06-29 with no bars; back-fill scanning back from 2026-06-30 repairs it.
    asyncio.run(svc.on_scan_tick([_candidate("AAA")], datetime(2026, 6, 29, 9, 40, tzinfo=UTC)))

    filled = asyncio.run(svc.backfill_recent(date(2026, 6, 30), days=3))
    assert filled == [_TRADING_DATE]  # only the day that had a missing opportunity
    assert store.read("bars")["symbol"].to_list() == ["AAA"]


# --- per-candidate isolation + batched scanner_hits (#254, #247) --------------------------------


class _BoomFundamentals:
    """Raises for one symbol; records every symbol it was asked for."""

    def __init__(self, boom_symbol: str) -> None:
        self.boom_symbol = boom_symbol
        self.seen: list[str] = []

    async def fetch_all(self, candidate: Candidate) -> list[Fundamentals]:
        self.seen.append(candidate.symbol)
        if candidate.symbol == self.boom_symbol:
            raise RuntimeError("fundamentals exploded")
        return [Fundamentals(candidate.symbol, 5_000_000, 9_000_000, 0.1, "fake")]


def test_scan_tick_isolates_a_failing_candidate(tmp_path: Path) -> None:
    """A high-rank symbol failing to open must not starve the ones ranked below it (#254)."""
    store = Store(tmp_path)
    svc = CaptureService(
        store=store,
        bars=FakeBars([]),  # type: ignore[arg-type]
        news=FakeNews([]),
        settings=_settings(),
        fundamentals=_BoomFundamentals("BAD"),  # type: ignore[arg-type]
    )
    now = datetime(2026, 6, 29, 14, 0, tzinfo=UTC)
    cands = [_candidate("BAD"), _candidate("GOOD"), _candidate("ALSOGOOD")]

    asyncio.run(svc.on_scan_tick(cands, now))  # must not raise

    opps = store.read("opportunities")
    assert sorted(opps["symbol"].to_list()) == ["ALSOGOOD", "BAD", "GOOD"]  # not stalled

    # Every candidate still gets a scanner hit — that it appeared is true regardless of opening.
    hits = store.read("scanner_hits")
    assert set(hits["symbol"].to_list()) == {"BAD", "GOOD", "ALSOGOOD"}


def test_scan_tick_writes_one_scanner_hits_file_per_tick(tmp_path: Path) -> None:
    """One append per tick, not one per candidate (#247 small-files explosion)."""
    store = Store(tmp_path)
    svc = _svc(store, FakeBars([]), FakeNews([]))
    now = datetime(2026, 6, 29, 14, 0, tzinfo=UTC)
    cands = [_candidate("AZI"), _candidate("BZI"), _candidate("CZI")]

    asyncio.run(svc.on_scan_tick(cands, now))

    part_dir = tmp_path / "scanner_hits" / "dt=2026-06-29"
    assert len(list(part_dir.glob("*.parquet"))) == 1  # was 1 file per candidate
    assert store.read("scanner_hits").height == 3  # same raw rows

    asyncio.run(svc.on_scan_tick(cands, now))  # a second tick adds exactly one more file
    assert len(list(part_dir.glob("*.parquet"))) == 2
    assert store.read("scanner_hits").height == 6


def test_scan_tick_with_no_candidates_writes_nothing(tmp_path: Path) -> None:
    store = Store(tmp_path)
    svc = _svc(store, FakeBars([]), FakeNews([]))
    asyncio.run(svc.on_scan_tick([], datetime(2026, 6, 29, 14, 0, tzinfo=UTC)))
    assert store.read("scanner_hits").is_empty()


# --- EOD fundamentals backfill (#255) ----------------------------------------------------------


class _FlakyFundamentals:
    """Fails on the first call for a symbol, succeeds afterwards — the #255 scenario."""

    def __init__(self, *, fail_first: bool = True) -> None:
        self.fail_first = fail_first
        self.calls: list[str] = []

    async def fetch_all(self, candidate: Candidate) -> list[Fundamentals]:
        self.calls.append(candidate.symbol)
        if self.fail_first and self.calls.count(candidate.symbol) == 1:
            return []  # what MultiFundamentals returns when every source errors
        return [Fundamentals(candidate.symbol, 5_000_000, 9_000_000, 0.1, "fake")]


def _fund_svc(store: Store, funds: object) -> CaptureService:
    return CaptureService(
        store=store,
        bars=FakeBars([]),  # type: ignore[arg-type]
        news=FakeNews([]),
        settings=_settings(),
        fundamentals=funds,  # type: ignore[arg-type]
    )


def test_eod_backfills_fundamentals_missing_from_a_failed_open(tmp_path: Path) -> None:
    """A fetch failure at open time must not disqualify the symbol forever (#255)."""
    store = Store(tmp_path)
    funds = _FlakyFundamentals()
    svc = _fund_svc(store, funds)

    asyncio.run(svc.on_scan_tick([_candidate("AZI")], datetime(2026, 6, 29, 14, 0, tzinfo=UTC)))
    assert store.read("fundamentals").is_empty()  # the open-time fetch came back empty

    filled = asyncio.run(svc.capture_missing_fundamentals(_TRADING_DATE))

    assert filled == 1
    df = store.read("fundamentals")
    assert df.height == 1
    assert df["symbol"].to_list() == ["AZI"]
    assert df["float_shares"].to_list() == [5_000_000]
    assert df["opportunity_id"].to_list() == ["2026-06-29:AZI"]


def test_eod_fundamentals_skips_opportunities_that_already_have_them(tmp_path: Path) -> None:
    """Idempotent: no redundant fetches for opportunities already covered."""
    store = Store(tmp_path)
    funds = _FlakyFundamentals(fail_first=False)  # succeeds at open time
    svc = _fund_svc(store, funds)
    asyncio.run(svc.on_scan_tick([_candidate("AZI")], datetime(2026, 6, 29, 14, 0, tzinfo=UTC)))
    assert store.read("fundamentals").height == 1
    calls_after_open = len(funds.calls)

    assert asyncio.run(svc.capture_missing_fundamentals(_TRADING_DATE)) == 0
    assert funds.calls == funds.calls[:calls_after_open]  # no re-fetch
    assert store.read("fundamentals").height == 1


def test_eod_fundamentals_still_missing_is_retried_not_recorded(tmp_path: Path) -> None:
    """A source that is still down leaves no row — and stays eligible for the next run."""
    store = Store(tmp_path)

    class _AlwaysEmpty:
        async def fetch_all(self, candidate: Candidate) -> list[Fundamentals]:
            return []

    svc = _fund_svc(store, _AlwaysEmpty())
    asyncio.run(svc.on_scan_tick([_candidate("AZI")], datetime(2026, 6, 29, 14, 0, tzinfo=UTC)))
    assert asyncio.run(svc.capture_missing_fundamentals(_TRADING_DATE)) == 0
    assert store.read("fundamentals").is_empty()


def test_eod_fundamentals_isolates_a_raising_symbol(tmp_path: Path) -> None:
    """A symbol whose source raises is skipped, not propagated — and the batch still runs it."""
    store = Store(tmp_path)
    funds = _BoomFundamentals("BAD")
    svc = _fund_svc(store, funds)
    now = datetime(2026, 6, 29, 14, 0, tzinfo=UTC)
    asyncio.run(svc.on_scan_tick([_candidate("BAD"), _candidate("GOOD")], now))
    assert set(store.read("fundamentals")["symbol"].to_list()) == {"GOOD"}

    funds.seen.clear()
    filled = asyncio.run(svc.capture_missing_fundamentals(_TRADING_DATE))

    # It was actually attempted (not silently excluded) and it raised — so nothing was filled...
    assert funds.seen == ["BAD"]
    assert filled == 0
    # ...and GOOD, already covered, was not re-fetched.
    assert set(store.read("fundamentals")["symbol"].to_list()) == {"GOOD"}


def test_eod_fundamentals_no_opportunities_is_a_noop(tmp_path: Path) -> None:
    store = Store(tmp_path)
    svc = _fund_svc(store, _FlakyFundamentals())
    assert asyncio.run(svc.capture_missing_fundamentals(_TRADING_DATE)) == 0


def test_partially_opened_symbol_is_not_reopened_every_tick(tmp_path: Path) -> None:
    """A failure *after* the opportunities row must not re-open the symbol next tick.

    `_open_opportunity` writes the row first and enriches after. If the oid were only marked open
    at the end, any enrichment failure would leave the row written but the symbol un-opened — so
    every subsequent tick appends another row. Over a ~480-tick scan window that is ~480 duplicate
    rows and Parquet files for one bad symbol: the #247 small-files explosion, moved into
    `opportunities`. The row itself is the definition of "open" (`_ensure_hydrated` re-seeds from
    it), so it is marked open as soon as it is durable.
    """
    store = Store(tmp_path)
    svc = _fund_svc(store, _BoomFundamentals("BAD"))
    now = datetime(2026, 6, 29, 14, 0, tzinfo=UTC)

    for _ in range(5):  # five ticks, same failing candidate
        asyncio.run(svc.on_scan_tick([_candidate("BAD"), _candidate("GOOD")], now))

    opps = store.read("opportunities")
    assert sorted(opps["symbol"].to_list()) == ["BAD", "GOOD"]  # one row each, not five
    assert opps.height == 2


def test_scan_tick_survives_a_scanner_hits_append_failure(tmp_path: Path) -> None:
    """A hit-log write failure must not abort the tick — app._on_tick doesn't wrap this call."""
    store = Store(tmp_path)
    svc = _svc(store, FakeBars([]), FakeNews([]))

    async def boom(*args: object, **kwargs: object) -> None:
        raise OSError("No space left on device")

    svc.store.append_async = boom  # type: ignore[method-assign]
    # Must return normally: raising here would skip the dashboard refresh for the whole tick.
    asyncio.run(svc.on_scan_tick([_candidate("AZI")], datetime(2026, 6, 29, 14, 0, tzinfo=UTC)))


def test_eod_fundamentals_refetches_a_null_float_row(tmp_path: Path) -> None:
    """A row with float_shares=None is NOT coverage — it's the case the float gate fails on (#255).

    yfinance's `.info` routinely omits floatShares for micro-caps and `from_info` still returns a
    Fundamentals, so "has any row" would mark exactly these opportunities as done forever.
    """
    store = Store(tmp_path)

    class _NullThenReal:
        def __init__(self) -> None:
            self.calls = 0

        async def fetch_all(self, candidate: Candidate) -> list[Fundamentals]:
            self.calls += 1
            if self.calls == 1:  # a row, but with no usable float
                return [Fundamentals(candidate.symbol, None, 9_000_000, 0.1, "yfinance")]
            return [Fundamentals(candidate.symbol, 5_000_000, 9_000_000, 0.1, "fmp")]

    funds = _NullThenReal()
    svc = _fund_svc(store, funds)
    asyncio.run(svc.on_scan_tick([_candidate("AZI")], datetime(2026, 6, 29, 14, 0, tzinfo=UTC)))
    assert store.read("fundamentals").height == 1  # the null-float row exists

    assert asyncio.run(svc.capture_missing_fundamentals(_TRADING_DATE)) == 1  # re-fetched anyway

    df = store.read("fundamentals")
    assert sorted(x for x in df["float_shares"].to_list() if x is not None) == [5_000_000]
    # ...and now that a usable float exists, it is not fetched a third time.
    assert asyncio.run(svc.capture_missing_fundamentals(_TRADING_DATE)) == 0
