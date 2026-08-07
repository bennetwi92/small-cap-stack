"""Tests for the virtual-portfolio tracker (#230) — the paper-book trading logic.

The exit simulator + sizing + selection are the product here, so they're exercised exhaustively:
target hit, stop, breakeven scratch, gap-through, mark-to-close, and the day-level 2-trade
capacity / opening-equity sizing rules.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from small_cap_stack.capture import Bar
from small_cap_stack.config import Settings
from small_cap_stack.portfolio import (
    CandidateTrade,
    SizedPosition,
    _DataFeeLedger,
    _select_day,
    _take_day,
    _TaxLedger,
    _VpsLedger,
    _WithdrawalLedger,
    best_target,
    commission,
    expectancy_curve,
    risk_ladder,
    simulate_exit,
    simulate_portfolio,
    simulate_portfolio_adaptive,
    size_position,
    step_risk_rung,
    trade_costs,
)

ET = ZoneInfo("America/New_York")
ET_UTC = UTC  # seeds store timestamps in UTC (the store's native tz), like test_report


def _s(**overrides: object) -> Settings:
    # The forward projection (`portfolio.projection`) is a 500-path × 252-session Monte-Carlo run
    # for EVERY book in the payload, so leaving it at production settings turned this file from
    # 4.5s into 57s — an order of magnitude of CI, spent re-running a simulation none of these
    # tests assert anything about. It has its own module (`test_projection.py`) with its own
    # settings; here it is dialled down to the cheapest run that still produces a real block.
    # An explicit override still wins, so a test that *does* want the full thing can ask.
    defaults: dict[str, object] = {"portfolio_projection_paths": 8}
    defaults.update(overrides)
    return Settings(_env_file=None, **defaults)  # type: ignore[call-arg]


def _bar(o: float, h: float, low: float, c: float, *, minute: int = 0, hour: int = 8) -> Bar:
    # ET-aware; hour defaults to 08:00 (pre-market) so trigger-time checks pass unless overridden.
    start = datetime(2026, 7, 14, hour, minute, tzinfo=ET)
    return Bar(start=start, open=o, high=h, low=low, close=c, volume=1000.0)


# --- --- Cost model (#232) ------------------------------------------------------------


def test_trade_costs_matches_broker_costs_research_table() -> None:
    """Pin the all-in round trip against research/broker-costs.md §3's table, to the cent.

    That table is what the account-viability verdict rests on, so if these drift apart one of the
    two is wrong. A $250 position at each price point; exit priced flat to entry so the SEC fee
    (charged on proceeds) is computed off a known notional."""
    s = _s()
    for price, qty, expected_rt in [
        (1.50, 166, 2.26),  # per-share rate binds; fees ≈ commission
        (2.50, 100, 1.36),  # exactly at the $0.35 minimum's break-even share count
        (10.00, 25, 0.87),  # minimum binds hard; you pay ~4× the headline rate
        (20.00, 12, 0.79),
    ]:
        c = trade_costs(qty, price, price, s)
        assert round(c.total_usd, 2) == expected_rt, f"${price} × {qty}sh"


def test_trade_costs_commission_only_would_understate_badly() -> None:
    """The bug this change fixes: commission alone misses ~half the cost at 100+ shares."""
    s = _s()
    c = trade_costs(100, 2.50, 2.50, s)
    commission_only = 2 * commission(
        100, s.portfolio_commission_per_share, s.portfolio_commission_min
    )
    assert commission_only == 0.70
    assert c.total_usd > 1.9 * commission_only  # pass-throughs ≈ double it


def test_trade_costs_sell_side_only_fees() -> None:
    """TAF + SEC are sell-side only: a higher exit lifts cost only via the SEC fee on proceeds."""
    s = _s()
    flat = trade_costs(100, 10.0, 10.0, s)
    up = trade_costs(100, 10.0, 20.0, s)
    # only the SEC fee moves: (100×20 − 100×10) × 0.0000278
    assert round(up.fees_usd - flat.fees_usd, 6) == round(1000 * s.portfolio_sec_fee_rate, 6)
    assert up.commission_usd == flat.commission_usd


def test_trade_costs_zero_qty_is_free() -> None:
    assert trade_costs(0, 10.0, 10.0, _s()).total_usd == 0.0


# --- --- simulate_exit ----------------------------------------------------------------


def test_exit_hits_fixed_target() -> None:
    # entry 10, stop 9 (risk 1). Target 2R = 12. Bar 1 highs to 12.5 -> fills at exactly 12.0.
    bars = [_bar(10, 10.2, 9.9, 10.1), _bar(10.1, 12.5, 10.0, 12.3)]
    out = simulate_exit(bars, 10.0, 9.0, 0, target_r=2.0)
    assert out.reason == "target"
    assert out.realized_r == 2.0
    assert out.exit_price == 12.0  # limit fill, gap-up over target not credited
    assert out.exit_index == 1


def test_exit_stops_out_at_minus_one_r() -> None:
    bars = [_bar(10, 10.3, 9.95, 10.2), _bar(10.1, 10.4, 8.8, 9.0)]
    out = simulate_exit(bars, 10.0, 9.0, 0, target_r=3.0)
    assert out.reason == "stop"
    # bar-2 opened at 10.1 (above stop) then dipped through 9.0 -> fills at the 9.0 stop, no slip.
    assert out.exit_price == 9.0
    assert out.realized_r == -1.0


def test_exit_stop_gap_through_fills_worse_than_stop() -> None:
    # bar opens BELOW the stop -> gap-through fills at the open, a loss worse than -1R.
    bars = [_bar(10, 10.3, 9.95, 10.2), _bar(8.5, 8.6, 8.4, 8.5)]
    out = simulate_exit(bars, 10.0, 9.0, 0, target_r=3.0)
    assert out.reason == "stop"
    assert out.exit_price == 8.5
    assert out.realized_r == -1.5


def test_exit_slippage_widens_stop_fill_only() -> None:
    bars = [_bar(10, 10.3, 9.95, 10.2), _bar(10.1, 10.2, 8.9, 9.0)]
    out = simulate_exit(bars, 10.0, 9.0, 0, target_r=3.0, tick_size=0.01, exit_slippage_ticks=2)
    assert out.reason == "stop"
    assert out.exit_price == 8.98  # 9.00 stop - 2 ticks
    assert out.realized_r == -1.02


def test_exit_stop_first_when_same_bar_breaches_both() -> None:
    # a bar that reaches the target high AND dips to the stop is treated as a stop (conservative).
    bars = [_bar(10, 10.1, 9.98, 10.0), _bar(10.0, 13.0, 8.9, 9.5)]
    out = simulate_exit(bars, 10.0, 9.0, 0, target_r=2.0)
    assert out.reason == "stop"


def test_exit_breakeven_arms_then_scratches() -> None:
    # bar 1 highs to 11 (arms breakeven at 1R since be=1.0), never hits 2R target; bar 2 falls back
    # to entry -> exit at breakeven (0R), tagged "breakeven".
    bars = [
        _bar(10, 10.1, 9.98, 10.05),
        _bar(10.05, 11.0, 10.2, 10.8),  # arms BE (high >= 11), no exit this bar
        _bar(10.8, 10.9, 9.9, 10.0),  # dips to 10.0 == entry (BE stop) -> scratch
    ]
    out = simulate_exit(bars, 10.0, 9.0, 0, target_r=2.0, breakeven_r=1.0)
    assert out.reason == "breakeven"
    assert out.exit_price == 10.0
    assert out.realized_r == 0.0


def test_exit_breakeven_no_lookahead_same_bar() -> None:
    # the SAME bar spikes to the arm level then reverses below entry; BE only protects the NEXT bar,
    # so this bar does NOT scratch at BE (stop still the original 9.0, not breached) -> continues.
    bars = [
        _bar(10, 10.1, 9.98, 10.05),
        _bar(10.05, 11.0, 9.95, 9.97),  # armed at end of bar; low 9.95 > original stop 9.0
        _bar(
            9.97, 13.5, 9.99, 13.0
        ),  # next bar: still holds entry(10.0)? low 9.99 < 10.0 -> BE stop
    ]
    out = simulate_exit(bars, 10.0, 9.0, 0, target_r=2.0, breakeven_r=1.0)
    assert (
        out.reason == "breakeven"
    )  # bar 3 low 9.99 breaches the armed 10.0 stop before the target
    assert out.exit_index == 2


def test_exit_marks_to_close_when_unresolved() -> None:
    bars = [_bar(10, 10.4, 9.9, 10.2), _bar(10.2, 10.6, 10.0, 10.5)]
    out = simulate_exit(bars, 10.0, 9.0, 0, target_r=5.0)  # never reaches 5R (=14), never stops
    assert out.reason == "close"
    assert out.exit_price == 10.5
    assert out.realized_r == 0.5


def test_exit_requires_positive_risk() -> None:
    import pytest

    with pytest.raises(ValueError):
        simulate_exit([_bar(10, 11, 9, 10)], 9.0, 9.0, 0, target_r=2.0)


# --- --- sizing & costs ---------------------------------------------------------------


def _size(equity: float, entry: float, stop: float) -> int:
    return size_position(equity, entry, stop, risk_fraction=0.05, max_position_fraction=0.50).qty


def _sized(equity: float, entry: float, stop: float) -> SizedPosition:
    return size_position(equity, entry, stop, risk_fraction=0.05, max_position_fraction=0.50)


# NB the names of the next two were swapped until #286 — each asserted the opposite of what it
# said, matching the inverted claim in the package docstring. The condition is
# `risk_qty < cap_qty  <=>  (entry - stop) / entry > risk_fraction / max_position_fraction`, i.e.
# the RISK target binds on a WIDE stop and the CAP binds on a TIGHT one.
def test_size_position_notional_cap_binds_on_tight_stop() -> None:
    # $500 eq, 5% risk = $25. Entry 10 / stop 9.5 -> risk/sh $0.50 -> floor(25/0.5)=50 by risk,
    # but the 50% cap is floor(250/10)=25 -> the CAP binds (25 < 50).
    assert _size(500.0, 10.0, 9.5) == 25
    # Entry 20 / stop 19 -> risk floor(25/1)=25; cap floor(250/20)=12 -> the CAP binds.
    assert _size(500.0, 20.0, 19.0) == 12


def test_size_position_risk_target_binds_on_wide_stop() -> None:
    # Entry 10 / stop 5 -> risk/sh $5 -> floor(25/5)=5 by risk; cap floor(250/10)=25 -> RISK binds.
    assert _size(500.0, 10.0, 5.0) == 5
    # Entry 3 / stop 2 -> risk/sh $1 -> floor(25/1)=25 by risk; cap floor(250/3)=83 -> RISK binds
    # (the cheap stock is risk-limited, not capital-limited, so it no longer buys 83 shares).
    assert _size(500.0, 3.0, 2.0) == 25


def test_size_position_floors_to_whole_shares() -> None:
    # risk/sh $0.30 -> floor(25/0.30)=83.33 -> 83, and the cap (floor(250/3)=83) coincides here.
    assert _size(500.0, 3.0, 2.70) == 83


def test_size_position_zero_when_unaffordable() -> None:
    assert _size(500.0, 300.0, 299.0) == 0  # cap floor(250/300)=0 -> can't afford a share


def test_size_position_zero_when_stop_too_wide_for_risk_budget() -> None:
    # Affordable (cap floor(250/100)=2) but risk/sh $30 > the $25 budget -> risk_qty 0 wins.
    assert _size(500.0, 100.0, 70.0) == 0


def test_size_position_nonpositive_risk_falls_back_to_cap() -> None:
    # Degenerate stop >= entry (caller guarantees this never happens) -> cap-bound defensively.
    assert _size(500.0, 10.0, 10.0) == 25


# --- --- sizing: which constraint bound, and the risk actually taken (#286) -------------


def test_sized_position_reports_cap_as_the_binding_constraint() -> None:
    # The tight-stop case: risk wanted 50 shares, the cap allowed 25 -> the cap gave up 25 shares,
    # and the position risks 25 x $0.50 = $12.50 = 2.5% of equity, HALF the configured 5% ceiling.
    sp = _sized(500.0, 10.0, 9.5)
    assert (sp.qty, sp.risk_qty, sp.cap_qty, sp.sized_by) == (25, 50, 25, "cap")
    assert sp.risk_usd == 12.5
    assert sp.risk_pct == 0.025


def test_sized_position_reports_risk_as_the_binding_constraint() -> None:
    # The wide-stop case: the risk budget is spent almost exactly, so risk_pct ~= risk_fraction.
    sp = _sized(500.0, 10.0, 5.0)
    assert (sp.qty, sp.risk_qty, sp.cap_qty, sp.sized_by) == (5, 5, 25, "risk")
    assert sp.risk_usd == 25.0
    assert sp.risk_pct == 0.05


def test_sized_position_tie_reports_risk_not_cap() -> None:
    # risk_qty == cap_qty == 83: the cap did not REDUCE the size, so nothing was given up to it.
    # Calling this "cap" would report a constraint that cost the trade nothing.
    sp = _sized(500.0, 3.0, 2.70)
    assert (sp.risk_qty, sp.cap_qty, sp.sized_by) == (83, 83, "risk")


def test_sized_position_risk_pct_never_exceeds_the_configured_fraction() -> None:
    # The invariant the page's "up to N% risk / trade" claim rests on, over a wide sweep of stops.
    for entry in (1.0, 3.0, 7.5, 20.0):
        for stop_frac in (0.005, 0.02, 0.05, 0.1, 0.25, 0.5):
            sp = _sized(500.0, entry, round(entry * (1 - stop_frac), 4))
            assert sp.risk_pct <= 0.05 + 1e-9, (entry, stop_frac, sp)
            # And the cap binds exactly when the stop is inside risk/position = 10% of entry.
            if sp.qty > 0:
                assert sp.sized_by == ("cap" if stop_frac < 0.10 else "risk"), (entry, stop_frac)


def test_sized_position_unaffordable_is_reported_not_crashed() -> None:
    sp = _sized(500.0, 300.0, 299.0)
    assert (sp.qty, sp.risk_usd, sp.risk_pct) == (0, 0.0, 0.0)


def test_commission_respects_minimum() -> None:
    assert commission(50, 0.0035, 0.35) == 0.35  # 50 × 0.0035 = 0.175 -> min 0.35
    assert commission(200, 0.0035, 0.35) == 0.70  # 200 × 0.0035 = 0.70 > min


# --- --- portfolio simulation ---------------------------------------------------------


def _cand(
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


def test_portfolio_caps_at_two_trades_per_day_by_trigger_time() -> None:
    win = [_bar(10, 12.5, 9.95, 12.3)]  # hits 2R
    cands = [
        _cand("AAA", 5, 10.0, 9.0, win),
        _cand("BBB", 6, 10.0, 9.0, win),
        _cand("CCC", 7, 10.0, 9.0, win),  # 3rd by time -> dropped (capacity 2)
    ]
    res = simulate_portfolio([(date(2026, 7, 14), cands)], _s(), target_r=2.0)
    assert res.n_trades == 2
    assert {t.symbol for t in res.trades} == {"AAA", "BBB"}


def test_portfolio_records_setups_dropped_by_the_daily_cap() -> None:
    # Three qualifying setups, cap 2 -> the 3rd (by trigger time) is skipped, and the book records
    # what it *would* have made at the day's target so the page can show what the cap cost.
    win = [_bar(10, 12.5, 9.95, 12.3)]  # hits +2R
    loss = [_bar(10, 10.3, 8.8, 9.0)]  # stops at 9.0 -> -1R
    cands = [
        _cand("AAA", 5, 10.0, 9.0, win),
        _cand("BBB", 6, 10.0, 9.0, win),
        _cand("CCC", 7, 10.0, 9.0, loss),  # 3rd by time -> skipped, would have been -1R
    ]
    res = simulate_portfolio([(date(2026, 7, 14), cands)], _s(), target_r=2.0)
    assert res.n_trades == 2
    assert [sk.symbol for sk in res.skipped] == ["CCC"]
    sk = res.skipped[0]
    # Simulated with the exact same exit model a taken trade would use (target + 2-tick stop slip).
    would_be = cands[2].exit_under(_s(), 2.0, 0.0)
    assert sk.reason == "stop" and sk.realized_r == would_be.realized_r < 0
    assert sk.target_r == 2.0  # simulated at the same target the day was taken at
    assert res.skipped_total_r == sk.realized_r
    # A skipped setup never touches equity or the trade stats — it's an informational log only.
    assert all(t.symbol != "CCC" for t in res.trades)


def test_portfolio_no_skips_when_under_the_cap() -> None:
    win = [_bar(10, 12.5, 9.95, 12.3)]
    cands = [_cand("AAA", 5, 10.0, 9.0, win), _cand("BBB", 6, 10.0, 9.0, win)]
    res = simulate_portfolio([(date(2026, 7, 14), cands)], _s(), target_r=2.0)
    assert res.skipped == () and res.skipped_total_r == 0.0


def test_portfolio_both_trades_size_off_opening_equity() -> None:
    # $500 open. Entry 10 / stop 9 -> risk/sh $1 -> 5% risk floor(25/1)=25; 50% cap floor(250/10)=25
    # (they coincide here) -> 25 shares each, regardless of the first trade's outcome.
    win = [_bar(10, 12.5, 9.95, 12.3)]
    cands = [_cand("AAA", 5, 10.0, 9.0, win), _cand("BBB", 6, 10.0, 9.0, win)]
    res = simulate_portfolio([(date(2026, 7, 14), cands)], _s(), target_r=2.0)
    assert [t.qty for t in res.trades] == [25, 25]


def test_portfolio_pnl_and_equity_bookkeeping() -> None:
    # Single winner: 25 sh × (12.0 - 10.0) = $50 gross.
    #   commission = 2 × max(0.35, 25×0.0035=0.0875) = 2 × 0.35 = $0.70
    #   fees       = 2×25×(0.0030+0.0002) + min(25×0.000166, 8.30) + (25×12.0)×0.0000278
    #              = 0.16 + 0.00415 + 0.00834 = $0.1725
    # -> round trip $0.8725, matching research/broker-costs.md's $0.87 for 25 sh of a $10 stock.
    # The market-data + VPS fees are zeroed here so this stays a test of *trade* bookkeeping; the
    # subscription and the getting-paid layer have their own tests below.
    win = [_bar(10, 12.5, 9.95, 12.3)]
    res = simulate_portfolio(
        [(date(2026, 7, 14), [_cand("AAA", 5, 10.0, 9.0, win)])],
        _s(portfolio_market_data_usd_per_month=0.0, portfolio_vps_gbp_per_month=0.0),
        target_r=2.0,
    )
    t = res.trades[0]
    assert t.qty == 25
    assert t.gross_pnl_usd == 50.0
    assert t.commission_usd == 0.70
    assert t.fees_usd == 0.1725
    assert t.net_pnl_usd == 49.1275
    assert res.end_equity == 549.1275
    assert res.wins == 1 and res.losses == 0
    assert res.win_rate == 1.0


def test_portfolio_drawdown_and_return() -> None:
    win = [_bar(10, 12.5, 9.95, 12.3)]  # +2R
    loss = [_bar(10, 10.3, 8.8, 9.0)]  # stops at 9.0 -> -1R
    days = [
        (date(2026, 7, 13), [_cand("AAA", 5, 10.0, 9.0, loss)]),
        (date(2026, 7, 14), [_cand("BBB", 5, 10.0, 9.0, win)]),
    ]
    res = simulate_portfolio(days, _s())
    assert res.n_trades == 2
    assert res.equity_curve[0][0] == date(2026, 7, 13)  # days sorted chronologically
    assert res.max_drawdown_pct > 0  # the day-1 loss draws down before day-2 recovers


def test_portfolio_empty_is_safe() -> None:
    res = simulate_portfolio([], _s())
    assert res.n_trades == 0
    assert res.end_equity == res.start_equity == 500.0
    assert res.win_rate is None and res.avg_r is None


def test_adaptive_falls_back_before_enough_samples_then_refits() -> None:
    from datetime import timedelta

    from small_cap_stack.portfolio import simulate_portfolio_adaptive

    # 6 warm-up days (1 trade each) then a decision day. min_samples=6, window big, grid {1.5,3.0}.
    # Warm-up trades run to +3R (high 13) then close, so over the trailing window target 1.5 banks
    # +1.5R each and target 3.0 fills for +3R -> 3.0 wins expectancy. It also has to clear the #476
    # margin gate against the 2.0 fallback: every trade gains exactly +1R by holding to 3R, so the
    # paired edge is +1.0R with ZERO spread -> a deterministic edge, which the gate scores as
    # infinitely many standard errors and lets through. The decision day is taken at 3.0.
    reach2 = [_bar(10, 13.0, 9.95, 13.0)]  # favourable to +3R then closes there
    s = _s(
        portfolio_target_grid=(1.5, 3.0),
        portfolio_adaptive_min_samples=6,
        portfolio_adaptive_window_days=90,
        portfolio_exit_slippage_ticks=0,
    )
    base = date(2026, 7, 1)
    days = [(base + timedelta(days=i), [_cand(f"W{i}", 5, 10.0, 9.0, reach2)]) for i in range(6)]
    days.append((base + timedelta(days=6), [_cand("DEC", 5, 10.0, 9.0, reach2)]))

    book = simulate_portfolio_adaptive(days, s)
    res, chosen = book.result, book.daily_targets
    per_day = dict(chosen)
    day0, decision = per_day[base], per_day[base + timedelta(days=6)]
    assert day0.target_r == s.portfolio_target_r  # day 0: no trailing samples -> fallback (2.0)
    assert (day0.fitted, day0.trailing_n) == (False, 0)  # ...and it says so (#463)
    assert decision.target_r == 3.0  # decision day: re-fit to the best trailing T
    assert (decision.fitted, decision.trailing_n) == (True, 6)
    dec = [t for t in res.trades if t.symbol == "DEC"][0]
    assert dec.target_r == 3.0


# --- --- adaptive optimiser -----------------------------------------------------------


def test_expectancy_curve_and_best_target() -> None:
    # AAA runs to +3R then closes; BBB stops at -1R. Mean realised R over BOTH per target:
    #  T=1: AAA +1, BBB -1  -> exp 0.0  (only AAA hit -> hit_rate 0.5)
    #  T=2: AAA +2, BBB -1  -> exp 0.5
    #  T=4: AAA never reaches 4R (peaks 13 == +3R) marks to close (+3R), BBB -1 -> exp 1.0
    big_win = [_bar(10, 13.0, 9.95, 13.0)]  # high 13 = +3R against risk 1
    loss = [_bar(10, 10.2, 8.9, 9.0)]
    cands = [_cand("AAA", 5, 10.0, 9.0, big_win), _cand("BBB", 6, 10.0, 9.0, loss)]
    # slippage off for clean pedagogical R values (it flows through exit_under via settings).
    stats = expectancy_curve(
        cands, _s(portfolio_exit_slippage_ticks=0), target_grid=[1.0, 2.0, 4.0]
    )
    by_t = {st.target_r: st for st in stats}
    assert by_t[1.0].expectancy_r == 0.0
    assert by_t[2.0].expectancy_r == 0.5
    assert by_t[4.0].expectancy_r == 1.0
    assert by_t[1.0].hit_rate == 0.5
    best = best_target(stats)
    assert best is not None
    assert best.target_r == 4.0  # highest expectancy


def test_best_target_breaks_ties_toward_smaller_target() -> None:
    from small_cap_stack.portfolio import TargetStat

    stats = [
        TargetStat(1.0, 0.0, 10, 0.6, 0.5),
        TargetStat(3.0, 0.0, 10, 0.3, 0.5),  # equal expectancy, bigger target
        TargetStat(2.0, 0.0, 10, 0.4, 0.2),
    ]
    best = best_target(stats)
    assert best is not None
    assert best.target_r == 1.0  # tie at 0.5 -> smaller target (higher hit rate) wins


def test_best_target_none_when_no_expectancy() -> None:
    from small_cap_stack.portfolio import TargetStat

    assert best_target([TargetStat(2.0, 0.0, 0, None, None)]) is None


# --- --- adaptive risk throttle / kill-switch (#239) ----------------------------------


def test_risk_ladder_shape() -> None:
    # 3 rungs incl. the 0 floor at the 5% default -> (0, 2.5%, 5%).
    assert risk_ladder(_s(portfolio_risk_rungs=3)) == (0.0, 0.025, 0.05)
    # The SHIPPED default is 1 rung: the throttle is switched off (#474).
    assert risk_ladder(_s()) == (0.05,)
    assert risk_ladder(_s(portfolio_risk_rungs=1)) == (0.05,)  # 1 rung -> throttle disabled
    assert risk_ladder(_s(portfolio_risk_rungs=2)) == (0.0, 0.05)  # binary kill-switch
    # honours a different max + rung count (evenly spaced).
    assert risk_ladder(_s(portfolio_risk_fraction=0.06, portfolio_risk_rungs=4)) == (
        0.0,
        0.02,
        0.04,
        0.06,
    )


def test_step_risk_rung_needs_consecutive_days() -> None:
    # step_days=2: one decisive day only builds the streak; the second in a row moves the rung.
    assert step_risk_rung(2, 0, -1.0, 3, 2) == (2, -1)  # 1st losing day -> streak only
    assert step_risk_rung(2, -1, -1.0, 3, 2) == (1, 0)  # 2nd in a row -> down a rung, streak resets
    assert step_risk_rung(0, 0, 1.0, 3, 2) == (0, 1)  # 1st winning day -> streak only
    assert step_risk_rung(0, 1, 1.0, 3, 2) == (1, 0)  # 2nd in a row -> up a rung
    # a flat / no-setup day holds BOTH the rung and the streak (no momentum lost across a gap)
    assert step_risk_rung(2, -1, 0.0, 3, 2) == (2, -1)
    # a decisive day in the OPPOSITE direction flips the streak to ±1 (no rung move yet)
    assert step_risk_rung(1, -1, 1.0, 3, 2) == (1, 1)
    # clamps at the ends and still resets the streak when a run completes there
    assert step_risk_rung(2, 1, 1.0, 3, 2) == (2, 0)  # at the top -> stays, resets
    assert step_risk_rung(0, -1, -1.0, 3, 2) == (0, 0)  # at the floor -> stays, resets


def test_step_risk_rung_step_days_one_is_eager() -> None:
    # step_days=1 reproduces one-rung-per-decisive-day.
    assert step_risk_rung(1, 0, 1.0, 3, 1) == (2, 0)
    assert step_risk_rung(1, 0, -1.0, 3, 1) == (0, 0)
    assert step_risk_rung(1, 0, 0.0, 3, 1) == (1, 0)  # flat still holds


def test_day_signal_r_is_size_independent() -> None:
    from small_cap_stack.portfolio import _day_signal_r, _select_day

    s = _s(portfolio_exit_slippage_ticks=0)
    win = _cand("AAA", 5, 10.0, 9.0, [_bar(10, 12.0, 9.95, 12.0)])  # +2R at target 2.0
    loss = _cand("BBB", 6, 10.0, 9.0, [_bar(10, 10.3, 8.8, 9.0)])  # -1R
    taken = _select_day([win, loss], s)
    assert _day_signal_r(taken, s, 2.0, 0.0) == 1.0  # +2 + (-1)
    assert _day_signal_r([], s, 2.0, 0.0) == 0.0  # no setups -> flat


def _win_cand(sym: str) -> CandidateTrade:
    return _cand(sym, 5, 10.0, 9.0, [_bar(10, 12.0, 9.95, 12.0)])  # +2R vs risk 1


def _win3_cand(sym: str) -> CandidateTrade:
    """Runs to +3R and closes there — a trade where holding past the 2.0R fallback genuinely pays.

    `_win_cand` tops out at exactly +2R, so under the #476 margin gate a 3.0R target is *identical*
    to the fallback on it (both exit at +2R, edge 0) and the gate correctly refuses to switch. Tests
    that need the fit to actually move need a trade with a real edge, which is this one."""
    return _cand(sym, 5, 10.0, 9.0, [_bar(10, 13.0, 9.95, 13.0)])  # +3R vs risk 1


def _loss_cand(sym: str) -> CandidateTrade:
    return _cand(sym, 5, 10.0, 9.0, [_bar(10, 10.3, 8.8, 9.0)])  # stops at -1R


def test_adaptive_risk_eager_step_throttles_down_then_rearms_from_zero() -> None:
    # step_days=1 (eager): min_samples huge so the TARGET stays at the 2.0 fallback — isolate RISK.
    # Two losing days walk risk 5% -> 2.5% -> 0%; at 0% the book sits out (no trade), but the day's
    # winning would-be setup still re-arms it 0% -> 2.5% -> 5%.
    s = _s(
        portfolio_risk_step_days=1,
        portfolio_risk_rungs=3,  # the default is 1 (throttle off, #474) — this tests the ladder
        portfolio_adaptive_min_samples=999,
        portfolio_exit_slippage_ticks=0,
    )
    base = date(2026, 7, 1)
    seq = [
        _loss_cand("L0"),  # rung 2 (5%): take, lose -> down
        _loss_cand("L1"),  # rung 1 (2.5%): take, lose -> down
        _win_cand("W2"),  # rung 0 (0%): SIT OUT, but would-be win -> up
        _win_cand("W3"),  # rung 1 (2.5%): take, win -> up
        _win_cand("W4"),  # rung 2 (5%): take, win -> hold (clamped)
    ]
    days = [(base + timedelta(days=i), [c]) for i, c in enumerate(seq)]
    book = simulate_portfolio_adaptive(days, s)
    res, daily_risk = book.result, book.daily_risk
    assert [r for _d, r in daily_risk] == [0.05, 0.025, 0.0, 0.025, 0.05]
    assert res.n_trades == 4  # the 0% day (W2) took nothing
    assert {t.symbol for t in res.trades} == {"L0", "L1", "W3", "W4"}


def test_adaptive_risk_two_day_step_needs_a_streak() -> None:
    # Default step_days=2: it takes TWO losing days in a row to drop a rung, two wins to climb one.
    # 4 losses then 5 wins: risk holds each level for two days, down and back up.
    s = _s(
        portfolio_risk_rungs=3,  # default is 1 (throttle off, #474); this tests the ladder
        portfolio_adaptive_min_samples=999,
        portfolio_exit_slippage_ticks=0,
    )
    base = date(2026, 7, 1)
    seq = [_loss_cand(f"L{i}") for i in range(4)] + [_win_cand(f"W{i}") for i in range(5)]
    days = [(base + timedelta(days=i), [c]) for i, c in enumerate(seq)]
    book = simulate_portfolio_adaptive(days, s)
    res, daily_risk = book.result, book.daily_risk
    # L L  L L  W W  W W  W   (two days per rung move)
    assert [r for _d, r in daily_risk] == [
        0.05,
        0.05,  # 2 losses -> now dropping
        0.025,
        0.025,  # 2 more losses -> dropping again
        0.0,  # parked at 0 (1st would-be win)
        0.0,  # 2nd would-be win -> re-arm
        0.025,
        0.025,  # 2 wins -> climb
        0.05,  # back to full
    ]
    assert res.n_trades == 7  # the two 0% days sat out


def test_adaptive_risk_stays_full_in_a_good_market() -> None:
    # A green run never knocks risk off the top rung.
    s = _s(portfolio_adaptive_min_samples=999, portfolio_exit_slippage_ticks=0)
    base = date(2026, 7, 1)
    days = [(base + timedelta(days=i), [_win_cand(f"W{i}")]) for i in range(4)]
    daily_risk = simulate_portfolio_adaptive(days, s).daily_risk
    assert [r for _d, r in daily_risk] == [0.05, 0.05, 0.05, 0.05]


# --- --- per-trade risk attribution (#286) ---------------------------------------------


def test_paper_trade_records_the_risk_it_actually_took() -> None:
    # _win_cand: entry 10 / stop 9 -> risk/sh $1. At $500 open equity the 5% budget buys 25 shares
    # and the 50% cap allows 25 -> a tie, so risk binds and the full $25 budget is spent.
    s = _s(portfolio_adaptive_min_samples=999, portfolio_exit_slippage_ticks=0)
    trades, _sk = _take_day([_win_cand("W")], 500.0, s, 2.0, 0.0)
    (t,) = trades
    assert (t.qty, t.sized_by) == (25, "risk")
    assert (t.risk_fraction, t.risk_usd, t.risk_pct) == (0.05, 25.0, 0.05)


def test_paper_trade_records_cap_bound_risk_well_under_the_ceiling() -> None:
    # The live case this issue opened on (SUNE): a stop 1.6% below entry. The risk budget would buy
    # far more than the cap allows, so the trade risks a fraction of the advertised 5% — and the
    # trade row has to say so rather than repeating the ceiling.
    s = _s(portfolio_adaptive_min_samples=999, portfolio_exit_slippage_ticks=0)
    cand = _cand("SUNE", 5, 10.0, 9.84, [_bar(10, 10.5, 9.9, 10.4)])  # risk/sh $0.16
    trades, _sk = _take_day([cand], 500.0, s, 2.0, 0.0)
    (t,) = trades
    assert t.sized_by == "cap"
    assert t.qty == 25  # cap floor(250/10)=25, vs risk floor(25/0.16)=156
    assert t.risk_usd == 4.0  # 25 x $0.16
    assert t.risk_pct == 0.008  # 0.8% of equity, against a 5% risk_fraction
    assert t.risk_fraction == 0.05  # the ceiling is still recorded, just not confused for the risk


def test_paper_trade_risk_fraction_follows_the_throttled_rung() -> None:
    # On a throttled day risk_fraction must be the RUNG, not the configured ceiling — otherwise the
    # trade log would claim 5% on a day the kill-switch deliberately halved the size.
    s = _s(portfolio_adaptive_min_samples=999, portfolio_exit_slippage_ticks=0)
    trades, _sk = _take_day([_win_cand("W")], 500.0, s, 2.0, 0.0, risk_fraction=0.025)
    (t,) = trades
    assert t.risk_fraction == 0.025
    assert (t.qty, t.risk_usd, t.risk_pct) == (12, 12.0, 0.024)  # floor(12.50/1)=12 shares


# --- --- the next-session state (#286) -------------------------------------------------


def test_next_session_state_is_forward_looking_not_the_last_collected_day() -> None:
    # The bug this exists to kill: the page rendered daily_risk[-1] as "Latest risk". After two
    # losing days the LAST day still traded at 5% (the step applies from tomorrow), so "latest"
    # said 5% while the book was in fact about to size the next setup at 2.5%.
    s = _s(
        portfolio_risk_rungs=3,  # default is 1 (throttle off, #474); this tests the ladder
        portfolio_adaptive_min_samples=999,
        portfolio_exit_slippage_ticks=0,
    )
    base = date(2026, 7, 1)
    days = [(base + timedelta(days=i), [_loss_cand(f"L{i}")]) for i in range(2)]
    book = simulate_portfolio_adaptive(days, s)
    assert [r for _d, r in book.daily_risk] == [0.05, 0.05]  # what the collected days DID
    st = book.state
    assert st is not None
    assert st.risk_fraction == 0.025  # what the next session WILL do — the knocked-down rung
    assert st.as_of == base + timedelta(days=2)  # the day after the last collected one
    assert (st.rung, st.n_rungs) == (1, 3)


def test_next_session_state_reports_streak_progress_toward_a_step() -> None:
    # One decisive day at step_days=2: streak -1, so the rung has NOT moved yet and the page can
    # honestly say "one more net-negative day steps risk down".
    s = _s(
        portfolio_risk_rungs=3,  # default is 1 (throttle off, #474); this tests the ladder
        portfolio_adaptive_min_samples=999,
        portfolio_exit_slippage_ticks=0,
    )
    days = [(date(2026, 7, 1), [_loss_cand("L0")])]
    st = simulate_portfolio_adaptive(days, s).state
    assert st is not None
    assert (st.streak, st.step_days, st.rung) == (-1, 2, 2)
    assert st.risk_fraction == 0.05  # still full risk — a single day is not a streak


def test_next_session_state_budgets_are_derived_from_the_end_equity() -> None:
    s = _s(portfolio_adaptive_min_samples=999, portfolio_exit_slippage_ticks=0)
    book = simulate_portfolio_adaptive([(date(2026, 7, 1), [_win_cand("W0")])], s)
    st = book.state
    assert st is not None
    eq = book.result.end_equity
    assert st.risk_budget_usd == round(eq * st.risk_fraction, 4)
    assert st.max_position_usd == round(eq * s.portfolio_position_fraction, 4)


def test_next_session_state_target_uses_every_collected_day() -> None:
    # The day-walk fits each day off STRICTLY-prior days, so the last day's target ignores itself.
    # The next session's fit must include it — otherwise the page shows a target one day stale.
    # 6 warm-up days of +2R wins with min_samples=6: no collected day has 6 strictly-prior samples,
    # so every daily target is the fallback, while the next session finally has enough to re-fit.
    s = _s(
        portfolio_adaptive_min_samples=6,
        portfolio_adaptive_window_days=60,
        portfolio_target_grid=(1.5, 3.0),
        portfolio_target_r=2.0,
        portfolio_exit_slippage_ticks=0,
    )
    base = date(2026, 7, 1)
    days = [(base + timedelta(days=i), [_win3_cand(f"W{i}")]) for i in range(6)]
    book = simulate_portfolio_adaptive(days, s)
    assert {f.target_r for _d, f in book.daily_targets} == {2.0}  # every day fell back
    assert not any(f.fitted for _d, f in book.daily_targets)  # ...and every day is marked as such
    st = book.state
    assert st is not None
    assert (st.target_fitted, st.target_trailing_n) == (True, 6)  # the next session does re-fit
    # _win3_cand runs to +3R: a 1.5R target banks 1.5R, a 3R target fills for 3R. So the fit picks
    # 3.0, and it clears the #476 margin gate (a deterministic +1R edge over the 2.0 fallback). The
    # point stands: it is not the fallback, because the next session re-fits off the 6th day that
    # no collected day could see.
    assert st.target_r == 3.0


def test_the_shipped_default_fits_on_all_history_with_a_margin_gate() -> None:
    # #476: the two knobs ship as "no trailing window" and "one standard error to switch".
    s = _s()
    assert s.portfolio_adaptive_window_days is None
    assert s.portfolio_target_switch_z == 1.0


def test_all_history_window_keeps_trades_a_trailing_window_would_have_dropped() -> None:
    # Six warm-up trades, then a 200-day gap, then the decision day. Any calendar window shorter
    # than the gap discards every warm-up trade and the fit starves; None keeps all of them.
    base = date(2026, 1, 1)
    days: list[tuple[date, list[CandidateTrade]]] = [
        (base + timedelta(days=i), [_win3_cand(f"W{i}")]) for i in range(6)
    ]
    days.append((base + timedelta(days=200), [_win3_cand("DEC")]))

    windowed = _s(
        portfolio_adaptive_window_days=40,
        portfolio_adaptive_min_samples=6,
        portfolio_target_grid=(1.5, 3.0),
        portfolio_exit_slippage_ticks=0,
    )
    unbounded = windowed.model_copy(update={"portfolio_adaptive_window_days": None})

    decision = base + timedelta(days=200)
    w_fit = dict(simulate_portfolio_adaptive(days, windowed).daily_targets)[decision]
    u_fit = dict(simulate_portfolio_adaptive(days, unbounded).daily_targets)[decision]
    # 40-day window: the warm-ups have aged out entirely, so the fit never runs.
    assert (w_fit.trailing_n, w_fit.status) == (0, "thin")
    # All history: every prior trade is in scope and the fit both runs and switches.
    assert (u_fit.trailing_n, u_fit.status, u_fit.target_r) == (6, "fitted", 3.0)


def test_margin_gate_holds_the_fallback_when_the_edge_is_not_worth_acting_on() -> None:
    # A grid pick that beats the fallback ON AVERAGE but not reliably: five trades where 3.0R wins
    # big and five where it loses, so the paired edge carries far more noise than signal. The
    # optimiser still prefers 3.0 — argmax does not care about spread — and the gate is what stops
    # the book acting on it. This is the case #476 exists for: an argmax over noisy means.
    s = _s(
        portfolio_adaptive_min_samples=4,
        portfolio_target_grid=(2.0, 3.0),
        portfolio_target_r=2.0,
        portfolio_target_switch_z=1.0,
        portfolio_exit_slippage_ticks=0,
    )
    base = date(2026, 7, 1)
    # 7 runners at edge +1, 2 stall-outs at edge -3: mean edge +0.11R, SD 1.76 -> z ~= 0.19.
    # 3.0R still wins the raw expectancy (2.11R vs the fallback's 2.00R), so the ARGMAX prefers it
    # and only the gate stands between that 0.11R of signal and a live change to the exit rule.
    stall = [
        _bar(10, 12.2, 9.5, 12.0, minute=0),  # tags +2R (fills a 2R target), stop untouched
        _bar(12, 12.5, 8.8, 8.8, minute=1),  # then breaks the stop, so a 3R target rides it to -1R
    ]
    seq = [_win3_cand(f"R{i}") for i in range(7)]  # +3R -> 3.0 fills (+3) vs fallback +2 -> edge +1
    seq += [_cand(f"F{i}", 5, 10.0, 9.0, stall) for i in range(2)]  # edge -3
    days = [(base + timedelta(days=i), [c]) for i, c in enumerate(seq)]
    book = simulate_portfolio_adaptive(days, s)
    st = book.state
    assert st is not None
    assert st.target_status == "margin"  # the fit ran, preferred 3.0, and was overruled
    assert st.target_considered_r == 3.0
    assert st.target_r == 2.0  # the fallback stands
    assert st.target_fitted is False
    assert st.target_edge_z is not None and st.target_edge_z < 1.0

    # Same data, gate disabled -> the pre-#476 behaviour: the raw argmax is taken.
    no_gate = s.model_copy(update={"portfolio_target_switch_z": 0.0})
    open_gate = simulate_portfolio_adaptive(days, no_gate)
    ost = open_gate.state
    assert ost is not None
    assert (ost.target_status, ost.target_r) == ("fitted", 3.0)


def test_margin_gate_lets_a_deterministic_edge_through() -> None:
    # Zero spread is the STRONGEST evidence, not the absence of it: every trade gains exactly +1R
    # by holding to 3R. A naive `edge / se` would divide by zero and block the clearest switch there
    # is, so the gate scores a zero-SE positive edge as infinitely many standard errors.
    s = _s(
        portfolio_adaptive_min_samples=4,
        portfolio_target_grid=(2.0, 3.0),
        portfolio_target_r=2.0,
        portfolio_exit_slippage_ticks=0,
    )
    base = date(2026, 7, 1)
    days = [(base + timedelta(days=i), [_win3_cand(f"W{i}")]) for i in range(6)]
    st = simulate_portfolio_adaptive(days, s).state
    assert st is not None
    assert (st.target_status, st.target_r, st.target_fitted) == ("fitted", 3.0, True)
    assert st.target_edge_r == 1.0
    assert st.target_edge_z == float("inf")


def test_a_zero_sample_window_is_reported_as_fallback_not_as_a_fit() -> None:
    # min_samples=0 waves the sample gate through, so day 0 reaches the optimiser with an EMPTY
    # trailing window: every grid target scores an undefined expectancy and `best_target` returns
    # None. The target is still the fallback, and `fitted` must say so (#463) — the one path where
    # the optimiser genuinely ran and declined to pick is exactly the path that would otherwise be
    # mislabelled as an adaptive choice.
    s = _s(
        portfolio_adaptive_min_samples=0,
        portfolio_target_grid=(1.5, 3.0),
        portfolio_target_r=2.0,
        portfolio_exit_slippage_ticks=0,
    )
    book = simulate_portfolio_adaptive([(date(2026, 7, 1), [_win_cand("W0")])], s)
    (_day, fit), *_ = book.daily_targets
    assert (fit.target_r, fit.fitted, fit.trailing_n) == (2.0, False, 0)


def test_next_session_state_is_none_for_an_empty_book() -> None:
    assert simulate_portfolio_adaptive([], _s()).state is None


def test_the_shipped_default_has_the_throttle_switched_off() -> None:
    # #474: the ladder is a bet on serial correlation of daily results, and the measured cost of
    # making that bet when it is absent is ~$22 per 29 sessions (500 calendar-preserving shuffles).
    # It ships OFF. Losing days must NOT knock risk down, which is exactly what a re-enable would
    # silently reintroduce — hence a test on the default rather than on the ladder helper.
    s = _s(portfolio_adaptive_min_samples=999, portfolio_exit_slippage_ticks=0)
    assert s.portfolio_risk_rungs == 1
    base = date(2026, 7, 1)
    days = [(base + timedelta(days=i), [_loss_cand(f"L{i}")]) for i in range(4)]
    book = simulate_portfolio_adaptive(days, s)
    assert {r for _d, r in book.daily_risk} == {s.portfolio_risk_fraction}  # flat through the run
    st = book.state
    assert st is not None
    assert (st.risk_fraction, st.rung, st.n_rungs) == (s.portfolio_risk_fraction, 0, 1)
    assert book.result.n_trades == 4  # four losing days and it never sat one out


def test_single_rung_disables_the_throttle() -> None:
    # portfolio_risk_rungs=1 -> always full risk even through a losing streak.
    s = _s(
        portfolio_risk_rungs=1,
        portfolio_adaptive_min_samples=999,
        portfolio_exit_slippage_ticks=0,
    )
    base = date(2026, 7, 1)
    days = [(base + timedelta(days=i), [_loss_cand(f"L{i}")]) for i in range(3)]
    book = simulate_portfolio_adaptive(days, s)
    res, daily_risk = book.result, book.daily_risk
    assert [r for _d, r in daily_risk] == [0.05, 0.05, 0.05]
    assert res.n_trades == 3  # every day still trades at full risk


def test_qualify_rejects_in_session_and_out_of_band() -> None:
    # A direct check that the selection predicate enforces strict pre-market + the price band.
    from small_cap_stack.portfolio import _qualify

    s = _s()
    pre = [_bar(10, 10.1, 9.9, 10.0, hour=9, minute=10)]  # 09:10 ET -> before the 09:15 cutoff
    edge = [_bar(10, 10.1, 9.9, 10.0, hour=9, minute=15)]  # 09:15 ET -> at cutoff (excluded)
    intr = [_bar(10, 10.1, 9.9, 10.0, hour=9, minute=45)]  # 09:45 ET -> in-session
    assert _qualify(0, 10.0, 10.0, 9.0, 1.0, True, pre, s) is True
    assert _qualify(0, 10.0, 10.0, 9.0, 1.0, True, edge, s) is False  # 09:15 is not < 09:15
    assert _qualify(0, 10.0, 10.0, 9.0, 1.0, True, intr, s) is False  # after the cutoff
    assert _qualify(0, 25.0, 25.0, 24.0, 1.0, True, pre, s) is False  # entry_fill 25 > $20 band
    assert _qualify(0, 1.5, 1.5, 1.2, 0.3, True, pre, s) is False  # entry_fill 1.50 < $2 floor
    assert _qualify(0, 2.0, 2.0, 1.7, 0.3, True, pre, s) is True  # $2.00 exactly is inclusive
    assert _qualify(0, 10.0, 10.0, 9.0, 1.0, False, pre, s) is False  # not takeable


# --- extraction (store integration; reuses the report seams) ---------------------------


def _seed_premarket(
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
    from small_cap_stack.storage import Store

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
            {
                "opportunity_id": oid,
                "symbol": symbol,
                "con_id": 1,
                "trading_date": day,
                "first_seen_utc": t0,
                "first_rank": 0,
            },
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
    cands = extract_day_trades(store, _s(portfolio_premarket_cutoff=time(9, 30)), day)
    assert [c.symbol for c in cands] == ["AZI"]
    assert cands[0].trigger_at.astimezone(ET).time() == time(9, 15)


def test_extract_day_trades_rejects_before_0530_floor(tmp_path: Path) -> None:
    """The book doesn't take the earliest pre-market tape: no trigger before 05:30 ET.

    The `first_hit` bar (index 0) is seeded at 05:00 ET so the run's trigger (idx 3) lands at
    05:15 — rejected on the default floor, accepted when the floor is dialled back to 04:00, so it
    is the floor doing the work and not a broken fixture."""
    from small_cap_stack.portfolio import extract_day_trades
    from small_cap_stack.storage import Store

    day = date(2026, 6, 29)
    store = Store(tmp_path)
    _seed_premarket(store, oid_time_utc=datetime(2026, 6, 29, 9, 0, tzinfo=ET_UTC))  # 05:00 ET

    assert extract_day_trades(store, _s(), day) == []
    cands = extract_day_trades(store, _s(portfolio_premarket_earliest=time(4, 0)), day)
    assert [c.symbol for c in cands] == ["AZI"]
    assert cands[0].trigger_at.astimezone(ET).time() == time(5, 15)


def test_extract_day_trades_takes_trigger_exactly_at_0530(tmp_path: Path) -> None:
    """The floor is inclusive: a trigger bar opening exactly at 05:30 ET is takeable.

    Pins the boundary convention against the cutoff's strict `<` — the window is [earliest, cutoff).
    """
    from small_cap_stack.portfolio import extract_day_trades
    from small_cap_stack.storage import Store

    day = date(2026, 6, 29)
    store = Store(tmp_path)
    _seed_premarket(store, oid_time_utc=datetime(2026, 6, 29, 9, 15, tzinfo=ET_UTC))  # 05:15 ET

    cands = extract_day_trades(store, _s(), day)
    assert [c.symbol for c in cands] == ["AZI"]
    assert cands[0].trigger_at.astimezone(ET).time() == time(5, 30)


def test_extract_day_trades_rejects_sub_2_dollar_entries(tmp_path: Path) -> None:
    """Sub-$2 entries are out of the book's price band (#386, floor raised $1 → $2).

    The same setup scaled to a ~$1.53 fill: rejected on the default floor, accepted when the floor
    is dialled back to the old $1 — so it is the price band doing the work, not a broken fixture."""
    from small_cap_stack.portfolio import extract_day_trades
    from small_cap_stack.storage import Store

    day = date(2026, 6, 29)
    store = Store(tmp_path)
    # 0.25× the AZI setup → the $6.13 fill becomes ~$1.53, below the $2 floor but above the old $1.
    _seed_premarket(
        store, oid_time_utc=datetime(2026, 6, 29, 12, 0, tzinfo=ET_UTC), price_scale=0.25
    )

    assert extract_day_trades(store, _s(), day) == []
    cands = extract_day_trades(store, _s(portfolio_entry_price_min=1.0), day)
    assert [c.symbol for c in cands] == ["AZI"]
    assert 1.0 <= cands[0].entry_fill < 2.0


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


# --- --- Market-data fee + settled-cash invariant (#232, #234) -------------------------


def test_data_fee_charged_at_month_rollover_when_under_waiver() -> None:
    """A quiet month bills the $10 subscription; it lands in equity, not just in the stats."""
    win = [_bar(10, 12.5, 9.95, 12.3)]
    days = [
        (date(2026, 6, 29), [_cand("AAA", 5, 10.0, 9.0, win)]),
        (date(2026, 7, 14), [_cand("BBB", 5, 10.0, 9.0, win)]),  # new month -> June settles
    ]
    # VPS zeroed so this isolates the *market-data* fee's effect on equity (VPS has its own tests).
    res = simulate_portfolio(days, _s(portfolio_vps_gbp_per_month=0.0), target_r=2.0)
    # June's commission ($0.70) is nowhere near the $30 waiver, and so is July's -> both billed.
    assert res.data_fees_usd == 20.0
    gross_net = sum(t.net_pnl_usd for t in res.trades)
    assert res.end_equity == round(500.0 + gross_net - 20.0, 4)
    assert res.total_costs_usd == round(res.commission_usd + res.fees_usd + 20.0, 4)


def test_data_fee_waived_when_month_clears_commission_threshold() -> None:
    """Above the threshold the subscription is free — model the waiver, don't over-charge."""
    win = [_bar(10, 12.5, 9.95, 12.3)]
    days = [(date(2026, 7, 14), [_cand("AAA", 5, 10.0, 9.0, win)])]
    # Drop the waiver below this month's commission ($0.70) -> waived.
    res = simulate_portfolio(days, _s(portfolio_market_data_waiver_usd=0.5), target_r=2.0)
    assert res.data_fees_usd == 0.0


