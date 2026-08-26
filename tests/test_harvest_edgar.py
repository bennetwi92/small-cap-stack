"""Point-in-time share counts for reconstructed sessions (#563).

Three things here can be wrong in ways nothing downstream would ever reveal, so they get the tests:

1. **Lookahead.** ``filed`` and ``end`` differ by days, and selecting on the wrong one hands a
   backtest a filing it could not have read. The resulting book is *better* than reality, quietly.
2. **A float number that no filing ever stated.** EDGAR publishes shares *outstanding*. Writing it
   into ``float_shares`` would put it in the column the live tracker fills from a real float source,
   where nothing afterwards could tell the two apart.
3. **A failure stored as a fact.** The pass writes a null row for "EDGAR has nothing", which is what
   makes it terminate — so a *transport* failure must never take that path, or one rejected
   ``User-Agent`` pins a permanent null on every symbol it touched.

The dilution case is the reason the first one matters at all: CLSK went 37M shares (2011) to 257M
(2026), so on this population "today's number, stamped backwards" is not a rounding error.
"""

from __future__ import annotations

import io
import json
import urllib.error
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

from small_cap_stack.capture import opportunity_record
from small_cap_stack.config import Settings
from small_cap_stack.harvest import (
    HARVEST_DATASETS,
    EdgarFundamentals,
    EdgarNotConfigured,
    SharesRow,
    discard_partial,
    discard_partial_fundamentals,
    edgar_source,
    harvest_fundamentals,
    harvest_store,
    plan_fundamentals,
    run_fundamentals,
    shares_asof,
)
from small_cap_stack.harvest import __main__ as cli_mod
from small_cap_stack.harvest.edgar import (
    EdgarError,
    cik_candidates,
    parse_cik_map,
    parse_shares_series,
)
from small_cap_stack.report import _funds_for
from small_cap_stack.scanner import Candidate
from small_cap_stack.storage import Store
from tests.support import settings

DAY = date(2026, 7, 2)
PREV = date(2026, 7, 1)


def _settings(tmp_path: Path, **kw: Any) -> Settings:
    kw.setdefault("recon_subdir", "recon")
    kw.setdefault("harvest_edgar_user_agent", "small-cap-stack tests@example.com")
    return settings(data_dir=tmp_path / "data", **kw)


def _row(end: str, filed: str, val: int, form: str = "10-Q") -> SharesRow:
    return SharesRow(
        end=date.fromisoformat(end), filed=date.fromisoformat(filed), val=val, form=form
    )


# ================================================================================================
# shares_asof: the correctness rule of the issue
# ================================================================================================


def test_selects_on_the_filing_date_not_the_cover_date() -> None:
    """The lookahead this module exists to prevent.

    A 10-Q dated 2026-08-04 was filed on the 6th. On the 5th its cover date already qualifies and
    the number did not exist yet — so an ``end <= session`` filter silently reads two days ahead.
    """
    rows = [
        _row("2026-05-07", "2026-05-11", 256_608_606),
        _row("2026-08-04", "2026-08-06", 300_000_000),
    ]
    assert shares_asof(rows, date(2026, 8, 5)).val == 256_608_606  # type: ignore[union-attr]
    assert shares_asof(rows, date(2026, 8, 6)).val == 300_000_000  # type: ignore[union-attr]


def test_the_dilution_case_todays_count_is_not_the_sessions_count() -> None:
    """CLSK's real shape: 37M shares in 2011, 257M in 2026. The naive answer is 7x wrong.

    This is the population the recon book is full of, which is why the whole pass exists rather
    than the harvest simply stamping `stable/shares-float`'s single current row onto every date.
    """
    rows = [
        _row("2011-06-29", "2012-05-21", 37_076_779),
        _row("2019-03-31", "2019-05-10", 60_000_000),
        _row("2026-08-04", "2026-08-06", 256_817_073),
    ]
    picked = shares_asof(rows, date(2024, 3, 15))
    assert picked is not None
    assert picked.val == 60_000_000  # the count knowable that day, not the current one
    assert picked.val != rows[-1].val


