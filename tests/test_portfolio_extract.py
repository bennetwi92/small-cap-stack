"""Turning the store into candidate trades (#230): `extract_day_trades` and the payload.

Split out of `test_portfolio.py` in #529. Store integration — the pre-market window, the price
band, the symbol exclusions, ordering, the trade-log context (#390), the **collected-never-gated**
invariant (#551/#554: the book takes a high-float name and a no-news name), and the per-day
candidate cache, which is here rather than with the ledgers because what it caches is extraction.
"""

from __future__ import annotations

from datetime import date, datetime, time
from pathlib import Path

import pytest

from small_cap_stack.portfolio import (
    CandidateTrade,
    simulate_portfolio,
)
from tests.support import (
    ET,
    ET_UTC,
)
from tests.support import (
    candidate as _cand,
)
from tests.support import (
    et_bar as _bar,
)
from tests.support import (
    portfolio_settings as _s,
)
from tests.support import (
    seed_premarket as _seed_premarket,
)


def test_extract_day_trades_selects_premarket_v2_setup(tmp_path: Path) -> None:
    from small_cap_stack.portfolio import extract_day_trades, simulate_portfolio
    from small_cap_stack.storage import Store

    day = date(2026, 6, 29)
    store = Store(tmp_path)
    _seed_premarket(store, oid_time_utc=datetime(2026, 6, 29, 12, 0, tzinfo=ET_UTC))  # 08:00 ET

    cands = extract_day_trades(store, _s(), day)
    assert len(cands) == 1
    c = cands[0]
    assert c.symbol == "AZI"
    assert c.entry_fill == 6.13 and 1.0 <= c.entry_fill <= 20.0
    assert c.trigger_at.astimezone(ET).time() < time(9, 30)  # strictly pre-market

    res = simulate_portfolio([(day, cands)], _s(), target_r=2.0)
    assert res.n_trades == 1
    t = res.trades[0]
    assert t.reason == "target" and t.realized_r == 2.0
    assert t.qty == 40  # floor(250 / 6.13)
    assert res.end_equity > res.start_equity  # a winning day


def test_extract_day_trades_rejects_in_session(tmp_path: Path) -> None:
    from small_cap_stack.portfolio import extract_day_trades
    from small_cap_stack.storage import Store

    day = date(2026, 6, 29)
    store = Store(tmp_path)
    _seed_premarket(store, oid_time_utc=datetime(2026, 6, 29, 16, 0, tzinfo=ET_UTC))  # 12:00 ET
    assert extract_day_trades(store, _s(), day) == []  # same setup, but the trigger is in-session


def test_extract_day_trades_rejects_after_0915_cutoff(tmp_path: Path) -> None:
    """The final pre-open ramp 09:15–09:30 trades like the open and is excluded (#383).

    Reproduces the 2026-07-20 VMAR case: a setup whose trigger bar opens at 09:15 ET qualified
    under the old 09:30 cutoff but is rejected by the tightened 09:15 default (strict `<`). The
    `first_hit` bar (index 0) is seeded at 09:00 ET so the run's trigger (idx 3) lands at 09:15."""
    from small_cap_stack.portfolio import extract_day_trades
    from small_cap_stack.storage import Store

    day = date(2026, 6, 29)
    store = Store(tmp_path)
    _seed_premarket(store, oid_time_utc=datetime(2026, 6, 29, 13, 0, tzinfo=ET_UTC))  # 09:00 ET

    # Trigger opens 09:15 ET — at the cutoff, so rejected by the 09:15 default (not < 09:15).
    assert extract_day_trades(store, _s(), day) == []
    # ...but it is a valid setup: relaxing the cutoff back to 09:30 lets it through.
    cands = extract_day_trades(store, _s(select_window_end=time(9, 30)), day)
    assert [c.symbol for c in cands] == ["AZI"]
    assert cands[0].trigger_at.astimezone(ET).time() == time(9, 15)


