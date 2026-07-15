"""Tests for the virtual-portfolio tracker (#230) — the paper-book trading logic.

The exit simulator + sizing + selection are the product here, so they're exercised exhaustively:
target hit, stop, breakeven scratch, gap-through, mark-to-close, and the day-level 2-trade
capacity / opening-equity sizing rules.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from small_cap_stack.capture import Bar
from small_cap_stack.config import Settings
from small_cap_stack.portfolio import (
    CandidateTrade,
    best_target,
    commission,
    expectancy_curve,
    simulate_exit,
    simulate_portfolio,
    size_position,
)

ET = ZoneInfo("America/New_York")
ET_UTC = UTC  # seeds store timestamps in UTC (the store's native tz), like test_report


def _s(**overrides: object) -> Settings:
    return Settings(_env_file=None, **overrides)  # type: ignore[call-arg]


def _bar(o: float, h: float, low: float, c: float, *, minute: int = 0, hour: int = 8) -> Bar:
    # ET-aware; hour defaults to 08:00 (pre-market) so trigger-time checks pass unless overridden.
    start = datetime(2026, 7, 14, hour, minute, tzinfo=ET)
    return Bar(start=start, open=o, high=h, low=low, close=c, volume=1000.0)


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


def test_size_position_floors_to_whole_shares() -> None:
    assert size_position(500.0, 3.0, 0.50) == 83  # 250 / 3 = 83.33 -> 83
    assert size_position(500.0, 20.0, 0.50) == 12  # 250 / 20 = 12.5 -> 12


def test_size_position_zero_when_unaffordable() -> None:
    assert size_position(500.0, 300.0, 0.50) == 0  # 250 < 300 -> can't afford a share


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


def test_portfolio_both_trades_size_off_opening_equity() -> None:
    # $500 open, 50% each = $250 -> floor(250/10)=25 shares each, regardless of the first's outcome.
    win = [_bar(10, 12.5, 9.95, 12.3)]
    cands = [_cand("AAA", 5, 10.0, 9.0, win), _cand("BBB", 6, 10.0, 9.0, win)]
    res = simulate_portfolio([(date(2026, 7, 14), cands)], _s(), target_r=2.0)
    assert [t.qty for t in res.trades] == [25, 25]


def test_portfolio_pnl_and_equity_bookkeeping() -> None:
    # single winner: 25 sh × (12.0 - 10.0) = $50 gross; commission 2 × max(0.35, 25×0.0035=0.0875)
    # = 2 × 0.35 = $0.70; net $49.30; equity 500 -> 549.30.
    win = [_bar(10, 12.5, 9.95, 12.3)]
    res = simulate_portfolio(
        [(date(2026, 7, 14), [_cand("AAA", 5, 10.0, 9.0, win)])], _s(), target_r=2.0
    )
    t = res.trades[0]
    assert t.qty == 25
    assert t.gross_pnl_usd == 50.0
    assert t.commission_usd == 0.70
    assert t.net_pnl_usd == 49.30
    assert res.end_equity == 549.30
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