def test_data_fee_compounds_into_sizing() -> None:
    """The fee must reduce the NEXT day's opening equity, hence its position size.

    Applied as a post-pass it would flatter the book: sizing is capital-based, so a $10 fee that
    doesn't compound leaves every later position too large. Priced at $5/share so the $10 fee
    actually crosses a whole-share boundary (~$245 vs ~$255 of buying power -> 48 vs 49 shares);
    at $10/share it wouldn't, and the test would pass vacuously."""
    win = [_bar(5, 6.5, 4.95, 6.3)]
    flat = [_bar(5, 5.05, 4.95, 5.0)]  # no-op day: marks to close ~flat
    days = [
        (date(2026, 6, 30), [_cand("AAA", 5, 5.0, 4.5, flat)]),
        (date(2026, 7, 1), [_cand("BBB", 5, 5.0, 4.5, win)]),  # new month -> June's fee settles
    ]
    charged = simulate_portfolio(days, _s(), target_r=2.0)
    free = simulate_portfolio(days, _s(portfolio_market_data_usd_per_month=0.0), target_r=2.0)
    # July's trade sizes off a $10-lighter account, so it buys strictly fewer shares.
    assert charged.trades[1].qty < free.trades[1].qty


def test_settled_cash_invariant_holds_by_construction() -> None:
    """#232 §6: total daily buy notional must not exceed the day's OPENING settled cash.

    The book never simulates settlement — the 50% × 2/day cap *is* the constraint. This pins that
    the config can't drift into a book the cash account couldn't actually have traded."""
    s = _s()
    assert s.portfolio_position_fraction * s.portfolio_max_trades_per_day <= 1.0

    win = [_bar(10, 12.5, 9.95, 12.3)]
    cands = [_cand("AAA", 5, 10.0, 9.0, win), _cand("BBB", 6, 10.0, 9.0, win)]
    res = simulate_portfolio([(date(2026, 7, 14), cands)], s, target_r=2.0)
    spent = sum(t.qty * t.entry_price for t in res.trades)
    assert spent <= s.portfolio_start_equity_usd  # 2 × 25sh × $10 = $500 exactly, never more