def test_an_amendment_restating_an_older_period_does_not_win() -> None:
    """A 10-Q/A filed later restates an *older* cover date, so "latest filed" returns a stale count.

    Ordering on ``end`` first asks the right question — which count describes the most recent
    date? — and the ``filed`` tiebreak then prefers the restatement over the original it corrects.
    """
    original = _row("2026-03-31", "2026-04-10", 100_000_000)
    newer = _row("2026-06-30", "2026-07-08", 120_000_000)
    amendment = _row("2026-03-31", "2026-09-01", 101_500_000, form="10-Q/A")
    on = date(2026, 9, 15)
    assert shares_asof([original, newer, amendment], on) == newer
    # ...and for the amended period itself, the correction wins over what it corrected.
    assert shares_asof([original, amendment], date(2026, 9, 2)) == amendment


def test_nothing_filed_yet_is_none_rather_than_the_first_filing() -> None:
    """A company that IPO'd after the session has no knowable count on it. Reaching forward to its
    first filing would be the same lookahead bug wearing a different hat."""
    assert shares_asof([_row("2026-06-30", "2026-07-08", 5_000_000)], date(2026, 1, 2)) is None
    assert shares_asof([], DAY) is None


# ================================================================================================
# parsing: tolerant of a decade of filings from thousands of filers
# ================================================================================================


def test_parse_shares_series_reads_the_real_payload_shape() -> None:
    payload = {
        "cik": 827876,
        "units": {
            "shares": [
                {"end": "2026-05-07", "val": 256608606, "form": "10-Q", "filed": "2026-05-11"},
                {"end": "2026-08-04", "val": 256817073, "form": "10-Q", "filed": "2026-08-06"},
            ]
        },
    }
    rows = parse_shares_series(payload)
    assert [r.val for r in rows] == [256_608_606, 256_817_073]
    assert rows[0].filed == date(2026, 5, 11) and rows[0].form == "10-Q"


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        {"units": None},
        {"units": {"shares": None}},
        {"units": {"other-unit": [{"end": "2026-01-01", "val": 1, "filed": "2026-01-02"}]}},
    ],
)
def test_parse_shares_series_returns_empty_rather_than_raising(payload: object) -> None:
    assert parse_shares_series(payload) == []


def test_one_malformed_row_does_not_cost_the_whole_symbol() -> None:
    """A decade of historical filings will contain something unparseable; losing the company over
    it would silently drop its share count from every session it appears in."""
    payload = {
        "units": {
            "shares": [
                {"end": "not-a-date", "val": 1, "filed": "2026-01-02"},
                {"end": "2026-01-01", "val": "lots", "filed": "2026-01-02"},
                {"end": "2026-01-01", "filed": "2026-01-02"},
                "not-even-a-dict",
                {"end": "2026-06-30", "val": 7_000_000, "filed": "2026-07-08"},
            ]
        }
    }
    assert [r.val for r in parse_shares_series(payload)] == [7_000_000]


def test_parse_cik_map_zero_pads_the_bare_integer_sec_serves() -> None:
    """``cik_str`` is an int despite the name, and the concept URL wants 10 digits."""
    payload = {
        "0": {"cik_str": 1045810, "ticker": "NVDA", "title": "NVIDIA CORP"},
        "1": {"cik_str": 827876, "ticker": "clsk", "title": "CleanSpark"},
        "2": {"cik_str": None, "ticker": "BAD", "title": "no cik"},
        "3": {"cik_str": "not-a-number", "ticker": "ALSOBAD"},
        "4": {"cik_str": 1, "ticker": ""},
        "5": "not a dict",
    }
    assert parse_cik_map(payload) == {"NVDA": "0001045810", "CLSK": "0000827876"}
    assert parse_cik_map(None) == {}


def test_cik_candidates_tries_both_share_class_punctuations() -> None:
    """The vendor says ``BRK.B``, EDGAR says ``BRK-B``. A class ticker that misses is a symbol
    carrying no share count for the whole harvest."""
    assert cik_candidates("brk.b")[:2] == ["BRK.B", "BRK-B"]
    assert cik_candidates("AAAA") == ["AAAA"]


# ================================================================================================
# transport: no key, but SEC has to know who is calling
# ================================================================================================


class _Resp:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self) -> _Resp:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def read(self) -> bytes:
        return self._body


