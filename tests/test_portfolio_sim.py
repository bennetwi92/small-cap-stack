"""Paper-book trading logic (#230): costs, the exit simulator, sizing, the day loop.

Split out of the 2,412-line `test_portfolio.py` in #529. This half is pure computation — no store,
no payload: `trade_costs`/`commission`, `simulate_exit`'s target/stop/breakeven/gap-through/
mark-to-close cases, `size_position`'s risk-vs-notional constraint, `simulate_portfolio` and its
adaptive variant, the risk ladder (#239), and the next-session state (#286).
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from small_cap_stack.portfolio import (
    CandidateTrade,
    SizedPosition,
    _select_day,
    _take_day,
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
from tests.support import (
    candidate as _cand,
)
from tests.support import (
    et_bar as _bar,
)
from tests.support import (
    portfolio_settings as _s,
)

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


def test_portfolio_caps_at_two_trades_per_day_by_trigger_time() -> None:
    # Cap explicit, not the shipped default (D-45 shrank that to 1) — this test is about the
    # mechanism at N=2, not about what currently ships.
    s = _s(portfolio_max_trades_per_day=2)
    win = [_bar(10, 12.5, 9.95, 12.3)]  # hits 2R
    cands = [
        _cand("AAA", 5, 10.0, 9.0, win),
        _cand("BBB", 6, 10.0, 9.0, win),
        _cand("CCC", 7, 10.0, 9.0, win),  # 3rd by time -> dropped (capacity 2)
    ]
    res = simulate_portfolio([(date(2026, 7, 14), cands)], s, target_r=2.0)
    assert res.n_trades == 2
    assert {t.symbol for t in res.trades} == {"AAA", "BBB"}


def test_portfolio_records_setups_dropped_by_the_daily_cap() -> None:
    # Three qualifying setups, cap 2 (explicit — see the test above) -> the 3rd (by trigger time)
    # is skipped, and the book records what it *would* have made at the day's target so the page
    # can show what the cap cost.
    s = _s(portfolio_max_trades_per_day=2)
    win = [_bar(10, 12.5, 9.95, 12.3)]  # hits +2R
    loss = [_bar(10, 10.3, 8.8, 9.0)]  # stops at 9.0 -> -1R
    cands = [
        _cand("AAA", 5, 10.0, 9.0, win),
        _cand("BBB", 6, 10.0, 9.0, win),
        _cand("CCC", 7, 10.0, 9.0, loss),  # 3rd by time -> skipped, would have been -1R
    ]
    res = simulate_portfolio([(date(2026, 7, 14), cands)], s, target_r=2.0)
    assert res.n_trades == 2
    assert [sk.symbol for sk in res.skipped] == ["CCC"]
    sk = res.skipped[0]
    # Simulated with the exact same exit model a taken trade would use (target + 2-tick stop slip).
    would_be = cands[2].exit_under(s, 2.0, 0.0)
    assert sk.reason == "stop" and sk.realized_r == would_be.realized_r < 0
    assert sk.target_r == 2.0  # simulated at the same target the day was taken at
    assert res.skipped_total_r == sk.realized_r
    # A skipped setup never touches equity or the trade stats — it's an informational log only.
    assert all(t.symbol != "CCC" for t in res.trades)


def test_portfolio_no_skips_when_under_the_cap() -> None:
    s = _s(portfolio_max_trades_per_day=2)
    win = [_bar(10, 12.5, 9.95, 12.3)]
    cands = [_cand("AAA", 5, 10.0, 9.0, win), _cand("BBB", 6, 10.0, 9.0, win)]
    res = simulate_portfolio([(date(2026, 7, 14), cands)], s, target_r=2.0)
    assert res.skipped == () and res.skipped_total_r == 0.0


# --- --- daily loss limit (#650, ships disabled) --------------------------------------


def test_daily_loss_limit_is_off_by_default() -> None:
    s = _s(portfolio_max_trades_per_day=2)  # explicit: both candidates must be takeable to prove
    # a loser doesn't stop the (disabled) day, independent of the D-45 daily-cap default
    assert s.portfolio_daily_loss_limit_r == 0.0
    loss = [_bar(10, 10.3, 8.8, 9.0)]  # stops at -1R
    win = [_bar(10, 12.5, 9.95, 12.3)]  # +2R
    cands = [_cand("AAA", 5, 10.0, 9.0, loss), _cand("BBB", 10, 10.0, 9.0, win)]
    trades, skipped = _take_day(cands, 500.0, s, 2.0, 0.0)
    assert {t.symbol for t in trades} == {"AAA", "BBB"}  # a loser never stops the (disabled) day
    assert skipped == []


def test_daily_loss_limit_stops_the_day_after_a_resolved_loss() -> None:
    s = _s(
        portfolio_daily_loss_limit_r=1.0,
        portfolio_exit_slippage_ticks=0,
        portfolio_max_trades_per_day=2,
    )
    loss = [
        _bar(10, 10.3, 9.9, 10.1, minute=0),
        _bar(10.1, 10.2, 8.8, 9.0, minute=5),  # breaks the 9.0 stop on bar 2 -> exit_index 1
    ]
    win = [_bar(10, 12.5, 9.95, 12.3, minute=20)]
    first = _cand("AAA", 5, 10.0, 9.0, loss)  # triggers 08:05
    # AAA's exit bar starts 08:05 + a 5-min bar_interval -> exit_end 08:10, strictly before BBB.
    second = _cand("BBB", 15, 10.0, 9.0, win)  # triggers 08:15
    trades, skipped = _take_day([first, second], 500.0, s, 2.0, 0.0)
    assert [t.symbol for t in trades] == ["AAA"]
    assert [(sk.symbol, sk.skip_reason) for sk in skipped] == [("BBB", "day_stopped")]


def test_daily_loss_limit_ignores_a_trade_still_open_at_the_next_trigger() -> None:
    # The no-lookahead case: same -1R AAA, but BBB triggers at 08:08 -- BEFORE AAA's 08:10 exit --
    # so AAA's loss is not yet knowable and must not count against BBB.
    s = _s(
        portfolio_daily_loss_limit_r=1.0,
        portfolio_exit_slippage_ticks=0,
        portfolio_max_trades_per_day=2,
    )
    loss = [
        _bar(10, 10.3, 9.9, 10.1, minute=0),
        _bar(10.1, 10.2, 8.8, 9.0, minute=5),  # exit_index 1 -> exit_end 08:10
    ]
    win = [_bar(10, 12.5, 9.95, 12.3, minute=8)]
    first = _cand("AAA", 5, 10.0, 9.0, loss)  # triggers 08:05
    second = _cand("BBB", 8, 10.0, 9.0, win)  # triggers 08:08, still concurrent with AAA
    trades, skipped = _take_day([first, second], 500.0, s, 2.0, 0.0)
    assert {t.symbol for t in trades} == {"AAA", "BBB"}
    assert skipped == []


def test_daily_loss_limit_leaves_the_taken_plus_skipped_invariant_intact() -> None:
    # 3 candidates, cap raised to 3 so the cap isn't what stops the later two -- the day-loss rule
    # is. len(trades) + len(skipped) == len(cands) must hold with the new fifth door in play.
    s = _s(
        portfolio_daily_loss_limit_r=1.0,
        portfolio_exit_slippage_ticks=0,
        portfolio_max_trades_per_day=3,
    )
    loss = [
        _bar(10, 10.3, 9.9, 10.1, minute=0),
        _bar(10.1, 10.2, 8.8, 9.0, minute=5),  # exit_end 08:10
    ]
    win = [_bar(10, 12.5, 9.95, 12.3, minute=20)]
    cands = [
        _cand("AAA", 5, 10.0, 9.0, loss),  # -1R, resolves 08:10
        _cand("BBB", 15, 10.0, 9.0, win),  # triggers 08:15, after AAA resolved -> stopped
        _cand("CCC", 20, 10.0, 9.0, win),  # triggers 08:20, still stopped
    ]
    trades, skipped = _take_day(cands, 500.0, s, 2.0, 0.0)
    assert len(trades) + len(skipped) == len(cands)
    assert [t.symbol for t in trades] == ["AAA"]
    assert {sk.skip_reason for sk in skipped} == {"day_stopped"}


def test_day_stopped_skips_do_not_count_toward_skipped_total_r() -> None:
    s = _s(
        portfolio_daily_loss_limit_r=1.0,
        portfolio_exit_slippage_ticks=0,
        portfolio_max_trades_per_day=2,
    )
    loss = [
        _bar(10, 10.3, 9.9, 10.1, minute=0),
        _bar(10.1, 10.2, 8.8, 9.0, minute=5),  # exit_end 08:10
    ]
    win = [_bar(10, 12.5, 9.95, 12.3, minute=20)]
    cands = [_cand("AAA", 5, 10.0, 9.0, loss), _cand("BBB", 15, 10.0, 9.0, win)]
    res = simulate_portfolio([(date(2026, 7, 14), cands)], s, target_r=2.0)
    assert [sk.skip_reason for sk in res.skipped] == ["day_stopped"]
    assert res.skipped_total_r == 0.0  # skipped_total_r stays cap-only


def test_a_winning_first_trade_does_not_stop_the_day() -> None:
    s = _s(
        portfolio_daily_loss_limit_r=1.0,
        portfolio_exit_slippage_ticks=0,
        portfolio_max_trades_per_day=2,
    )
    win = [_bar(10, 12.5, 9.95, 12.3, minute=0)]
    cands = [_cand("AAA", 5, 10.0, 9.0, win), _cand("BBB", 15, 10.0, 9.0, win)]
    trades, skipped = _take_day(cands, 500.0, s, 2.0, 0.0)
    assert {t.symbol for t in trades} == {"AAA", "BBB"}
    assert skipped == []


def test_portfolio_both_trades_size_off_opening_equity() -> None:
    # $500 open. Entry 10 / stop 9 -> risk/sh $1 -> 5% risk floor(25/1)=25; 50% cap floor(250/10)=25
    # (they coincide here) -> 25 shares each, regardless of the first trade's outcome.
    win = [_bar(10, 12.5, 9.95, 12.3)]
    cands = [_cand("AAA", 5, 10.0, 9.0, win), _cand("BBB", 6, 10.0, 9.0, win)]
    s = _s(portfolio_max_trades_per_day=2)
    res = simulate_portfolio([(date(2026, 7, 14), cands)], s, target_r=2.0)
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
    from small_cap_stack.portfolio import _day_signal_r

    s = _s(portfolio_exit_slippage_ticks=0, portfolio_max_trades_per_day=2)
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


def test_the_shipped_grid_is_a_single_value_so_the_optimiser_cannot_fire() -> None:
    """#644: the layer is retired by grid width, not by deleting it.

    Over 61 sessions the optimiser moved the target on 2 days, both wrongly, and its edge decayed
    to z=0.043. A one-value grid makes `best_target` a no-op, so every day runs
    `portfolio_target_r`. Re-enabling is this one field — which is the point of disabling it this
    way rather than by ripping the layer out.
    """
    assert _s().portfolio_target_grid == (2.0,)


def test_a_single_value_grid_holds_the_fallback_where_a_wider_one_would_switch() -> None:
    """The discriminating case: identical trades, identical guards, only the grid differs.

    `_win3_cand` runs to +3R, so a (1.5, 3.0) grid re-fits to 3.0 and clears the margin gate — the
    exact behaviour `test_next_session_state_target_uses_every_collected_day` pins. Under the
    shipped one-value grid the same history must never leave 2.0, and must never be *reported* as
    fitted either: a `TargetFit` claiming `fitted` while sitting on the fallback is precisely the
    failure #463 was raised for.
    """
    base = date(2026, 7, 1)
    days = [(base + timedelta(days=i), [_win3_cand(f"W{i}")]) for i in range(10)]
    common = {
        "portfolio_adaptive_min_samples": 6,
        "portfolio_target_r": 2.0,
        "portfolio_exit_slippage_ticks": 0,
    }

    wide = simulate_portfolio_adaptive(days, _s(portfolio_target_grid=(1.5, 3.0), **common))
    assert 3.0 in {f.target_r for _d, f in wide.daily_targets}  # the layer does fire when it can

    single = simulate_portfolio_adaptive(days, _s(portfolio_target_grid=(2.0,), **common))
    assert {f.target_r for _d, f in single.daily_targets} == {2.0}
    assert all(t.target_r == 2.0 for t in single.result.trades)
    # `thin` while warming up, then `fitted` at 2.0 — the optimiser runs and agrees with itself.
    # What it must never do is report a *switch*, which is what `target_r == 2.0` above pins.
    assert {f.status for _d, f in single.daily_targets} <= {"thin", "fitted"}
    st = single.state
    assert st is not None and st.target_r == 2.0  # and the next session inherits the same answer


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


def test_qualify_needs_takeable_and_usable_numbers() -> None:
    """What `_qualify` still decides after #567.

    Selection — the price band and the trigger-time window — moved into the engine and reaches
    here already folded into `takeable` (see `tests/test_bullflag_day.py`). What is left is the
    book's own question: are the numbers usable to size and simulate a position? So the price and
    time cases that used to live here are gone, deliberately, rather than duplicated in two layers.
    """
    from small_cap_stack.portfolio import _qualify

    assert _qualify(0, 10.0, 10.0, 9.0, 1.0, True) is True
    assert _qualify(0, 10.0, 10.0, 9.0, 1.0, False) is False  # engine didn't select it
    assert _qualify(None, 10.0, 10.0, 9.0, 1.0, True) is False  # never fired
    assert _qualify(0, None, 10.0, 9.0, 1.0, True) is False
    assert _qualify(0, 10.0, None, 9.0, 1.0, True) is False
    assert _qualify(0, 10.0, 10.0, None, 1.0, True) is False  # no stop -> no risk to size against
    assert _qualify(0, 10.0, 10.0, 9.0, None, True) is False
    assert _qualify(0, 10.0, 10.0, 9.0, 0.0, True) is False  # non-positive risk is unsizeable
    assert _qualify(0, 10.0, 10.0, 9.0, -1.0, True) is False