def test_extract_day_trades_takes_the_earliest_premarket_tape(tmp_path: Path) -> None:
    """The floor is OPEN — a 05:15 ET trigger is takeable (#569, reversing #405's 05:30).

    Measured before the reversal: the earlier floor adds 4 trades over 30 sessions, all stop-outs,
    for −4.69R. Taken anyway — n=4 is not evidence, and the floor it replaces was not a measured
    edge either. A floor dialled back in still rejects it, so it is the setting doing the work and
    not a broken fixture."""
    from small_cap_stack.portfolio import extract_day_trades
    from small_cap_stack.storage import Store

    day = date(2026, 6, 29)
    store = Store(tmp_path)
    _seed_premarket(store, oid_time_utc=datetime(2026, 6, 29, 9, 0, tzinfo=ET_UTC))  # 05:00 ET

    cands = extract_day_trades(store, _s(), day)  # trigger lands 05:15
    assert [c.symbol for c in cands] == ["AZI"]
    assert cands[0].trigger_at.astimezone(ET).time() == time(5, 15)

    # The control: re-impose a floor above it and the same setup drops out.
    assert extract_day_trades(store, _s(select_window_start=time(5, 30)), day) == []


def test_extract_day_trades_floor_is_inclusive(tmp_path: Path) -> None:
    """The window is [start, end): a trigger opening exactly ON the floor is takeable.

    Pins the boundary convention against the cutoff's strict `<`. Uses an explicit floor rather
    than the default, so it keeps testing the convention if the default moves again.
    """
    from small_cap_stack.portfolio import extract_day_trades
    from small_cap_stack.storage import Store

    day = date(2026, 6, 29)
    store = Store(tmp_path)
    _seed_premarket(store, oid_time_utc=datetime(2026, 6, 29, 9, 15, tzinfo=ET_UTC))  # 05:15 ET

    cands = extract_day_trades(store, _s(select_window_start=time(5, 30)), day)
    assert [c.symbol for c in cands] == ["AZI"]
    assert cands[0].trigger_at.astimezone(ET).time() == time(5, 30)  # exactly on the floor


def test_extract_day_trades_rejects_entries_below_the_price_floor(tmp_path: Path) -> None:
    """The price floor rejects a sub-floor entry, and it is the floor doing the work.

    The floor has moved twice ($1 → $2 at #386, back to $1 at #608 for the collection phase), so
    this asserts the *mechanism* against an explicit floor rather than against whichever value
    happens to ship. The same setup is scaled below the floor, rejected, then accepted once the
    floor is dialled under it."""
    from small_cap_stack.portfolio import extract_day_trades
    from small_cap_stack.storage import Store

    day = date(2026, 6, 29)
    store = Store(tmp_path)
    # 0.25× the AZI setup → the $6.13 fill becomes ~$1.53.
    _seed_premarket(
        store, oid_time_utc=datetime(2026, 6, 29, 12, 0, tzinfo=ET_UTC), price_scale=0.25
    )

    assert extract_day_trades(store, _s(select_price_min=2.0), day) == []
    cands = extract_day_trades(store, _s(select_price_min=1.0), day)
    assert [c.symbol for c in cands] == ["AZI"]
    assert 1.0 <= cands[0].entry_fill < 2.0


def test_the_shipped_price_band_is_the_collection_phase_band(tmp_path: Path) -> None:
    """#643 raised the floor to $3.00, completing the shrink #608 said it intended.

    #608 widened the band to the scanner's own $1–$50 on 2026-08-07 as a temporary collection-phase
    choice, explicitly to be reverted once the record could say where the floor belonged. Over 61
    sessions it now does: the $3 floor takes the book from −9.67R / $283.03 / 41.9% max DD to
    +12.65R / $791.40 / 18.8%, positive in both stores. The **cap** stays at $50 and is deliberately
    untouched — no candidate in the record has ever exceeded it, so it has never bound.

    Pinned because both halves are deliberate strategy decisions. A silent drift in the floor would
    erase the measured shrink; a silent narrowing of the cap would cost coverage for no measured
    gain."""
    s = _s()
    assert (s.select_price_min, s.select_price_max) == (3.0, 50.0)


def test_extract_day_trades_excludes_configured_symbols(tmp_path: Path) -> None:
    """ETFs mis-captured before the scanner's #226 ETF/ETN filter are dropped from the book.

    They're leveraged single-stock ETFs with no share float, so they were never Warrior candidates;
    the scanner no longer captures them but the stored opportunities remain. The exclude list drops
    them on-read. Matching is case-insensitive so a config typo can't leak one back in."""
    from small_cap_stack.portfolio import extract_day_trades
    from small_cap_stack.storage import Store

    day = date(2026, 6, 29)
    store = Store(tmp_path)
    _seed_premarket(store, oid_time_utc=datetime(2026, 6, 29, 12, 0, tzinfo=ET_UTC))  # 08:00 ET

    # The seeded AZI setup qualifies by default...
    assert [c.symbol for c in extract_day_trades(store, _s(), day)] == ["AZI"]
    # ...but is excluded when listed (case-insensitively).
    assert extract_day_trades(store, _s(portfolio_exclude_symbols=("azi",)), day) == []