def _http_error(code: int, body: bytes = b"") -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://sec", code, "err", {}, io.BytesIO(body))  # type: ignore[arg-type]


CIK_MAP = {"0": {"cik_str": 1234, "ticker": "AAAA"}, "1": {"cik_str": 99, "ticker": "BBBB"}}
SERIES = {
    "units": {
        "shares": [
            {"end": "2026-03-31", "val": 12_000_000, "form": "10-Q", "filed": "2026-04-10"},
            {"end": "2026-06-30", "val": 40_000_000, "form": "10-Q", "filed": "2026-07-08"},
        ]
    }
}


@dataclass
class _Fake:
    """A stand-in for ``urllib.request.urlopen`` that records what was asked and how."""

    responses: dict[str, object] = field(default_factory=dict)
    errors: dict[str, urllib.error.HTTPError] = field(default_factory=dict)
    urls: list[str] = field(default_factory=list)
    agents: list[str] = field(default_factory=list)

    def __call__(self, req: Any, timeout: float = 0) -> _Resp:  # noqa: ARG002 — urlopen's signature
        self.urls.append(req.full_url)
        self.agents.append(req.get_header("User-agent"))
        for key, exc in self.errors.items():
            if key in req.full_url:
                raise exc
        for key, payload in self.responses.items():
            if key in req.full_url:
                return _Resp(json.dumps(payload).encode())
        raise _http_error(404)


def _source(monkeypatch: Any, fake: _Fake, **kw: Any) -> EdgarFundamentals:
    monkeypatch.setattr("urllib.request.urlopen", fake)
    kw.setdefault("sleep", lambda _: None)
    return EdgarFundamentals(user_agent="scs tests@example.com", **kw)


def test_a_share_count_arrives_with_the_filing_that_established_it(monkeypatch: Any) -> None:
    fake = _Fake({"company_tickers.json": CIK_MAP, "CIK0000001234": SERIES})
    src = _source(monkeypatch, fake)
    got = src.shares_asof("AAAA", date(2026, 7, 20))
    assert got is not None
    assert got.shares_outstanding == 40_000_000
    assert (got.as_of, got.filed, got.form) == (date(2026, 6, 30), date(2026, 7, 8), "10-Q")
    # The CIK is zero-padded into the concept path, and the tag is the cover-page `dei` one.
    assert "CIK0000001234/dei/EntityCommonStockSharesOutstanding.json" in fake.urls[-1]
    assert all(agent == "scs tests@example.com" for agent in fake.agents)


def test_shares_outstanding_is_never_written_as_float_shares(monkeypatch: Any) -> None:
    """The distinction #563 insists on. EDGAR publishes outstanding — a ceiling on free float, not
    free float — and ``float_shares`` is the column a real float source fills."""
    src = _source(monkeypatch, _Fake({"company_tickers.json": CIK_MAP, "CIK0000001234": SERIES}))
    got = src.shares_asof("AAAA", date(2026, 7, 20))
    assert got is not None
    assert got.float_shares is None
    assert got.source == "edgar"


def test_the_403_says_which_setting_is_missing(monkeypatch: Any) -> None:
    """SEC serves an HTML block page, not a JSON error, and it looks nothing like a rate limit —
    the one failure here a reader would otherwise misdiagnose as an outage."""
    fake = _Fake(errors={"company_tickers.json": _http_error(403, b"<html>Request Rate...")})
    src = _source(monkeypatch, fake)
    with pytest.raises(EdgarError, match="HARVEST_EDGAR_USER_AGENT"):
        src.shares_asof("AAAA", DAY)


def test_a_filer_with_no_cover_page_tag_is_data_not_a_failure(monkeypatch: Any) -> None:
    """404 on the concept means "nothing here". Raising would abandon a whole session over one
    shell company."""
    fake = _Fake({"company_tickers.json": CIK_MAP}, errors={"CIK0000001234": _http_error(404)})
    assert _source(monkeypatch, fake).shares_asof("AAAA", DAY) is None


def test_a_symbol_sec_does_not_list_is_data_not_a_failure(monkeypatch: Any) -> None:
    """The map holds ~10.4k tickers; a foreign private issuer or an OTC name is genuinely absent."""
    src = _source(monkeypatch, _Fake({"company_tickers.json": CIK_MAP}))
    assert src.shares_asof("ZZZZ", DAY) is None
    assert src.cik_for("ZZZZ") is None