# --- --- Getting-paid layer: VPS ledger ------------------------------------------------


def _pay_settings(**overrides: object) -> Settings:
    """Clean 1.25 FX rate with the noise fees off, for exact getting-paid arithmetic."""
    base: dict[str, object] = {
        "portfolio_gbpusd_rate": 1.25,
        "portfolio_market_data_usd_per_month": 0.0,
        "portfolio_vps_gbp_per_month": 0.0,
    }
    base.update(overrides)
    return _s(**base)


def test_vps_ledger_charges_every_month_in_gbp_and_usd() -> None:
    """The box bills monthly whether or not it traded; £ is converted to $ at the assumed rate."""
    led = _VpsLedger(_s(portfolio_vps_gbp_per_month=10.0, portfolio_gbpusd_rate=1.25))
    assert led.roll(date(2026, 1, 10)) == 0.0  # first month just anchors
    assert led.roll(date(2026, 1, 20)) == 0.0  # still January
    assert led.roll(date(2026, 2, 5)) == 12.5  # February opens -> January's £10 settles at $12.50
    assert led.close(date(2026, 2, 28)) == 12.5  # final (February) month settles at close
    assert led.total_gbp == 20.0 and led.total_usd == 25.0
    assert [(e.kind, e.usd, e.gbp) for e in led.events] == [
        ("vps", 12.5, 10.0),
        ("vps", 12.5, 10.0),
    ]