def test_extract_day_trades_is_deterministic_and_totally_ordered(tmp_path: Path) -> None:
    """Repeated extraction over an unchanged store must be identical (#381).

    It wasn't: ``day_opportunities`` deduped with polars ``.unique(keep="first")`` without
    ``maintain_order=True``, so opportunity order permuted between runs. Candidates were then
    stable-sorted on ``trigger_at`` alone, so names triggering on the *same bar* inherited that
    arbitrary order — and ``portfolio_max_trades_per_day`` took a **different pair** whenever such a
    tie straddled the day's cap. The published ``portfolio.json`` could therefore change between
    rebuilds with no new data, which breaks the store-raw / compute-on-read guarantee.

    Three identical setups under different tickers all trigger on the same bar, so this fails
    loudly if either the dedup order or the tiebreak regresses."""
    from small_cap_stack.portfolio import extract_day_trades
    from small_cap_stack.storage import Store

    day = date(2026, 6, 29)
    store = Store(tmp_path)
    t0 = datetime(2026, 6, 29, 12, 0, tzinfo=ET_UTC)  # 08:00 ET
    for sym in ("MULL", "SNDU", "SNXX"):
        _seed_premarket(store, oid_time_utc=t0, symbol=sym)

    runs = [extract_day_trades(store, _s(), day) for _ in range(8)]

    # All three tie on trigger_at, so only the tiebreak can order them.
    assert len({c.trigger_at for c in runs[0]}) == 1
    fingerprint = [(c.symbol, c.seg_id, c.run, c.trigger_at, c.entry_price) for c in runs[0]]
    assert [f[0] for f in fingerprint] == ["MULL", "SNDU", "SNXX"]  # total order, by symbol
    for r in runs[1:]:
        assert [(c.symbol, c.seg_id, c.run, c.trigger_at, c.entry_price) for c in r] == fingerprint


# --- --- Trade-log context: float + what the setup offered (#390) ----------------------
#
# The log used to say only what the book *took*. These pin the three columns that say what was
# there to take: the name's float, the peak favourable excursion in R (Max R − R is the R left on
# the table), and that same peak as a plain move (which R hides whenever the stop is wide).


def test_extract_carries_float_and_the_peak_the_setup_offered(tmp_path: Path) -> None:
    from small_cap_stack.portfolio import extract_day_trades
    from small_cap_stack.storage import Store

    day = date(2026, 6, 29)
    store = Store(tmp_path)
    _seed_premarket(store, oid_time_utc=datetime(2026, 6, 29, 12, 0, tzinfo=ET_UTC))
    [c] = extract_day_trades(store, _s(), day)

    assert c.float_shares == 8_000_000  # merged off the seeded fmp fundamentals row
    # The seeded flag runs to a 7.64 high off a 6.13 fill with a 5.60 stop.
    assert c.max_r == pytest.approx((7.64 - c.entry_price) / c.risk, abs=0.001)
    assert c.max_r > 2.0  # ...i.e. it offered more than the 2R the book's target takes
    # Same peak, as a plain move off entry — a fraction, like every other _pct in this payload.
    assert c.max_gain_pct == pytest.approx((7.64 - c.entry_price) / c.entry_price, abs=1e-5)


def test_extract_float_is_none_when_no_fundamentals_landed(tmp_path: Path) -> None:
    """A missing float must read as unknown, not as 0 — the log renders "—" for it."""
    from small_cap_stack.portfolio import extract_day_trades
    from small_cap_stack.storage import Store

    store = Store(tmp_path)
    _seed_premarket(
        store, oid_time_utc=datetime(2026, 6, 29, 12, 0, tzinfo=ET_UTC), float_shares=None
    )
    [c] = extract_day_trades(store, _s(), date(2026, 6, 29))
    assert c.float_shares is None
    assert c.max_r is not None  # the peak does not depend on fundamentals landing