def test_the_series_is_fetched_once_per_symbol_however_many_sessions_ask(monkeypatch: Any) -> None:
    """What makes a ~500-session backfill minutes rather than nights: a company's whole filing
    history arrives in one response, so the cost is per SYMBOL, not per symbol-day."""
    fake = _Fake({"company_tickers.json": CIK_MAP, "CIK0000001234": SERIES})
    src = _source(monkeypatch, fake)
    for day in (date(2026, 7, 20), date(2026, 7, 21), date(2026, 7, 22)):
        assert src.shares_asof("AAAA", day) is not None
    assert src.calls == 2  # the ticker map, then the concept — and nothing more
    # A symbol EDGAR has nothing for is remembered too, or it would cost a call per session.
    src.shares_asof("ZZZZ", DAY)
    src.shares_asof("ZZZZ", PREV)
    assert src.calls == 2


def test_it_sleeps_between_requests_to_stay_under_sec_fair_access(monkeypatch: Any) -> None:
    slept: list[float] = []
    fake = _Fake({"company_tickers.json": CIK_MAP, "CIK0000001234": SERIES})
    src = _source(monkeypatch, fake, min_interval_sec=0.15, sleep=slept.append)
    src.shares_asof("AAAA", DAY)
    assert slept == [0.15]  # before the second call; the first pays nothing


def test_a_network_error_is_retried_and_then_surfaces_as_a_harvest_error(monkeypatch: Any) -> None:
    """It has to end as an ``EdgarError`` rather than a bare ``URLError``: that is what the pass
    counts as a *failure* (as opposed to "EDGAR has nothing"), and so what abandons a date instead
    of pinning a permanent null on it."""
    attempts: list[int] = []

    def boom(req: Any, timeout: float = 0) -> None:  # noqa: ARG001 — urlopen's signature
        attempts.append(1)
        raise urllib.error.URLError("no route to host")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    src = EdgarFundamentals(user_agent="scs t@e.com", max_retries=2, sleep=lambda _: None)
    with pytest.raises(EdgarError, match="no route to host"):
        src.shares_asof("AAAA", DAY)
    assert len(attempts) == 3


def test_a_symbol_whose_filings_all_postdate_the_session_is_none(monkeypatch: Any) -> None:
    """It has a CIK and a series; nothing in it was filed yet. That is a null, not a failure."""
    src = _source(monkeypatch, _Fake({"company_tickers.json": CIK_MAP, "CIK0000001234": SERIES}))
    assert src.shares_asof("AAAA", date(2026, 1, 5)) is None


def test_a_rate_limit_is_retried_rather_than_abandoned(monkeypatch: Any) -> None:
    fake = _Fake({"company_tickers.json": CIK_MAP}, errors={"CIK": _http_error(429)})
    src = _source(monkeypatch, fake, max_retries=2)
    with pytest.raises(EdgarError, match="HTTP 429"):
        src.shares_asof("AAAA", DAY)
    assert sum(1 for u in fake.urls if "CIK0000001234" in u) == 3  # the try plus two retries


# ================================================================================================
# the writer: what lands in data/recon/fundamentals
# ================================================================================================


@dataclass
class FakeShares:
    """A ``PointInTimeFundamentals`` under test control — answers, silences and failures."""

    answers: dict[str, int] = field(default_factory=dict)
    fails: set[str] = field(default_factory=set)
    calls: int = 0
    asked: list[tuple[str, date]] = field(default_factory=list)

    def shares_asof(self, symbol: str, on: date) -> Any:
        self.calls += 1
        self.asked.append((symbol, on))
        if symbol in self.fails:
            raise EdgarError(f"boom {symbol}")
        val = self.answers.get(symbol)
        if val is None:
            return None
        from small_cap_stack.fundamentals import AsOfShares

        return AsOfShares(
            symbol=symbol,
            float_shares=None,
            shares_outstanding=val,
            source="edgar",
            as_of=date(2026, 6, 30),
            filed=date(2026, 7, 1),
            form="10-Q",
        )


