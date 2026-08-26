"""Selection bookkeeping (#251/#256) and the reconstructed-history payload (#430/#488).

Split out of `test_portfolio.py` in #529. Every candidate has to leave by exactly one door — taken,
cap-skipped, throttled, or unaffordable — and the four are deliberately not interchangeable, since
the page asks what the *cap* cost. Then the recon half: a second store spliced into `books_all`
while `books` stays byte-identical, with provenance on every row.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from small_cap_stack.portfolio import (
    _select_day,
    _take_day,
    simulate_portfolio,
    simulate_portfolio_adaptive,
    size_position,
)
from tests.support import (
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

# --- unaffordable setups + selection source-of-truth (#251, #256) -------------------------------


def test_unaffordable_setup_is_recorded_not_silently_dropped() -> None:
    """A selected setup the book can't size to one share must not vanish from every log (#251).

    It used to `continue` past both `trades` and `skipped`. Needs a tiny equity to reach — at the
    default $500 book both cap_qty and risk_qty stay >= 1 unless equity falls to ~$40.
    """
    win = [_bar(10, 12.5, 9.95, 12.3)]
    cands = [_cand("AAA", 5, 10.0, 9.0, win)]
    s = _s(portfolio_start_equity_usd=5.0)  # can't afford a single $10 share

    res = simulate_portfolio([(date(2026, 7, 14), cands)], s, target_r=2.0)

    assert res.n_trades == 0  # not taken...
    assert [(sk.symbol, sk.skip_reason) for sk in res.skipped] == [("AAA", "unaffordable")]
    # ...and it does NOT pollute "what did the daily cap cost me?" — different question.
    assert res.skipped_total_r == 0.0


def test_cap_dropped_setups_are_tagged_cap() -> None:
    win = [_bar(10, 12.5, 9.95, 12.3)]
    loss = [_bar(10, 10.1, 8.9, 9.0)]
    cands = [
        _cand("AAA", 5, 10.0, 9.0, win),
        _cand("BBB", 6, 10.0, 9.0, win),
        _cand("CCC", 7, 10.0, 9.0, loss),  # 3rd by trigger time -> cap drops it
    ]
    res = simulate_portfolio(
        [(date(2026, 7, 14), cands)], _s(portfolio_max_trades_per_day=2), target_r=2.0
    )

    assert [(sk.symbol, sk.skip_reason) for sk in res.skipped] == [("CCC", "cap")]
    assert res.skipped_total_r == res.skipped[0].realized_r  # cap-only headline still counts it


def test_throttled_sitout_is_logged_as_throttled_not_cap_or_unaffordable() -> None:
    """rung-0 (risk_fraction=0) sizes every position to 0 on purpose — the kill-switch sitting the
    day out. Attributing that to the cap or to the equity would misname the constraint (#251), but
    logging nothing at all deleted the setup from every view the page has (#465)."""
    win = [_bar(10, 12.5, 9.95, 12.3)]
    cands = [_cand("AAA", 5, 10.0, 9.0, win), _cand("BBB", 6, 10.0, 9.0, win)]

    trades, skipped = _take_day(cands, 500.0, _s(), 2.0, 0.0, risk_fraction=0.0)

    assert trades == []
    assert [(sk.symbol, sk.skip_reason) for sk in skipped] == [
        ("AAA", "throttled"),
        ("BBB", "throttled"),
    ]


def test_take_day_selection_follows_select_day(monkeypatch: pytest.MonkeyPatch) -> None:
    """_take_day must derive its taken set FROM _select_day, not re-slice inline (#256).

    Asserting the two agree proves nothing — they agree by construction, which is why the bug was
    invisible. The invariant that matters is that they *cannot diverge*, so change what _select_day
    returns and require the trades to follow. An inline slice ignores the patch and takes 2.
    """
    win = [_bar(10, 12.5, 9.95, 12.3)]
    cands = [
        _cand("AAA", 5, 10.0, 9.0, win),
        _cand("BBB", 6, 10.0, 9.0, win),
        _cand("CCC", 7, 10.0, 9.0, win),
    ]
    s = _s(portfolio_max_trades_per_day=2)
    assert [c.symbol for c in _select_day(cands, s)] == ["AAA", "BBB"]  # earliest N by trigger

    # A selection rule the inline slice would never produce: one trade, not max_trades_per_day.
    monkeypatch.setattr(
        "small_cap_stack.portfolio.sim._select_day",
        lambda cands, s: sorted(cands, key=lambda c: c.trigger_at)[:1],
    )
    trades, skipped = _take_day(cands, 500.0, s, 2.0, 0.0)

    assert [t.symbol for t in trades] == ["AAA"]  # followed the selector...
    assert [sk.symbol for sk in skipped] == ["BBB", "CCC"]  # ...and the rest is the remainder


def test_throttled_rung_sizing_to_zero_is_not_called_unaffordable() -> None:
    """Any throttled rung can size to 0 on a wide stop — that's the ladder, not the equity (#251).

    Guarding on `rf > 0` only excluded rung 0. Rung 1 (rf=0.025) is a $12.50 risk budget at $500,
    so a $15/share-risk setup sizes to 0 while the book is perfectly healthy — and telling the
    trader it was "unaffordable" blames their equity for what the kill-switch did.
    """
    wide = [_bar(10, 21.0, 4.0, 20.0)]  # entry 20, stop 5 -> $15/share risk
    cands = [_cand("AAA", 5, 20.0, 5.0, wide)]
    s = _s()
    assert size_position(500.0, 20.0, 5.0, risk_fraction=0.025, max_position_fraction=0.5).qty == 0

    trades, skipped = _take_day(cands, 500.0, s, 2.0, 0.0, risk_fraction=0.025)

    assert trades == []
    # Throttled, not unaffordable — the book could afford it at full risk. Recorded either way,
    # because a setup that is in neither log is a setup the page cannot show at all (#465).
    assert [(sk.symbol, sk.skip_reason) for sk in skipped] == [("AAA", "throttled")]


def test_unaffordable_still_recorded_at_full_risk() -> None:
    """The genuine case — full configured risk and still not one share — is still logged."""
    win = [_bar(10, 12.5, 9.95, 12.3)]
    cands = [_cand("AAA", 5, 10.0, 9.0, win)]
    s = _s()

    trades, skipped = _take_day(cands, 5.0, s, 2.0, 0.0)

    assert trades == []
    assert [(sk.symbol, sk.skip_reason) for sk in skipped] == [("AAA", "unaffordable")]


def test_every_candidate_leaves_by_exactly_one_door() -> None:
    """``taken + skipped == cands``, at every rung (#465).

    The accounting invariant behind the page: a qualifying setup is either in the trade log or in
    the skipped log, never in neither. Asserting it at each rung is the point — the hole was
    rung-specific, so a single-rung test would have passed throughout.
    """
    win = [_bar(10, 12.5, 9.95, 12.3)]
    wide = [_bar(10, 21.0, 4.0, 20.0)]  # $15/share risk: sizes to 0 at a throttled rung
    cands = [
        _cand("AAA", 5, 10.0, 9.0, win),
        _cand("BBB", 6, 20.0, 5.0, wide),
        _cand("CCC", 7, 10.0, 9.0, win),  # beyond the 2/day cap
    ]
    s = _s()

    for rf in (0.0, 0.025, 0.05):
        trades, skipped = _take_day(cands, 500.0, s, 2.0, 0.0, risk_fraction=rf)
        seen = [t.symbol for t in trades] + [sk.symbol for sk in skipped]
        assert sorted(seen) == ["AAA", "BBB", "CCC"], rf
        assert len(seen) == len(set(seen)), rf  # and never through two doors at once


def test_throttled_skips_stay_out_of_the_cap_headline() -> None:
    """``skipped_total_r`` / ``skipped_count`` answer "what did the N/day cap cost me?".

    Giving throttled setups their own reason (#465) is what lets them be recorded without being
    counted here — the exact conflation the rung-0 silence was avoiding.
    """
    win = [_bar(10, 12.5, 9.95, 12.3)]
    s = _s(
        portfolio_risk_step_days=1, portfolio_adaptive_min_samples=999, portfolio_risk_rungs=2
    )  # binary kill-switch: one losing day parks the book at 0%
    base = date(2026, 7, 1)
    days = [
        (base, [_cand("L0", 5, 10.0, 9.0, [_bar(10, 10.1, 8.9, 9.0)])]),  # lose -> park
        (base + timedelta(days=1), [_cand("W1", 5, 10.0, 9.0, win)]),  # parked: throttled
    ]

    res = simulate_portfolio_adaptive(days, s).result

    assert [(sk.symbol, sk.skip_reason) for sk in res.skipped] == [("W1", "throttled")]
    assert res.skipped_total_r == 0.0  # cap-only headline untouched by the throttle


def test_rung_zero_day_does_not_blame_the_daily_cap() -> None:
    """Nothing is taken on a rung-0 day, so the cap was never the binding constraint (#251).

    CCC would be past the 2/day cap, but with nothing traded the cap cost us nothing — so the whole
    day is the throttle's, including the candidates sitting beyond the cap. They are still recorded
    (#465); what must not happen is their landing in the cap population.
    """
    win = [_bar(10, 12.5, 9.95, 12.3)]
    cands = [_cand(x, i + 5, 10.0, 9.0, win) for i, x in enumerate(["AAA", "BBB", "CCC"])]

    trades, skipped = _take_day(cands, 500.0, _s(), 2.0, 0.0, risk_fraction=0.0)

    assert trades == []
    assert [sk.symbol for sk in skipped] == ["AAA", "BBB", "CCC"]
    assert {sk.skip_reason for sk in skipped} == {"throttled"}


def test_take_day_tolerates_a_non_prefix_selector(monkeypatch: pytest.MonkeyPatch) -> None:
    """`dropped` must be the complement of `taken`, not `ordered[len(taken):]` (#256 review).

    The positional form silently re-assumes _select_day returns a trigger-time prefix. Under a
    selector that skips a middle candidate it would log a *taken* trade as cap-dropped (double
    counting its R) and lose the genuinely dropped one from every log.
    """
    win = [_bar(10, 12.5, 9.95, 12.3)]
    cands = [_cand(x, i + 5, 10.0, 9.0, win) for i, x in enumerate(["AAA", "BBB", "CCC"])]
    monkeypatch.setattr(  # skip the middle one — a non-prefix selection
        "small_cap_stack.portfolio.sim._select_day",
        lambda cs, st: [c for c in sorted(cs, key=lambda c: c.trigger_at) if c.symbol != "BBB"],
    )

    trades, skipped = _take_day(cands, 500.0, _s(), 2.0, 0.0)

    assert [t.symbol for t in trades] == ["AAA", "CCC"]
    assert [sk.symbol for sk in skipped] == ["BBB"]  # the real drop, not CCC


def test_skipped_is_returned_in_trigger_order() -> None:
    """The page reverses this list for "newest first", so it must arrive in trigger order."""
    win = [_bar(10, 12.5, 9.95, 12.3)]
    # AAA (earliest) is unaffordable at full risk; DDD (latest) is dropped by the 2/day cap.
    cands = [_cand(x, i + 5, 10.0, 9.0, win) for i, x in enumerate(["AAA", "BBB", "CCC"])]
    s = _s(portfolio_max_trades_per_day=2, portfolio_start_equity_usd=20.0)

    _, skipped = _take_day(cands, 20.0, s, 2.0, 0.0)

    triggers = [sk.trigger_at for sk in skipped]
    assert triggers == sorted(triggers)


# --------------------------------------------------------------------------------------------
# Reconstructed history (#430) — a second store of days rebuilt from purchased vendor minute bars,
# spliced into the book as a *parallel* scope so the live Phase-1 record is never overwritten.
# --------------------------------------------------------------------------------------------


def _recon_payload(
    tmp_path: Path, *, live_day: datetime, recon_days: list[datetime], **settings: object
) -> dict:  # type: ignore[type-arg]
    """Seed a live store + a recon store and build the payload over both."""
    import small_cap_stack.portfolio as pf
    from small_cap_stack.storage import Store

    live = Store(tmp_path / "live")
    _seed_premarket(live, oid_time_utc=live_day)
    recon = Store(tmp_path / "recon")
    for d in recon_days:
        _seed_premarket(recon, oid_time_utc=d)
    return pf.build_portfolio_payload(
        live,
        _s(**settings),  # type: ignore[arg-type]
        datetime(2026, 6, 30, 12, 0, tzinfo=ET_UTC),
        recon_store=recon,
    )


def test_recon_store_absent_leaves_the_payload_untouched(tmp_path: Path) -> None:
    """The whole feature is inert until the harvest lands something (#430).

    A box that has never run the harvest must publish exactly what it published before — no second
    book set, and a coverage block whose reconstructed half is empty rather than missing."""
    import small_cap_stack.portfolio as pf
    from small_cap_stack.storage import Store

    store = Store(tmp_path / "live")
    _seed_premarket(store, oid_time_utc=datetime(2026, 6, 29, 12, 0, tzinfo=ET_UTC))
    now = datetime(2026, 6, 30, 12, 0, tzinfo=ET_UTC)

    plain = pf.build_portfolio_payload(store, _s(), now)
    # An empty recon store (the directory does not even exist) must be indistinguishable from none.
    empty = pf.build_portfolio_payload(
        store, _s(), now, recon_store=Store(tmp_path / "nothing-here")
    )

    assert "books_all" not in plain
    assert "books_all" not in empty
    assert plain["books"] == empty["books"]
    assert plain["coverage"]["recon"]["days"] == 0  # type: ignore[index,call-overload]
    assert plain["coverage"]["live"]["days"] == 1  # type: ignore[index,call-overload]


def test_recon_days_extend_the_combined_book_only(tmp_path: Path) -> None:
    """The deepening sample the harvest exists to produce — but in `books_all`, never in `books`.

    `books` is path-dependent twice over (the adaptive re-fit reads a trailing window; every
    position sizes off running equity), so splicing ~500 reconstructed days in front of the live
    ones would not extend the live record, it would replace it. The two are published side by
    side."""
    payload = _recon_payload(
        tmp_path,
        live_day=datetime(2026, 6, 29, 12, 0, tzinfo=ET_UTC),
        recon_days=[
            datetime(2026, 6, 25, 12, 0, tzinfo=ET_UTC),
            datetime(2026, 6, 26, 12, 0, tzinfo=ET_UTC),
        ],
    )

    live_trades = payload["books"]["adaptive"]["trades"]  # type: ignore[index,call-overload]
    all_trades = payload["books_all"]["adaptive"]["trades"]  # type: ignore[index,call-overload]

    # The live book is untouched: one seeded day, one trade, all of it live.
    assert len(live_trades) == 1
    assert {t["source"] for t in live_trades} == {"live"}
    # The combined book carries the reconstructed days too, and they sort *before* the live one.
    assert len(all_trades) == 3
    assert [t["source"] for t in all_trades] == ["recon", "recon", "live"]
    assert [t["date"] for t in all_trades] == ["2026-06-25", "2026-06-26", "2026-06-29"]


def test_live_wins_when_a_date_exists_in_both_stores(tmp_path: Path) -> None:
    """The #428 calibration days are exactly this overlap: harvested *and* watched live.

    Live is the ground truth the reconstruction is calibrated against, so it wins — the day must
    appear once, as live, and the drop must be reported rather than silently swallowed."""
    same_day = datetime(2026, 6, 29, 12, 0, tzinfo=ET_UTC)
    payload = _recon_payload(tmp_path, live_day=same_day, recon_days=[same_day])

    # The overlap day is the ONLY day either store has, so there is no combined book to build.
    assert "books_all" not in payload
    assert payload["coverage"]["recon"]["days"] == 0  # type: ignore[index,call-overload]
    assert payload["coverage"]["recon"]["overlap_days_dropped"] == 1  # type: ignore[index,call-overload]
    assert {t["source"] for t in payload["books"]["adaptive"]["trades"]} == {"live"}  # type: ignore[index,call-overload]


def test_by_source_split_keeps_the_two_populations_apart(tmp_path: Path) -> None:
    """A combined book must never read as if every trade were equally well evidenced (#430)."""
    payload = _recon_payload(
        tmp_path,
        live_day=datetime(2026, 6, 29, 12, 0, tzinfo=ET_UTC),
        recon_days=[datetime(2026, 6, 25, 12, 0, tzinfo=ET_UTC)],
    )

    live_split = payload["books"]["adaptive"]["stats"]["by_source"]  # type: ignore[index,call-overload]
    all_split = payload["books_all"]["adaptive"]["stats"]["by_source"]  # type: ignore[index,call-overload]

    # An all-live book still carries the key, zeroed — so the page renders one shape regardless.
    assert live_split["live"]["n_trades"] == 1
    assert live_split["recon"] == {
        "n_trades": 0,
        "wins": 0,
        "losses": 0,
        "win_rate": None,
        "total_r": 0.0,
        "avg_r": None,
        "n_days": 0,
    }
    # The combined book attributes each trade to the store it came from.
    assert all_split["live"]["n_trades"] == 1
    assert all_split["recon"]["n_trades"] == 1
    assert all_split["recon"]["n_days"] == 1
    assert all_split["live"]["total_r"] + all_split["recon"]["total_r"] == pytest.approx(
        payload["books_all"]["adaptive"]["stats"]["total_r"]  # type: ignore[index,call-overload]
    )


def test_combined_books_carry_no_projection(tmp_path: Path) -> None:
    """The forward view resamples what the tracker OBSERVED, so it stays live-only (#430).

    Bootstrapping it from a reconstructed-heavy history would forecast an account trading a
    universe we know differs from the live one — through appearance timing (#433), not the 50-row
    rank cap once blamed for it, which #460 measured as never binding."""
    payload = _recon_payload(
        tmp_path,
        live_day=datetime(2026, 6, 29, 12, 0, tzinfo=ET_UTC),
        recon_days=[datetime(2026, 6, 25, 12, 0, tzinfo=ET_UTC)],
    )

    assert payload["books"]["adaptive"]["projection"] is not None  # type: ignore[index,call-overload]
    for key, book in payload["books_all"].items():  # type: ignore[union-attr]
        assert book["projection"] is None, key


def test_recon_and_live_candidate_caches_cannot_collide(tmp_path: Path) -> None:
    """The cache filename is the date alone, so the two stores need separate directories (#430).

    Sharing one would let an overlap day's live and reconstructed extractions overwrite each other,
    flipping which one the book saw on every rebuild."""
    import small_cap_stack.portfolio as pf

    s = _s(data_dir=tmp_path)
    assert pf.portfolio_candidate_cache_dir(s) != pf.portfolio_candidate_cache_dir(s, "recon")
    # The live path is unchanged, so caches primed before #430 are still found.
    assert pf.portfolio_candidate_cache_dir(s) == tmp_path / "cache" / "portfolio_candidates"


def test_recon_store_dir_follows_settings(tmp_path: Path) -> None:
    """`recon_subdir=""` switches the feature off entirely — no second store is even opened."""
    import small_cap_stack.portfolio as pf

    assert pf.recon_store_dir(_s(data_dir=tmp_path)) == tmp_path / "recon"
    assert pf.recon_store_dir(_s(data_dir=tmp_path, recon_subdir="")) is None
    assert pf.open_recon_store(_s(data_dir=tmp_path, recon_subdir="")) is None
    opened = pf.open_recon_store(_s(data_dir=tmp_path))
    assert opened is not None and opened.data_dir == tmp_path / "recon"


def test_cached_candidates_round_trip_their_provenance(tmp_path: Path) -> None:
    """Provenance must survive the on-disk candidate cache, or the second rebuild loses the label.

    A cache written before #430 has no `source` key at all; `_candidate_from_json` indexes rather
    than `.get()`s it (the #390 convention) so that raises and forces one correct re-extract —
    rather than silently relabelling every reconstructed day as live, permanently."""
    import small_cap_stack.portfolio as pf

    cand = _cand("AZI", 8, 10.0, 9.0, [_bar(10, 12.5, 9.95, 12.3)])
    recon = replace(cand, source="recon")

    assert pf._candidate_from_json(pf._candidate_to_json(recon)).source == "recon"
    assert pf._candidate_from_json(pf._candidate_to_json(cand)).source == "live"

    stale = pf._candidate_to_json(recon)
    del stale["source"]  # a pre-#430 cache entry
    with pytest.raises(KeyError):
        pf._candidate_from_json(stale)


def test_the_recon_candidate_budget_bounds_the_payload_newest_first(tmp_path: Path) -> None:
    """The #448 bound on #273's failure mode, applied where it can still be applied.

    `build_portfolio_payload` retains every day's bars (it re-simulates the same day list once per
    selectable target), so peak memory is linear in days x candidates — which is what OOM-killed
    the box at ~25 live days. A finished harvest makes it ~500, and reconstructed days are denser
    than live ones — though not for the rank-cap reason once assumed (#460).
    """
    payload = _recon_payload(
        tmp_path,
        live_day=datetime(2026, 6, 29, 12, 0, tzinfo=ET_UTC),
        recon_days=[
            datetime(2026, 6, 22, 12, 0, tzinfo=ET_UTC),
            datetime(2026, 6, 23, 12, 0, tzinfo=ET_UTC),
            datetime(2026, 6, 24, 12, 0, tzinfo=ET_UTC),
        ],
        portfolio_recon_max_candidates=2,  # each seeded day contributes one candidate
    )

    all_trades = payload["books_all"]["adaptive"]["trades"]  # type: ignore[index,call-overload]
    # Newest-first: the two most recent reconstructed days survive, the oldest is dropped. That
    # ordering matters — what survives is the segment contiguous with the live record.
    assert [t["date"] for t in all_trades] == ["2026-06-23", "2026-06-24", "2026-06-29"]

    cov = payload["coverage"]["recon"]  # type: ignore[index,call-overload]
    # Never silent: a capped payload says so, or "coverage from 06-23" reads as "that is all the
    # harvest has" rather than "that is all the payload can hold".
    assert cov["capped_days_dropped"] == 1
    assert cov["candidate_budget"] == 2
    assert cov["days"] == 2


def test_the_budget_always_yields_at_least_one_reconstructed_day(tmp_path: Path) -> None:
    """A budget smaller than a single busy session must not produce an empty `books_all` with no
    explanation — one unusually heavy day would silently disable the whole feature."""
    payload = _recon_payload(
        tmp_path,
        live_day=datetime(2026, 6, 29, 12, 0, tzinfo=ET_UTC),
        recon_days=[datetime(2026, 6, 25, 12, 0, tzinfo=ET_UTC)],
        portfolio_recon_max_candidates=1,
    )
    assert payload["coverage"]["recon"]["days"] == 1  # type: ignore[index,call-overload]
    assert payload["coverage"]["recon"]["capped_days_dropped"] == 0  # type: ignore[index,call-overload]


def test_the_budget_is_off_by_default_for_the_sizes_that_exist_today(tmp_path: Path) -> None:
    """15k candidates is ~400 MB retained; nothing the harvest has produced comes near it, so the
    cap must be invisible until it is genuinely needed."""
    payload = _recon_payload(
        tmp_path,
        live_day=datetime(2026, 6, 29, 12, 0, tzinfo=ET_UTC),
        recon_days=[
            datetime(2026, 6, 24, 12, 0, tzinfo=ET_UTC),
            datetime(2026, 6, 25, 12, 0, tzinfo=ET_UTC),
        ],
    )
    assert payload["coverage"]["recon"]["days"] == 2  # type: ignore[index,call-overload]
    assert payload["coverage"]["recon"]["capped_days_dropped"] == 0  # type: ignore[index,call-overload]