# --- the "collected, never gated" invariant (#551/#554) ----------------------------------
#
# `research/strategy.md` §4 states, as the spec's central claim, that float and news are collected
# and never gated. That section is hand-written prose inside `strategy_doc.py`'s renderer — the
# generator guarantees the *numbers* track Settings, and guarantees nothing about the claim. Before
# these tests you could add a float filter to `_qualify` and no test would fail while the spec went
# on printing "No." Eight surfaces asserted a `float < 20M` filter the engine has never applied,
# and two published reports argued about it.


def test_the_book_takes_a_high_float_name(tmp_path: Path) -> None:
    """⚠️ FLOAT IS NOT A GATE. If you added one and this failed, DELETE THIS TEST — deliberately.

    246,000,000 is CLSK, which is in the published book at 12x `float_max_shares`. That setting's
    only consumer is `gates.py::float_gate`, whose only caller is the EOD report's `float_ok`
    count; nothing in the selection path reads it.

    If float should ever gate, the check goes in `portfolio.extract._qualify` — and then this test
    comes out and `research/strategy.md` §4 changes in the same PR. That is the whole point: the
    invariant is a decision someone makes, not an accident nobody notices.
    """
    from small_cap_stack.portfolio import extract_day_trades
    from small_cap_stack.storage import Store

    store = Store(tmp_path)
    _seed_premarket(
        store, oid_time_utc=datetime(2026, 6, 29, 12, 0, tzinfo=ET_UTC), float_shares=246_000_000
    )
    cands = extract_day_trades(store, _s(), date(2026, 6, 29))

    assert cands, (
        "a 246M-float setup was dropped from the book, so float has become a gate. If that is "
        "intended: delete this test and change research/strategy.md §4 in the same PR."
    )
    [c] = cands
    assert c.float_shares == 246_000_000  # carried as context...
    assert c.max_r is not None  # ...and the setup is still fully measured and takeable


def test_the_book_takes_a_name_with_no_news(tmp_path: Path) -> None:
    """⚠️ NEWS IS NOT A GATE either. Same contract as the float test above.

    The original brief made "breaking news on the stock" a hard requirement. It never shipped as
    one: `extract.py` does not read the `news` dataset at all, and `gates.py::news_gate` feeds only
    the EOD report's `with_recent_news` count.
    """
    from small_cap_stack.portfolio import extract_day_trades
    from small_cap_stack.storage import Store

    store = Store(tmp_path)
    _seed_premarket(store, oid_time_utc=datetime(2026, 6, 29, 12, 0, tzinfo=ET_UTC))
    assert store.read("news").is_empty()  # nothing seeded a headline

    [c] = extract_day_trades(store, _s(), date(2026, 6, 29))
    assert c.max_r is not None


def test_news_rows_do_not_change_which_candidates_the_book_takes(tmp_path: Path) -> None:
    """The stronger half: news present or absent, the book extracts the same trades.

    Catches a news read entering by the back door as well as an explicit gate — and note that
    `payload._EXTRACT_DATASETS` deliberately omits `news`, so adding one would also silently bust
    every cached day's candidate fingerprint.
    """
    from small_cap_stack.portfolio import extract_day_trades
    from small_cap_stack.storage import Store

    day = date(2026, 6, 29)
    seeded_at = datetime(2026, 6, 29, 12, 0, tzinfo=ET_UTC)

    quiet = Store(tmp_path / "quiet")
    _seed_premarket(quiet, oid_time_utc=seeded_at)

    loud = Store(tmp_path / "loud")
    _seed_premarket(loud, oid_time_utc=seeded_at)
    loud.append(
        "news",
        [
            {
                "opportunity_id": f"{day.isoformat()}:AZI",
                "symbol": "AZI",
                "time": "2026-06-29 08:00:00",
                "ts_utc": seeded_at,
                "provider": "DJ-N",
                "headline": "AZI announces something material",
                "article_id": "a1",
            }
        ],
        partition_date=day,
    )
    assert loud.read("news").height == 1

    key = lambda c: (c.symbol, c.entry_price, c.stop, c.max_r)  # noqa: E731
    assert [key(c) for c in extract_day_trades(loud, _s(), day)] == [
        key(c) for c in extract_day_trades(quiet, _s(), day)
    ]