def test_vps_cost_folds_into_equity_and_is_separate_from_broker_costs() -> None:
    win = [_bar(10, 12.5, 9.95, 12.3)]
    days = [
        (date(2026, 6, 29), [_cand("AAA", 5, 10.0, 9.0, win)]),
        (date(2026, 7, 14), [_cand("BBB", 5, 10.0, 9.0, win)]),  # new month -> June's VPS settles
    ]
    s = _s(
        portfolio_market_data_usd_per_month=0.0,
        portfolio_vps_gbp_per_month=10.0,
        portfolio_gbpusd_rate=1.25,
    )
    res = simulate_portfolio(days, s, target_r=2.0)
    assert res.vps_costs_gbp == 20.0 and res.vps_costs_usd == 25.0  # June + July, £10 each at 1.25
    gross_net = sum(t.net_pnl_usd for t in res.trades)
    assert res.end_equity == round(500.0 + gross_net - 25.0, 4)  # VPS folded into the balance
    assert res.total_costs_usd == round(
        res.commission_usd + res.fees_usd, 4
    )  # VPS is NOT a broker cost


# --- --- Getting-paid layer: CGT ledger ------------------------------------------------


def test_tax_ledger_zero_below_annual_allowance() -> None:
    s = _pay_settings()  # 24% rate, £3,000 allowance, rate 1.25
    led = _TaxLedger(s)
    led.roll(date(2026, 1, 1))  # anchor the tax year
    led.observe([SimpleNamespace(net_pnl_usd=1000.0)])  # £800 gain < £3,000 allowance
    assert led.reserve_usd() == 0.0
    assert led.close(date(2026, 3, 1)) == 0.0