def _opportunities(store: Store, trading_date: date, *symbols: str) -> None:
    store.append(
        "opportunities",
        [
            opportunity_record(
                Candidate(rank=i, symbol=sym, con_id=0, exchange="SMART", currency="USD"),
                f"{trading_date.isoformat()}:{sym}",
                datetime(2026, 7, 2, 12, 0, tzinfo=UTC),
                trading_date,
            )
            for i, sym in enumerate(symbols, start=1)
        ],
        partition_date=trading_date,
    )


def test_a_row_lands_for_every_opportunity_carrying_its_provenance(tmp_path: Path) -> None:
    s = _settings(tmp_path)
    store = harvest_store(s)
    _opportunities(store, DAY, "AAAA", "BBBB")
    src = FakeShares(answers={"AAAA": 12_000_000, "BBBB": 4_000_000})

    result = harvest_fundamentals(src, store, s, DAY)
    assert (result.opportunities, result.resolved, result.unresolved, result.failed) == (2, 2, 0, 0)

    rows = store.read("fundamentals", dt=DAY).sort("symbol").to_dicts()
    assert [r["opportunity_id"] for r in rows] == [
        f"{DAY.isoformat()}:AAAA",
        f"{DAY.isoformat()}:BBBB",
    ]
    assert [r["shares_outstanding"] for r in rows] == [12_000_000, 4_000_000]
    # The two dates are what make the number auditable afterwards rather than taken on trust.
    assert rows[0]["as_of"] == date(2026, 6, 30) and rows[0]["filed"] == date(2026, 7, 1)
    assert rows[0]["form"] == "10-Q"
    # Deterministic, and unmistakably not a capture time: a live row lands inside the session.
    assert rows[0]["ts_utc"] == datetime(DAY.year, DAY.month, DAY.day, tzinfo=UTC)


def test_the_book_reads_the_rows_through_the_live_seam_and_still_sees_no_float(
    tmp_path: Path,
) -> None:
    """``portfolio.extract`` merges fundamentals through ``report._funds_for``. These rows go in
    unchanged and come out as *no float*, which is the honest answer: EDGAR never stated one.
    ``shares_outstanding`` DOES surface (#694, D-45 reads it for the shares-out selection band) —
    EDGAR is exactly the source that datum has, unlike float."""
    s = _settings(tmp_path)
    store = harvest_store(s)
    _opportunities(store, DAY, "AAAA")
    harvest_fundamentals(FakeShares(answers={"AAAA": 12_000_000}), store, s, DAY)

    funds = store.read("fundamentals", dt=DAY)
    assert _funds_for(funds, f"{DAY.isoformat()}:AAAA") == (None, None, 12_000_000)


def test_a_symbol_edgar_has_nothing_for_is_recorded_as_a_null_rather_than_omitted(
    tmp_path: Path,
) -> None:
    """The property that makes the pass terminate. An absent row is indistinguishable from an
    unharvested date, so omitting the misses would re-ask about every un-findable name forever."""
    s = _settings(tmp_path)
    store = harvest_store(s)
    _opportunities(store, DAY, "AAAA", "ZZZZ")
    result = harvest_fundamentals(FakeShares(answers={"AAAA": 12_000_000}), store, s, DAY)

    assert (result.resolved, result.unresolved, result.failed) == (1, 1, 0)
    rows = {r["symbol"]: r for r in store.read("fundamentals", dt=DAY).to_dicts()}
    assert rows["ZZZZ"]["shares_outstanding"] is None
    assert rows["ZZZZ"]["as_of"] is None
    assert plan_fundamentals(store) == []  # ...and the date is done, not offered again


def test_a_transport_failure_is_never_stored_as_a_null(tmp_path: Path) -> None:
    """The other half of the same rule. A rejected User-Agent fails every symbol; writing those as
    "EDGAR has nothing" would pin a permanent null on every date it touched."""
    s = _settings(tmp_path)
    store = harvest_store(s)
    _opportunities(store, DAY, "AAAA", "BBBB")
    result = harvest_fundamentals(FakeShares(fails={"AAAA", "BBBB"}), store, s, DAY)

    assert not result.complete and result.failed == 2
    assert store.read("fundamentals", dt=DAY).is_empty()
    assert plan_fundamentals(store) == [DAY]  # still pending, so it is retried