def test_taken_and_skipped_trades_both_carry_the_peak_and_float() -> None:
    """Both logs answer the same question, so both need the same columns.

    Max R is a property of the *candidate* — measured against the initial stop over the rest of the
    day — so it must survive the target sweep unchanged. That is what makes ``max_r - realized_r``
    read as "what this exit left on the table" rather than as a second exit model."""
    s = _s(portfolio_max_trades_per_day=1, portfolio_exit_slippage_ticks=0)
    bars = [_bar(10, 12.0, 9.95, 12.0)]  # +2R available on the entry bar
    taken = _cand("AAA", 5, 10.0, 9.0, bars, float_shares=6_000_000, max_r=2.8, max_gain_pct=0.28)
    dropped = _cand("BBB", 6, 10.0, 9.0, bars, float_shares=None, max_r=5.0, max_gain_pct=0.5)

    res = simulate_portfolio([(date(2026, 7, 14), [taken, dropped])], s, target_r=2.0)
    [t] = res.trades
    assert (t.symbol, t.float_shares, t.max_r, t.max_gain_pct) == ("AAA", 6_000_000, 2.8, 0.28)
    assert round(t.max_r - t.realized_r, 4) == 0.8  # 0.8R left on the table at this target
    [sk] = res.skipped
    assert (sk.symbol, sk.float_shares, sk.max_r, sk.max_gain_pct) == ("BBB", None, 5.0, 0.5)

    # A different target changes what was TAKEN but not what was OFFERED.
    wider = simulate_portfolio([(date(2026, 7, 14), [taken, dropped])], s, target_r=1.0)
    assert wider.trades[0].realized_r == 1.0
    assert wider.trades[0].max_r == 2.8


def test_payload_trade_log_exposes_the_peak_and_float(tmp_path: Path) -> None:
    from small_cap_stack.portfolio import build_portfolio_payload
    from small_cap_stack.storage import Store

    store = Store(tmp_path)
    _seed_premarket(store, oid_time_utc=datetime(2026, 6, 29, 12, 0, tzinfo=ET_UTC))
    payload = build_portfolio_payload(store, _s(), datetime(2026, 6, 30, 12, 0, tzinfo=ET_UTC))
    trade = payload["books"]["adaptive"]["trades"][0]  # type: ignore[index,call-overload]

    assert trade["float_shares"] == 8_000_000
    assert trade["max_r"] > trade["realized_r"]  # exited at target; the move kept going
    assert 0 < trade["max_pct"] < 1  # a fraction, not already multiplied out to percent


def test_late_fundamentals_bust_the_candidate_cache(tmp_path: Path) -> None:
    """The EOD fundamentals backfill lands a float on a day whose bars are already final (#255).

    ``_EXTRACT_DATASETS`` must therefore list ``fundamentals``: without it the day's fingerprint
    is unchanged by that write, and the cache serves a null-float candidate forever."""
    import small_cap_stack.portfolio as pf
    from small_cap_stack.storage import Store

    day = date(2026, 6, 29)
    store = Store(tmp_path)
    _seed_premarket(
        store, oid_time_utc=datetime(2026, 6, 29, 12, 0, tzinfo=ET_UTC), float_shares=None
    )
    now = datetime(2026, 6, 30, 12, 0, tzinfo=ET_UTC)
    cache_dir = pf.portfolio_candidate_cache_dir(_s(data_dir=tmp_path))
    primed = pf.build_portfolio_payload(store, _s(), now, cache_dir=cache_dir)
    assert primed["books"]["adaptive"]["trades"][0]["float_shares"] is None  # type: ignore[index,call-overload]

    store.append(
        "fundamentals",
        [
            {
                "opportunity_id": f"{day.isoformat()}:AZI",
                "symbol": "AZI",
                "ts_utc": datetime(2026, 6, 29, 20, 0, tzinfo=ET_UTC),
                "float_shares": 4_200_000,
                "shares_outstanding": 9_000_000,
                "short_percent": 0.1,
                "source": "fmp",
            }
        ],
        partition_date=day,
    )
    rebuilt = pf.build_portfolio_payload(store, _s(), now, cache_dir=cache_dir)
    assert rebuilt["books"]["adaptive"]["trades"][0]["float_shares"] == 4_200_000  # type: ignore[index,call-overload]