def test_tax_ledger_reserves_cgt_above_allowance() -> None:
    s = _pay_settings()
    led = _TaxLedger(s)
    led.roll(date(2026, 1, 1))
    led.observe([SimpleNamespace(net_pnl_usd=5000.0)])  # £4,000 gain; taxable £1,000
    assert led.reserve_usd() == 300.0  # £1,000 × 24% = £240 -> $300 at 1.25
    fee = led.close(date(2026, 3, 1))
    assert fee == 300.0
    assert led.total_gbp == 240.0 and led.total_usd == 300.0
    assert [(e.kind, e.usd, e.gbp) for e in led.events] == [("tax", 300.0, 240.0)]


def test_tax_ledger_losses_reduce_the_years_gain() -> None:
    s = _pay_settings()
    led = _TaxLedger(s)
    led.roll(date(2026, 1, 1))
    led.observe([SimpleNamespace(net_pnl_usd=5000.0)])  # +£4,000
    led.observe([SimpleNamespace(net_pnl_usd=-2000.0)])  # -£1,600 -> net £2,400 < allowance
    assert led.reserve_usd() == 0.0


def test_tax_ledger_settles_and_resets_at_the_6_april_boundary() -> None:
    s = _pay_settings()
    led = _TaxLedger(s)
    led.roll(date(2025, 5, 1))  # anchors tax year starting 2025-04-06
    led.observe([SimpleNamespace(net_pnl_usd=5000.0)])  # £4,000 gain in year one
    fee = led.roll(date(2026, 4, 10))  # crosses 2026-04-06 -> year one settles
    assert round(fee, 4) == 300.0
    assert led.reserve_usd() == 0.0  # the new year starts clean
    led.observe([SimpleNamespace(net_pnl_usd=5000.0)])  # year two accrues independently
    assert led.reserve_usd() == 300.0


