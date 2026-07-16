"""Tests for the virtual-portfolio tracker (#230) — the paper-book trading logic.

The exit simulator + sizing + selection are the product here, so they're exercised exhaustively:
target hit, stop, breakeven scratch, gap-through, mark-to-close, and the day-level 2-trade
capacity / opening-equity sizing rules.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from small_cap_stack.capture import Bar
from small_cap_stack.config import Settings
from small_cap_stack.portfolio import (
    CandidateTrade,
    _TaxLedger,
    _VpsLedger,
    _WithdrawalLedger,
    best_target,
    commission,
    expectancy_curve,
    simulate_exit,
    simulate_portfolio,
    size_position,
    trade_costs,
)

ET = ZoneInfo("America/New_York")
ET_UTC = UTC  # seeds store timestamps in UTC (the store's native tz), like test_report


def _s(**overrides: object) -> Settings:
    return Settings(_env_file=None, **overrides)  # type: ignore[call-arg]


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
    return size_position(equity, entry, stop, risk_fraction=0.05, max_position_fraction=0.50)


def test_size_position_risk_target_binds_on_tight_stop() -> None:
    # $500 eq, 5% risk = $25. Entry 10 / stop 9.5 -> risk/sh $0.50 -> floor(25/0.5)=50 by risk,
    # but the 50% cap is floor(250/10)=25 -> the CAP binds (25 < 50).
    assert _size(500.0, 10.0, 9.5) == 25
    # Entry 10 / stop 5 -> risk/sh $5 -> floor(25/5)=5 by risk; cap floor(250/10)=25 -> RISK binds.
    assert _size(500.0, 10.0, 5.0) == 5


def test_size_position_notional_cap_binds_on_wide_stop() -> None:
    # Entry 3 / stop 2 -> risk/sh $1 -> floor(25/1)=25 by risk; cap floor(250/3)=83 -> RISK binds
    # (the cheap stock is risk-limited, not capital-limited, so it no longer buys 83 shares).
    assert _size(500.0, 3.0, 2.0) == 25
    # Entry 20 / stop 19 -> risk floor(25/1)=25; cap floor(250/20)=12 -> the CAP binds.
    assert _size(500.0, 20.0, 19.0) == 12


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
    assert size_position(500.0, 10.0, 10.0, risk_fraction=0.05, max_position_fraction=0.50) == 25


def test_commission_respects_minimum() -> None:
    assert commission(50, 0.0035, 0.35) == 0.35  # 50 × 0.0035 = 0.175 -> min 0.35
    assert commission(200, 0.0035, 0.35) == 0.70  # 200 × 0.0035 = 0.70 > min


# --- --- portfolio simulation ---------------------------------------------------------


def _cand(sym: str, minute: int, entry: float, stop: float, bars: list[Bar]) -> CandidateTrade:
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
    # Warm-up trades reach exactly +2R favourable (high 12) then close, so over the trailing window
    # target 1.5 hits (+1.5R each) and target 3.0 never hits (marks to close at +2R) -> 3.0 wins
    # expectancy. The decision day must therefore be taken at 3.0, not the 2.0 fallback.
    reach2 = [_bar(10, 12.0, 9.95, 12.0)]  # favourable to +2R then closes at +2R
    s = _s(
        portfolio_target_grid=(1.5, 3.0),
        portfolio_adaptive_min_samples=6,
        portfolio_adaptive_window_days=90,
        portfolio_exit_slippage_ticks=0,
    )
    base = date(2026, 7, 1)
    days = [(base + timedelta(days=i), [_cand(f"W{i}", 5, 10.0, 9.0, reach2)]) for i in range(6)]
    days.append((base + timedelta(days=6), [_cand("DEC", 5, 10.0, 9.0, reach2)]))

    res, chosen = simulate_portfolio_adaptive(days, s)
    per_day = dict(chosen)
    assert per_day[base] == s.portfolio_target_r  # day 0: no trailing samples -> fallback (2.0)
    assert per_day[base + timedelta(days=6)] == 3.0  # decision day: re-fit to the best trailing T
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


def test_qualify_rejects_in_session_and_out_of_band() -> None:
    # A direct check that the selection predicate enforces strict pre-market + the price band.
    from small_cap_stack.portfolio import _qualify

    s = _s()
    pre = [_bar(10, 10.1, 9.9, 10.0, hour=9, minute=15)]  # 09:15 ET -> pre-market
    intr = [_bar(10, 10.1, 9.9, 10.0, hour=9, minute=45)]  # 09:45 ET -> in-session
    assert _qualify(0, 10.0, 10.0, 9.0, 1.0, True, pre, s) is True
    assert _qualify(0, 10.0, 10.0, 9.0, 1.0, True, intr, s) is False  # after 09:30
    assert _qualify(0, 25.0, 25.0, 24.0, 1.0, True, pre, s) is False  # entry_fill 25 > $20 band
    assert _qualify(0, 10.0, 10.0, 9.0, 1.0, False, pre, s) is False  # not takeable


# --- extraction (store integration; reuses the report seams) ---------------------------


def _seed_premarket(store: object, *, oid_time_utc: datetime) -> None:
    """Seed a clean pre-market bull flag (AZI, triggers to ~2.8R) + a no-setup name (DUD).

    ``oid_time_utc`` is the first bar / first_hit; 12:00 UTC = 08:00 ET (EDT) → strictly pre-market;
    16:00 UTC = 12:00 ET → in-session, which the pre-market filter must reject."""
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
            "open": o,
            "high": h,
            "low": low,
            "close": c,
            "volume": v,
        }

    oid = f"{day.isoformat()}:AZI"
    store.append(
        "opportunities",
        [
            {
                "opportunity_id": oid,
                "symbol": "AZI",
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
            bar_row(oid, "AZI", 0, 5.0, 5.8, 4.6, 5.7),  # launch (green)
            bar_row(oid, "AZI", 1, 5.7, 6.5, 5.6, 6.4, 2000),  # higher-high pole
            bar_row(oid, "AZI", 2, 6.4, 6.1, 5.6, 5.7),  # flag (red)
            bar_row(oid, "AZI", 3, 5.7, 7.64, 5.7, 7.5),  # trigger + Max R ~2.8
        ],
        partition_date=day,
    )
    store.append(
        "scanner_hits",
        [{"opportunity_id": oid, "symbol": "AZI", "ts_utc": t0, "rank": 0}],
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
    trade = adaptive["trades"][0]
    assert trade["symbol"] == "AZI" and trade["reason"] == "target"
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

    monkeypatch.setattr(pf, "extract_day_trades", _boom)  # type: ignore[attr-defined]
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

    def _spy(store: object, s: object, d: date) -> list[CandidateTrade]:
        calls.append(d)
        return real(store, s, d)  # type: ignore[arg-type]

    monkeypatch.setattr(pf, "extract_day_trades", _spy)  # type: ignore[attr-defined]
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

    def _spy(store: object, s: object, d: date) -> list[CandidateTrade]:
        calls.append(d)
        return real(store, s, d)  # type: ignore[arg-type]

    monkeypatch.setattr(pf, "extract_day_trades", _spy)  # type: ignore[attr-defined]
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

    def _spy(store: object, s: object, d: date) -> list[CandidateTrade]:
        calls.append(d)
        return real(store, s, d)  # type: ignore[arg-type]

    monkeypatch.setattr(pf, "extract_day_trades", _spy)  # type: ignore[attr-defined]
    # force_dates re-extracts even on a valid cache (the day whose raw data the caller just changed)
    pf.build_portfolio_payload(store, _s(), now, cache_dir=cache_dir, force_dates={day})
    assert calls == [day]