def test_cache_written_before_the_peak_fields_is_rejected(tmp_path: Path) -> None:
    """An older cache file must re-extract, not silently serve nulls for the new columns."""
    import json

    from small_cap_stack.portfolio import (
        _candidate_to_json,
        _read_candidate_cache,
        extract_day_trades,
    )
    from small_cap_stack.storage import Store

    store = Store(tmp_path)
    _seed_premarket(store, oid_time_utc=datetime(2026, 6, 29, 12, 0, tzinfo=ET_UTC))
    [c] = extract_day_trades(store, _s(), date(2026, 6, 29))

    legacy = _candidate_to_json(c)
    for key in ("float_shares", "max_r", "max_gain_pct"):
        legacy.pop(key)
    path = tmp_path / "legacy.json"
    path.write_text(json.dumps({"fingerprint": "fp", "candidates": [legacy]}))

    assert _read_candidate_cache(path, "fp") is None  # schema drift → re-extract


def test_build_portfolio_payload_shape(tmp_path: Path) -> None:
    from small_cap_stack.portfolio import build_portfolio_payload
    from small_cap_stack.storage import Store

    store = Store(tmp_path)
    _seed_premarket(store, oid_time_utc=datetime(2026, 6, 29, 12, 0, tzinfo=ET_UTC))  # 08:00 ET
    payload = build_portfolio_payload(store, _s(), datetime(2026, 6, 30, 12, 0, tzinfo=ET_UTC))

    assert payload["start_equity"] == 500.0
    assert payload["gbpusd_rate"] == 1.27  # top-level FX rate for the take-home panel
    assert "adaptive" in payload["books"]
    assert set(payload["targets"]) >= {"1.5", "2", "3"}  # grid widened with extremes
    adaptive = payload["books"]["adaptive"]
    assert adaptive["stats"]["n_trades"] == 1
    assert "daily_targets" in adaptive  # only the adaptive book carries the per-day target
    assert "daily_targets" not in payload["books"]["2"]  # fixed books do not
    # Getting-paid layer flows through the payload: stats, a cash-flow schedule, and config knobs.
    assert "net_take_home_gbp" in adaptive["stats"]
    assert "withdrawals_gbp" in adaptive["stats"] and "tax_paid_gbp" in adaptive["stats"]
    assert "cash_flows" in adaptive
    assert "withdraw_fraction" in payload["config"] and "cgt_rate" in payload["config"]
    # The target chart draws its rules from the *adaptive grid*, which the widened `targets` book
    # list can't stand in for — 4R/5R are selectable books the daily re-fit can never choose.
    assert payload["config"]["target_grid"] == [1.5, 2.0, 2.5, 3.0]
    assert payload["config"]["target_fallback_r"] == 2.0
    # The forward projection rides along per book (see `portfolio.projection`), and the page needs
    # the day-rate knobs from config to state its own comparison rather than hard-coding one.
    assert adaptive["projection"]["available"] is True
    assert "projection" in payload["books"]["2"]
    assert payload["config"]["day_rate_gbp"] == 800.0
    assert payload["config"]["day_rate_net_annual_gbp"] == pytest.approx(91520.0)
    assert {t for _d, t in [(d["date"], d["target"]) for d in adaptive["daily_targets"]]} <= set(
        payload["config"]["target_grid"] + [payload["config"]["target_fallback_r"]]
    )
    # Every plotted day says whether the optimiser ran or the fallback stood in (#463) — a flat
    # target line is otherwise indistinguishable from a re-fit that never fired.
    day = adaptive["daily_targets"][0]
    assert day["fitted"] is False and day["n"] == 0  # one seeded day: nothing trailing to fit on
    # ...and WHY it fell back: no samples, not a failed margin gate (#476).
    assert day["status"] == "thin"
    assert payload["config"]["adaptive_window_days"] is None  # all history, not a trailing window
    assert payload["config"]["target_switch_z"] == 1.0
    trade = adaptive["trades"][0]
    assert trade["symbol"] == "AZI" and trade["reason"] == "target"
    # Per-trade risk attribution + the next-session state reach the page (#286).
    assert {"risk_fraction", "risk_usd", "risk_pct", "sized_by"} <= set(trade)
    assert trade["sized_by"] in {"risk", "cap"}
    assert trade["risk_pct"] <= payload["config"]["risk_fraction"]
    assert adaptive["stats"]["avg_risk_pct"] is not None
    assert "cap_bound_count" in adaptive["stats"]
    state = adaptive["next_session"]
    assert state["as_of"] == "2026-06-30"  # the day after the last collected one
    assert state["risk_fraction"] in payload["config"]["risk_ladder"]
    assert state["risk_budget_usd"] == round(
        adaptive["stats"]["end_equity"] * state["risk_fraction"], 4
    )
    # The target in force is published with its provenance, so the page can say "fallback" rather
    # than presenting it as an adaptive choice (#463).
    assert state["target_fitted"] is False and state["target_trailing_n"] == 1
    assert state["target_status"] == "thin" and state["target_considered_r"] is None
    # Only the adaptive book throttles risk / re-fits a target, so only it carries the state.
    assert "next_session" not in payload["books"]["2"]
    # Skipped log rides along in every book (empty here — a single seeded setup never hits the cap).
    assert "skipped" in adaptive and adaptive["skipped"] == []
    assert adaptive["stats"]["skipped_count"] == 0 and adaptive["stats"]["skipped_total_r"] == 0.0
    # fully JSON-serialisable (dates/datetimes already stringified)
    import json

    json.dumps(payload)