# --- --- Getting-paid layer: withdrawal ledger -----------------------------------------


def test_withdrawal_pays_fraction_of_profit_above_hwm_then_ratchets() -> None:
    s = _pay_settings(
        portfolio_start_equity_usd=10000.0,
        portfolio_withdraw_floor_usd=2000.0,
        portfolio_withdraw_fraction=0.5,
        portfolio_withdraw_cadence_months=3,
    )
    led = _WithdrawalLedger(s)
    assert led.roll(date(2026, 1, 15), 10000.0, 0.0) == 0.0  # first eval just anchors the cadence
    assert led.roll(date(2026, 2, 15), 12000.0, 0.0) == 0.0  # only 1 month elapsed (< 3)
    paid = led.roll(date(2026, 4, 15), 12000.0, 0.0)  # 3 months -> pay 50% of the £2,000 profit
    assert paid == 1000.0
    assert led.total_usd == 1000.0 and led.total_gbp == 800.0  # $1,000 / 1.25
    # HWM ratcheted to the post-withdrawal balance ($11,000): no new profit -> no further payout.
    assert led.roll(date(2026, 7, 15), 11000.0, 0.0) == 0.0


def test_withdrawal_is_noop_below_floor_or_underwater() -> None:
    below = _WithdrawalLedger(
        _pay_settings(portfolio_start_equity_usd=500.0, portfolio_withdraw_floor_usd=2000.0)
    )
    below.roll(date(2026, 1, 15), 500.0, 0.0)  # anchor
    assert below.roll(date(2026, 4, 15), 1500.0, 0.0) == 0.0  # equity under the $2,000 floor

    under = _WithdrawalLedger(
        _pay_settings(portfolio_start_equity_usd=10000.0, portfolio_withdraw_floor_usd=2000.0)
    )
    under.roll(date(2026, 1, 15), 10000.0, 0.0)  # anchor, HWM = 10,000
    assert under.roll(date(2026, 4, 15), 9000.0, 0.0) == 0.0  # below the high-water mark