def test_scattered_failures_past_the_ratio_abandon_the_date(tmp_path: Path) -> None:
    """A date sampled from a fraction of its universe extracts perfectly well and is
    indistinguishable, afterwards, from a genuinely thin one (#446) — the shared rule in
    ``runner.abandon_reason``, applied to this pass too."""
    s = _settings(tmp_path, harvest_max_failure_ratio=0.2, harvest_max_consecutive_failures=99)
    store = harvest_store(s)
    symbols = [f"S{i:02d}" for i in range(12)]
    _opportunities(store, DAY, *symbols)
    src = FakeShares(answers=dict.fromkeys(symbols, 1_000_000), fails=set(symbols[:4]))

    result = harvest_fundamentals(src, store, s, DAY)
    assert not result.complete and result.failed == 4  # 4/12 > 20%
    assert store.read("fundamentals", dt=DAY).is_empty()


def test_the_breaker_stops_asking_once_sec_is_clearly_the_problem(tmp_path: Path) -> None:
    s = _settings(tmp_path, harvest_max_consecutive_failures=3)
    store = harvest_store(s)
    symbols = [f"S{i:02d}" for i in range(20)]
    _opportunities(store, DAY, *symbols)
    src = FakeShares(fails=set(symbols))

    result = harvest_fundamentals(src, store, s, DAY)
    assert not result.complete
    assert src.calls == 3  # not 20


def test_a_rerun_replaces_the_partition_rather_than_appending_to_it(tmp_path: Path) -> None:
    """The store is append-only, so a second pass would otherwise leave two share counts per
    opportunity and flip the day's cache fingerprint on every rebuild."""
    s = _settings(tmp_path)
    store = harvest_store(s)
    _opportunities(store, DAY, "AAAA")
    run_fundamentals(FakeShares(answers={"AAAA": 1_000_000}), store, s, [DAY])
    run_fundamentals(FakeShares(answers={"AAAA": 2_000_000}), store, s, [DAY])

    rows = store.read("fundamentals", dt=DAY).to_dicts()
    assert len(rows) == 1 and rows[0]["shares_outstanding"] == 2_000_000


def test_one_unreachable_date_does_not_stop_the_next(tmp_path: Path) -> None:
    """Unlike the minute-bar pass, which stops the night on the first entitlement wall because
    everything older is equally unbuyable. Here one bad date says nothing about the next."""
    s = _settings(tmp_path)
    store = harvest_store(s)
    _opportunities(store, DAY, "AAAA")
    _opportunities(store, PREV, "BBBB")
    src = FakeShares(answers={"BBBB": 5_000_000}, fails={"AAAA"})

    results = run_fundamentals(src, store, s, [DAY, PREV])
    assert [r.complete for r in results] == [False, True]
    assert store.read("fundamentals", dt=PREV).height == 1
    assert plan_fundamentals(store) == [DAY]


# ================================================================================================
# planning: "done" is read off the disk, not out of a checkpoint
# ================================================================================================


def test_planning_offers_harvested_dates_without_share_counts_newest_first(tmp_path: Path) -> None:
    s = _settings(tmp_path)
    store = harvest_store(s)
    _opportunities(store, PREV, "AAAA")
    _opportunities(store, DAY, "AAAA")
    assert plan_fundamentals(store) == [DAY, PREV]

    run_fundamentals(FakeShares(answers={"AAAA": 1}), store, s, [DAY])
    assert plan_fundamentals(store) == [PREV]


def test_reharvesting_a_date_drops_its_share_counts_so_they_are_rebuilt(tmp_path: Path) -> None:
    """``fundamentals`` is in HARVEST_DATASETS, and because "done" is the partition on disk, the
    next pass rebuilds against the *new* opportunity list rather than leaving rows keyed to symbols
    that no longer appear."""
    assert "fundamentals" in HARVEST_DATASETS
    s = _settings(tmp_path)
    store = harvest_store(s)
    _opportunities(store, DAY, "AAAA")
    run_fundamentals(FakeShares(answers={"AAAA": 1}), store, s, [DAY])
    assert plan_fundamentals(store) == []

    discard_partial(store, DAY)
    assert plan_fundamentals(store) == []  # the opportunities went too — nothing to enrich yet
    _opportunities(store, DAY, "AAAA", "CCCC")
    assert plan_fundamentals(store) == [DAY]