# --- --- Per-day candidate cache (backfill-dashboard-perf) ----------------------------
#
# The cache exists so a single-date dashboard backfill re-extracts only the changed day instead of
# re-doing the whole cross-day archive. These pin: serialisation fidelity, that a cache hit skips
# extraction entirely, and that a settings change / new partition / force_dates all bust it.


def test_candidate_json_round_trips_exactly(tmp_path: Path) -> None:
    from small_cap_stack.portfolio import (
        _candidate_from_json,
        _candidate_to_json,
        extract_day_trades,
    )
    from small_cap_stack.storage import Store

    day = date(2026, 6, 29)
    store = Store(tmp_path)
    _seed_premarket(store, oid_time_utc=datetime(2026, 6, 29, 12, 0, tzinfo=ET_UTC))  # 08:00 ET
    [c] = extract_day_trades(store, _s(), day)
    # Frozen-dataclass equality covers every field incl. the full bar tuple + tz-aware datetimes.
    assert _candidate_from_json(_candidate_to_json(c)) == c


def test_cache_matches_uncached_and_writes_file(tmp_path: Path) -> None:
    from small_cap_stack.portfolio import build_portfolio_payload, portfolio_candidate_cache_dir
    from small_cap_stack.storage import Store

    store = Store(tmp_path)
    _seed_premarket(store, oid_time_utc=datetime(2026, 6, 29, 12, 0, tzinfo=ET_UTC))
    now = datetime(2026, 6, 30, 12, 0, tzinfo=ET_UTC)
    cache_dir = portfolio_candidate_cache_dir(_s(data_dir=tmp_path))

    plain = build_portfolio_payload(store, _s(), now)
    cached = build_portfolio_payload(store, _s(), now, cache_dir=cache_dir)
    assert cached["books"] == plain["books"]  # identical book, just cheaper
    assert (cache_dir / "2026-06-29.json").exists()  # the day was persisted


def test_cache_hit_skips_extraction(tmp_path: Path, monkeypatch: object) -> None:
    import small_cap_stack.portfolio as pf
    from small_cap_stack.storage import Store

    store = Store(tmp_path)
    _seed_premarket(store, oid_time_utc=datetime(2026, 6, 29, 12, 0, tzinfo=ET_UTC))
    now = datetime(2026, 6, 30, 12, 0, tzinfo=ET_UTC)
    cache_dir = pf.portfolio_candidate_cache_dir(_s(data_dir=tmp_path))
    primed = pf.build_portfolio_payload(store, _s(), now, cache_dir=cache_dir)

    def _boom(*a: object, **k: object) -> list[CandidateTrade]:
        raise AssertionError("extract_day_trades must not run on a cache hit")

    # Patched where it is LOOKED UP. `pf.extract_day_trades` is a re-export binding — payload.py
    # resolves its own global, so patching the facade is a silent no-op and this test would pass
    # while proving nothing (#259).
    monkeypatch.setattr(pf.payload, "extract_day_trades", _boom)  # type: ignore[attr-defined]

    # Positive control FIRST: a cache miss must reach the patched function. Without this, "the spy
    # never fired" is satisfied both by a working cache and by a patch that never took hold.
    with pytest.raises(AssertionError, match="must not run on a cache hit"):
        pf.build_portfolio_payload(store, _s(), now, cache_dir=tmp_path / "empty-cache")

    # Same store + settings → matching fingerprint → served entirely from cache, no extraction.
    served = pf.build_portfolio_payload(store, _s(), now, cache_dir=cache_dir)
    assert served["books"] == primed["books"]