def test_withdrawal_holds_back_the_outstanding_tax_reserve() -> None:
    s = _pay_settings(
        portfolio_start_equity_usd=10000.0,
        portfolio_withdraw_floor_usd=2000.0,
        portfolio_withdraw_fraction=0.5,
        portfolio_withdraw_cadence_months=3,
    )
    led = _WithdrawalLedger(s)
    led.roll(date(2026, 1, 15), 10000.0, 0.0)  # anchor
    # Profit $2,000 -> would pay $1,000, but available = 12,000 - 2,000 floor - 9,500 reserve = 500.
    assert led.roll(date(2026, 4, 15), 12000.0, 9500.0) == 500.0


# --- --- Getting-paid layer: end-to-end wiring + metrics -------------------------------


def test_portfolio_quarterly_withdrawal_and_return_adds_it_back() -> None:
    win = [_bar(10, 12.5, 9.95, 12.3)]  # +2R -> exit at $12
    s = _pay_settings(
        portfolio_start_equity_usd=10000.0,
        portfolio_withdraw_floor_usd=2000.0,
        portfolio_withdraw_fraction=0.5,
        portfolio_withdraw_cadence_months=3,
    )
    days = [
        (date(2026, 6, 15), [_cand("AAA", 5, 10.0, 9.0, win)]),  # books the quarter's profit
        (date(2026, 9, 15), []),  # quarter boundary (no 6-Apr crossing) -> withdrawal fires
    ]
    res = simulate_portfolio(days, s, target_r=2.0)
    profit = res.trades[0].net_pnl_usd  # equity climbs by this over the quarter
    assert res.withdrawals_usd == round(0.5 * profit, 4)
    assert res.net_take_home_gbp == round(res.withdrawals_usd / 1.25, 4)
    assert res.withdrawals_gbp == res.net_take_home_gbp
    assert [cf.kind for cf in res.cash_flows] == ["withdrawal"]
    assert res.cash_flows[0].date == date(2026, 9, 15)
    # Total-value return adds the withdrawn cash back, so paying yourself doesn't read as a loss.
    assert res.return_pct == round(profit / 10000.0, 4)
    assert res.end_equity == round(10000.0 + profit - res.withdrawals_usd, 4)
    # The scheduled cash-out is not a trading drawdown — the P&L path only ever rose here.
    assert res.max_drawdown_pct == 0.0