def test_the_narrow_discard_leaves_the_harvested_bars_alone(tmp_path: Path) -> None:
    s = _settings(tmp_path)
    store = harvest_store(s)
    _opportunities(store, DAY, "AAAA")
    run_fundamentals(FakeShares(answers={"AAAA": 1}), store, s, [DAY])

    assert discard_partial_fundamentals(store, DAY) is True
    assert discard_partial_fundamentals(store, DAY) is False  # idempotent
    assert store.read("fundamentals", dt=DAY).is_empty()
    assert not store.read("opportunities", dt=DAY).is_empty()


def test_an_unharvested_date_writes_nothing_and_stays_pending(tmp_path: Path) -> None:
    s = _settings(tmp_path)
    store = harvest_store(s)
    result = harvest_fundamentals(FakeShares(), store, s, DAY)
    assert result.opportunities == 0 and result.complete
    assert store.read("fundamentals", dt=DAY).is_empty()


# ================================================================================================
# configuration + CLI
# ================================================================================================


def test_it_refuses_to_start_without_a_user_agent(tmp_path: Path) -> None:
    """Every request would 403. Refusing up front is the one failure here that cannot be mistaken
    for an SEC outage."""
    with pytest.raises(EdgarNotConfigured, match="HARVEST_EDGAR_USER_AGENT"):
        edgar_source(_settings(tmp_path, harvest_edgar_user_agent="   "))
    src = edgar_source(_settings(tmp_path, harvest_edgar_user_agent=" scs a@b.com "))
    assert src.user_agent == "scs a@b.com"


def _cli(monkeypatch: Any, s: Settings, argv: list[str]) -> int:
    monkeypatch.setattr(cli_mod, "get_settings", lambda: s)
    monkeypatch.setattr(cli_mod, "configure_logging", lambda **_: None)
    return cli_mod.main(argv)


def _json_out(capsys: Any) -> Any:
    out = capsys.readouterr().out
    start = out.index("\n{\n") + 1 if "\n{\n" in out else out.index("{")
    return json.loads(out[start:])


def test_cli_fundamentals_fills_the_backlog_and_reports_what_is_left(
    monkeypatch: Any, tmp_path: Path, capsys: Any
) -> None:
    s = _settings(tmp_path)
    store = harvest_store(s)
    _opportunities(store, DAY, "AAAA", "ZZZZ")
    _opportunities(store, PREV, "AAAA")
    monkeypatch.setattr(cli_mod, "edgar_source", lambda _s: FakeShares(answers={"AAAA": 9}))

    assert _cli(monkeypatch, s, ["fundamentals", "--limit", "1"]) == 0
    payload = _json_out(capsys)
    assert payload["dates"] == 1
    assert (payload["with_shares"], payload["without_shares"]) == (1, 1)
    assert payload["remaining"] == 1  # --limit took the newest; PREV is still pending


def test_cli_status_counts_the_dates_still_missing_share_counts(
    monkeypatch: Any, tmp_path: Path, capsys: Any
) -> None:
    s = _settings(tmp_path)
    _opportunities(harvest_store(s), DAY, "AAAA")
    assert _cli(monkeypatch, s, ["status", "--today", "2026-07-10"]) == 0
    assert _json_out(capsys)["fundamentals_pending"] == 1


def test_a_missing_user_agent_can_never_fail_a_night_that_spent_vendor_budget(
    tmp_path: Path, capsys: Any
) -> None:
    """The enrichment runs after the night's stop condition has already fired. A free extra must
    not be able to turn a harvested night into a failed one."""
    s = _settings(tmp_path, harvest_edgar_user_agent="")
    store = harvest_store(s)
    _opportunities(store, DAY, "AAAA")
    out = cli_mod._fill_fundamentals(s, store, [DAY])
    assert out["dates"] == 0 and "EdgarNotConfigured" in out["error"]
    assert "HARVEST_EDGAR_USER_AGENT" in capsys.readouterr().err