def test_cache_busted_by_settings_change(tmp_path: Path, monkeypatch: object) -> None:
    import small_cap_stack.portfolio as pf
    from small_cap_stack.storage import Store

    store = Store(tmp_path)
    _seed_premarket(store, oid_time_utc=datetime(2026, 6, 29, 12, 0, tzinfo=ET_UTC))
    now = datetime(2026, 6, 30, 12, 0, tzinfo=ET_UTC)
    cache_dir = pf.portfolio_candidate_cache_dir(_s(data_dir=tmp_path))
    pf.build_portfolio_payload(store, _s(), now, cache_dir=cache_dir)

    calls: list[date] = []
    real = pf.extract_day_trades

    def _spy(store: object, s: object, d: date, *, source: str = "live") -> list[CandidateTrade]:
        calls.append(d)
        return real(store, s, d)  # type: ignore[arg-type]

    monkeypatch.setattr(pf.payload, "extract_day_trades", _spy)  # patched where it's used
    # A settings change flips the fingerprint, so the cached day must be re-extracted (correctness).
    pf.build_portfolio_payload(
        store, _s(portfolio_exclude_symbols=("ZZZZ",)), now, cache_dir=cache_dir
    )
    assert date(2026, 6, 29) in calls


def test_cache_busted_by_new_partition(tmp_path: Path, monkeypatch: object) -> None:
    import small_cap_stack.portfolio as pf
    from small_cap_stack.storage import Store

    day = date(2026, 6, 29)
    store = Store(tmp_path)
    _seed_premarket(store, oid_time_utc=datetime(2026, 6, 29, 12, 0, tzinfo=ET_UTC))
    now = datetime(2026, 6, 30, 12, 0, tzinfo=ET_UTC)
    cache_dir = pf.portfolio_candidate_cache_dir(_s(data_dir=tmp_path))
    pf.build_portfolio_payload(store, _s(), now, cache_dir=cache_dir)

    # A late backfill lands a new bars part file for the day → the fingerprint must change.
    store.append(
        "bars",
        [
            {
                "opportunity_id": f"{day.isoformat()}:AZI",
                "symbol": "AZI",
                "bar_start_utc": datetime(2026, 6, 29, 12, 20, tzinfo=ET_UTC),
                "open": 7.5,
                "high": 7.6,
                "low": 7.4,
                "close": 7.5,
                "volume": 1000.0,
            }
        ],
        partition_date=day,
    )
    calls: list[date] = []
    real = pf.extract_day_trades

    def _spy(store: object, s: object, d: date, *, source: str = "live") -> list[CandidateTrade]:
        calls.append(d)
        return real(store, s, d)  # type: ignore[arg-type]

    monkeypatch.setattr(pf.payload, "extract_day_trades", _spy)  # patched where it's used
    pf.build_portfolio_payload(store, _s(), now, cache_dir=cache_dir)
    assert day in calls  # stale cache detected via the changed partition file set


def test_force_dates_bypasses_cache(tmp_path: Path, monkeypatch: object) -> None:
    import small_cap_stack.portfolio as pf
    from small_cap_stack.storage import Store

    day = date(2026, 6, 29)
    store = Store(tmp_path)
    _seed_premarket(store, oid_time_utc=datetime(2026, 6, 29, 12, 0, tzinfo=ET_UTC))
    now = datetime(2026, 6, 30, 12, 0, tzinfo=ET_UTC)
    cache_dir = pf.portfolio_candidate_cache_dir(_s(data_dir=tmp_path))
    pf.build_portfolio_payload(store, _s(), now, cache_dir=cache_dir)  # prime a valid cache

    calls: list[date] = []
    real = pf.extract_day_trades

    def _spy(store: object, s: object, d: date, *, source: str = "live") -> list[CandidateTrade]:
        calls.append(d)
        return real(store, s, d)  # type: ignore[arg-type]

    monkeypatch.setattr(pf.payload, "extract_day_trades", _spy)  # patched where it's used
    # force_dates re-extracts even on a valid cache (the day whose raw data the caller just changed)
    pf.build_portfolio_payload(store, _s(), now, cache_dir=cache_dir, force_dates={day})
    assert calls == [day]