def test_getting_paid_layer_is_noop_at_the_default_500_account() -> None:
    """At $500 the floor gates withdrawals and gains sit far below the CGT allowance: pure no-op."""
    win = [_bar(10, 12.5, 9.95, 12.3)]
    days = [
        (date(2026, 1, 15), [_cand("AAA", 5, 10.0, 9.0, win)]),
        (date(2026, 4, 20), [_cand("BBB", 5, 10.0, 9.0, win)]),  # crosses a quarter and 6 April
    ]
    res = simulate_portfolio(
        days,
        _s(portfolio_market_data_usd_per_month=0.0, portfolio_vps_gbp_per_month=0.0),
        target_r=2.0,
    )
    assert res.withdrawals_usd == 0.0  # equity never clears the $2,000 floor
    assert res.tax_paid_usd == 0.0  # gains are a rounding error against the £3,000 allowance
    assert res.cash_flows == ()


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


# --- gap-month billing (#249) ------------------------------------------------------------------


def _trade_with_commission(usd: float) -> object:
    """_DataFeeLedger.observe only reads commission_usd — no need for a whole PaperTrade."""
    return SimpleNamespace(commission_usd=usd)


def test_vps_ledger_charges_months_with_no_collected_data() -> None:
    """A data outage is not a free month — the box bills regardless (#249).

    June data, then September data: the old ledger settled once on the observed June->September
    rollover and re-anchored, silently dropping July and August entirely.
    """
    led = _VpsLedger(_s(portfolio_vps_gbp_per_month=10.0, portfolio_gbpusd_rate=1.25))
    assert led.roll(date(2026, 6, 10)) == 0.0  # anchor on June
    charged = led.roll(date(2026, 9, 3))  # next data is September: Jun + Jul + Aug all due

    assert charged == 37.5  # 3 x $12.50, not 1
    assert led.total_gbp == 30.0 and led.total_usd == 37.5
    # Each gap month gets its own dated cash flow, at the start of the month it rolls into; the
    # final transition keeps the observed day, so a gapless run is billed exactly as before.
    assert [(e.date, e.usd) for e in led.events] == [
        (date(2026, 7, 1), 12.5),
        (date(2026, 8, 1), 12.5),
        (date(2026, 9, 3), 12.5),
    ]


def test_vps_ledger_walks_a_year_boundary() -> None:
    led = _VpsLedger(_s(portfolio_vps_gbp_per_month=10.0, portfolio_gbpusd_rate=1.0))
    assert led.roll(date(2026, 11, 20)) == 0.0
    assert led.roll(date(2027, 2, 4)) == 30.0  # Nov, Dec, Jan
    assert [e.date for e in led.events] == [date(2026, 12, 1), date(2027, 1, 1), date(2027, 2, 4)]


def test_vps_ledger_gapless_months_unchanged() -> None:
    """The no-gap path must bill exactly as it did before the walk was introduced."""
    led = _VpsLedger(_s(portfolio_vps_gbp_per_month=10.0, portfolio_gbpusd_rate=1.25))
    assert led.roll(date(2026, 1, 10)) == 0.0
    assert led.roll(date(2026, 2, 5)) == 12.5
    assert led.roll(date(2026, 2, 20)) == 0.0  # same month, no double charge
    assert [e.date for e in led.events] == [date(2026, 2, 5)]


def test_data_fee_ledger_charges_gap_months_unwaived() -> None:
    """Gap months carry no commission, so no waiver can apply to them (#249)."""
    s = _s(
        portfolio_market_data_usd_per_month=10.0,
        portfolio_market_data_waiver_usd=30.0,
    )
    led = _DataFeeLedger(s)
    assert led.roll(date(2026, 6, 1)) == 0.0  # anchor June
    led.observe([_trade_with_commission(50.0)])  # June clears the waiver

    charged = led.roll(date(2026, 9, 2))

    # June waived (commission >= 30), July + August charged (no data, no commission, no waiver).
    assert charged == 20.0
    assert led.total_charged == 20.0


def test_data_fee_ledger_out_of_order_day_does_not_loop() -> None:
    """Days arrive ascending; an earlier day must not walk backwards forever."""
    led = _DataFeeLedger(_s(portfolio_market_data_usd_per_month=10.0))
    assert led.roll(date(2026, 6, 5)) == 0.0
    assert led.roll(date(2026, 5, 5)) == 0.0  # regression, ignored rather than hanging
    assert led.total_charged == 0.0


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
    res = simulate_portfolio([(date(2026, 7, 14), cands)], _s(), target_r=2.0)

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
    s = _s()
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
