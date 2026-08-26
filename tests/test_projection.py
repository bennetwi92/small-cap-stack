"""Tests for the forward projection (`portfolio.projection`) — the only forward-looking book code.

Two things are the product here and get exercised hardest:

- the **path walk**, because it decides the drawdown and payout numbers a trader would size a life
  around, and because its failure mode is silent (a plausible-looking fan built on a wrong base);
- the **income arithmetic**, because it inverts a chain of tax/cost rules and an inversion that is
  quietly wrong still returns a confident number of years.

Everything is seeded, so every assertion below is exact rather than statistical.
"""

from __future__ import annotations

import random
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from small_cap_stack.capture import Bar
from small_cap_stack.config import Settings
from small_cap_stack.portfolio import (
    CandidateTrade,
    build_projection,
    capital_for_income,
    day_samples,
    future_sessions,
    simulate_portfolio,
    years_to_capital,
)
from small_cap_stack.portfolio.projection import (
    DaySample,
    _annualised,
    _draw_days,
    _pctile,
    _simulate_path,
    annual_fixed_costs_gbp,
    day_rate_net_annual_gbp,
    income_from_capital,
    income_ramp,
)
from tests.support import settings

ET = ZoneInfo("America/New_York")

# Small but real: enough paths for percentiles to be defined and cheap enough to run per test.
PATHS = 40
SESSIONS = 60


def _s(**overrides: object) -> Settings:
    defaults: dict[str, object] = {
        "portfolio_projection_paths": PATHS,
        "portfolio_projection_days": SESSIONS,
    }
    defaults.update(overrides)
    return settings(**defaults)


def _bar(d: date, o: float, h: float, low: float, c: float, *, minute: int = 0) -> Bar:
    return Bar(
        start=datetime(d.year, d.month, d.day, 8, minute, tzinfo=ET),
        open=o,
        high=h,
        low=low,
        close=c,
        volume=1000.0,
    )


def _cand(d: date, sym: str, minute: int, bars: list[Bar]) -> CandidateTrade:
    return CandidateTrade(
        trading_date=d,
        symbol=sym,
        seg_id=f"{d.isoformat()}:{sym}",
        run=1,
        trigger_at=datetime(d.year, d.month, d.day, 8, minute, tzinfo=ET),
        entry_price=10.0,
        entry_fill=10.0,
        stop=9.0,
        risk=1.0,
        entry_index=0,
        bars=tuple(bars),
    )


def _win(d: date, minute: int = 0) -> list[Bar]:
    return [_bar(d, 10, 12.6, 9.95, 12.4, minute=minute)]  # clears +2R


def _loss(d: date, minute: int = 0) -> list[Bar]:
    return [_bar(d, 10, 10.3, 8.8, 9.0, minute=minute)]  # stops out at −1R


def _book(win_rate: float, *, n_days: int = 30, seed: int = 3, s: Settings | None = None):  # type: ignore[no-untyped-def]
    """A synthetic historical book with a tunable edge — the thing the projection resamples."""
    st = s or _s()
    rng = random.Random(seed)
    days: list[tuple[date, list[CandidateTrade]]] = []
    d = date(2026, 6, 1)
    for _ in range(n_days):
        while d.weekday() >= 5:
            d += timedelta(days=1)
        cands = [
            _cand(d, f"S{k}", 5 + k, _win(d, k) if rng.random() < win_rate else _loss(d, k))
            for k in range(rng.choice([0, 1, 1, 2]))
        ]
        days.append((d, cands))
        d += timedelta(days=1)
    return simulate_portfolio(days, st, target_r=2.0)


# `win_rate` shifts the whole RNG stream (trade counts and win/loss draws are interleaved), so it
# does NOT by itself decide whether the realised book made money — the seed does. These two name
# the fixtures the tests actually mean, and each asserts its own character so a future change to
# `_book` fails loudly here instead of quietly making half the assertions below vacuous.
def _winning_book(s: Settings | None = None):  # type: ignore[no-untyped-def]
    res = _book(0.5, seed=5, s=s)
    assert res.end_equity > res.start_equity, "fixture drifted: expected a profitable book"
    return res


def _losing_book(s: Settings | None = None):  # type: ignore[no-untyped-def]
    res = _book(0.05, seed=3, s=s)
    assert res.end_equity < res.start_equity / 2, "fixture drifted: expected a badly losing book"
    return res


# --- --- Day samples: the base every projected return is measured against ----------------------


def test_day_samples_keeps_the_days_the_book_sat_out() -> None:
    # Sit-out days are most of the calendar and they set the CADENCE of everything downstream (how
    # long to the withdrawal floor, how many chances to draw a bad run). Dropping them would make
    # every projected year look like a year of nothing but trading days.
    res = _book(0.4, n_days=30)
    samples = day_samples(res)
    assert len(samples) == len(res.equity_curve)
    assert any(not sm.returns for sm in samples), "expected at least one no-trade day in the sample"
    assert [sm.day for sm in samples] == [d for d, _e in res.equity_curve]


def test_day_samples_divides_both_trades_by_the_days_opening_equity() -> None:
    # Both of a day's positions are sized off the day's OPEN. Using each trade's own `equity_before`
    # would divide the second trade by a base that already contains the first trade's P&L — a
    # silent, small, always-in-one-direction error in every projected return.
    d = date(2026, 7, 14)
    res = simulate_portfolio(
        [(d, [_cand(d, "AAA", 5, _win(d, 0)), _cand(d, "BBB", 6, _win(d, 1))])],
        _s(portfolio_max_trades_per_day=2),
    )
    assert res.n_trades == 2
    opening = res.trades[0].equity_before
    assert res.trades[1].equity_before != opening  # the second trade really does see a moved base
    sample = day_samples(res)[0]
    assert sample.returns == pytest.approx(tuple(t.net_pnl_usd / opening for t in res.trades))


def test_day_samples_commission_fraction_tracks_the_same_base() -> None:
    res = _book(0.5, n_days=20)
    traded = {sm.day: sm for sm in day_samples(res) if sm.returns}
    day = next(iter(traded))
    rows = [t for t in res.trades if t.trading_date == day]
    expected = sum(t.commission_usd for t in rows) / rows[0].equity_before
    assert traded[day].commission_frac == pytest.approx(expected)


# --- --- Sessions, quantiles, bootstrap --------------------------------------------------------


def test_future_sessions_are_weekday_trading_days_strictly_after_the_start() -> None:
    start = date(2026, 8, 3)  # a Monday
    out = future_sessions(start, 12, _s())
    assert len(out) == 12
    assert out[0] > start
    assert out == sorted(out)
    assert all(d.weekday() < 5 for d in out)


def test_future_sessions_skips_a_holiday_the_calendar_knows() -> None:
    # US Thanksgiving 2026 is 26 Nov. The exchange calendar is the whole reason this isn't a naive
    # weekday loop, so assert it is actually being consulted.
    out = future_sessions(date(2026, 11, 20), 6, _s())
    assert date(2026, 11, 26) not in out
    assert date(2026, 11, 25) in out and date(2026, 11, 27) in out


def test_future_sessions_honours_the_manual_closure_override() -> None:
    closed = date(2026, 8, 5)
    out = future_sessions(date(2026, 8, 3), 6, _s(calendar_closed_dates=(closed,)))
    assert closed not in out


def test_future_sessions_survives_past_the_calendars_published_horizon() -> None:
    # The calendar publishes ~1 year ahead and the default horizon is ~1 year, so the tail of a
    # projection can fall off the end. Degrading to weekdays is the documented behaviour; raising
    # would take the page down.
    out = future_sessions(date.today(), 400, _s())
    assert len(out) == 400
    assert all(d.weekday() < 5 for d in out)


def test_pctile_interpolates_and_handles_degenerate_inputs() -> None:
    assert _pctile([], 0.5) == 0.0
    assert _pctile([7.0], 0.9) == 7.0
    assert _pctile([0.0, 10.0], 0.5) == pytest.approx(5.0)
    assert _pctile([0.0, 1.0, 2.0, 3.0, 4.0], 0.25) == pytest.approx(1.0)
    assert _pctile([0.0, 10.0, 20.0], 0.75) == pytest.approx(15.0)


def test_draw_days_emits_contiguous_blocks_not_iid_days() -> None:
    # Block sampling is what keeps a losing run a losing run. If this ever degrades to i.i.d. days
    # the fan still renders and the projected drawdown quietly halves — so pin the shape.
    samples = day_samples(_book(0.4, n_days=20))
    drawn = _draw_days(samples, 12, 4, random.Random(1))
    assert len(drawn) == 12
    by_day = {sm.day: i for i, sm in enumerate(samples)}
    for start in range(0, 12, 4):  # each block of 4 walks consecutive sample indices (wrapping)
        idx = [by_day[sm.day] for sm in drawn[start : start + 4]]
        assert idx == [(idx[0] + k) % len(samples) for k in range(len(idx))]


def test_draw_days_block_of_one_is_plain_resampling() -> None:
    samples = day_samples(_book(0.4, n_days=20))
    assert len(_draw_days(samples, 7, 1, random.Random(1))) == 7


# --- --- The projection itself -----------------------------------------------------------------


def test_projection_is_unavailable_with_nothing_to_resample() -> None:
    empty = simulate_portfolio([], _s())
    out = build_projection(empty, _s())
    assert out["available"] is False and out["reason"]

    d = date(2026, 7, 14)
    no_trades = simulate_portfolio([(d, [])], _s())
    out = build_projection(no_trades, _s())
    # A fan chart drawn off zero trading days is a straight line asserting certainty — refuse.
    assert out["available"] is False and "resample" in str(out["reason"])


def test_projection_shape_and_band_ordering() -> None:
    out = build_projection(_book(0.45), _s())
    assert out["available"] is True
    assert out["sessions"] == SESSIONS
    assert out["paths"] == PATHS
    assert len(out["sample_days"]) == len(out["bands"]["p50"])
    assert out["sample_days"][-1] == out["end_date"]
    # Percentile bands must nest, at every point. A crossing means the transpose or the quantile is
    # wrong, and the chart would render a band inside-out without complaining.
    for i in range(len(out["sample_days"])):
        vals = [out["bands"][f"p{p}"][i] for p in (5, 25, 50, 75, 95)]
        assert vals == sorted(vals)
    ends = out["end_equity"]
    assert ends["p5"] <= ends["p25"] <= ends["p50"] <= ends["p75"] <= ends["p95"]
    assert 0.0 <= out["p_profit"] <= 1.0
    assert 0.0 <= out["drawdown"]["p50"] <= out["drawdown"]["p90"] <= out["drawdown"]["max"]


def test_projection_is_deterministic_for_a_fixed_seed() -> None:
    # publish-dashboard rebuilds every 15 minutes. An unseeded fan would move between publishes and
    # read as news when it was noise.
    res = _book(0.45)
    assert build_projection(res, _s()) == build_projection(res, _s())
    other = build_projection(res, _s(portfolio_projection_seed=999))
    assert other["bands"]["p50"] != build_projection(res, _s())["bands"]["p50"]


def test_projection_starts_from_the_books_closing_balance_and_the_next_session() -> None:
    res = _book(0.45)
    out = build_projection(res, _s())
    assert out["start_equity"] == pytest.approx(round(res.end_equity, 2))
    last_collected = res.equity_curve[-1][0]
    assert date.fromisoformat(str(out["start_date"])) > last_collected


def test_projection_never_reports_a_negative_balance() -> None:
    # A cash account cannot go overdrawn: once equity hits zero the subscriptions are cancelled and
    # the box is switched off. Before this was handled, dead paths kept being billed the monthly
    # fixed costs and walked to −$130, dragging the whole low band below the axis.
    out = build_projection(_losing_book(), _s())
    assert out["available"] is True
    assert min(out["end_equity"].values()) >= 0.0
    assert min(min(band) for band in out["bands"].values()) >= 0.0


def test_projection_of_a_losing_book_pays_nothing_and_refuses_an_income_answer() -> None:
    out = build_projection(_losing_book(), _s())
    assert out["first_withdrawal"]["probability"] == 0.0
    assert out["first_withdrawal"]["median_date"] is None
    assert out["take_home_gbp"]["p50"] == 0.0
    assert out["growth"]["p50"] <= 0.0
    # No positive growth rate means no amount of waiting gets there. A number here would be worse
    # than a blank — it would be a date.
    assert out["day_rate_years"]["p50"] is None
    assert all(rung["capital_usd"] is None for rung in out["ladder"])


def test_projection_of_a_winning_book_dates_the_first_payout_and_the_first_cgt_bill() -> None:
    # A year-long horizon so the quarterly withdrawal cadence and the 6-Apr tax boundary both fall
    # inside it — the two dates the "when does this start paying me" question actually asks for.
    s = _s(portfolio_projection_days=252, portfolio_max_trades_per_day=2)
    out = build_projection(_winning_book(s), s)
    assert out["first_withdrawal"]["probability"] > 0.0
    first_wd = date.fromisoformat(str(out["first_withdrawal"]["median_date"]))
    assert first_wd > date.fromisoformat(str(out["start_date"]))
    assert out["take_home_gbp"]["p50"] > 0.0
    # CGT settles at the UK tax-year boundary, so any bill inside the horizon is dated 6 April.
    if out["first_tax"]["median_date"] is not None:
        assert date.fromisoformat(str(out["first_tax"]["median_date"])).month == 4


def test_projection_reports_the_sample_it_was_built_from() -> None:
    # 30-odd days of history is the dominant uncertainty in every number above, and a smooth fan
    # chart is very good at hiding that. The page leads with these.
    res = _book(0.45, n_days=25)
    out = build_projection(res, _s())
    assert out["sample"]["days"] == len(res.equity_curve)
    assert out["sample"]["trades"] == res.n_trades
    assert 0 < out["sample"]["trading_days"] <= out["sample"]["days"]


def test_a_withdrawal_step_is_not_counted_as_a_drawdown() -> None:
    # Matches `_finalize`: paying yourself is not a drawdown, so the measure walks the pure
    # trading-P&L path rather than the balance. Proven on a path built to make the distinction
    # unmissable — EVERY day is a winner (block=1 over a one-day sample), so the trading path only
    # ever rises, while the balance visibly steps DOWN at each quarterly payout. Max DD must be
    # exactly zero; anything above it means the balance is being measured.
    s = _s(portfolio_projection_days=252, portfolio_projection_block_days=1)
    sessions = future_sessions(date(2026, 8, 3), 252, s)
    always_up = [DaySample(date(2026, 8, 3), (0.02,), 0.0)]
    idx = frozenset(range(len(sessions)))
    out = _simulate_path(always_up, sessions, 10_000.0, s, random.Random(0), idx)

    assert out.max_drawdown_pct == 0.0
    assert out.withdrawals_gbp > 0.0, "fixture must actually pay out for this to prove anything"
    # …and the balance really did fall on payout days, which is what a naive measure would catch.
    assert any(b < a for a, b in zip(out.equity_at, out.equity_at[1:], strict=True))


# --- --- The income arithmetic ------------------------------------------------------------------


def test_day_rate_net_annual_is_net_of_the_ir35_haircut() -> None:
    s = _s()
    assert day_rate_net_annual_gbp(s) == pytest.approx(800.0 * 220 * 0.52)
    # It is deliberately NET: the other side of the comparison is a post-CGT withdrawal, and
    # matching a gross assignment rate against it would flatter the day job by the whole PAYE bill.
    assert day_rate_net_annual_gbp(s) < 800.0 * 220


def test_annual_fixed_costs_carry_both_the_box_and_the_data_feed() -> None:
    s = _s()
    expected = 12 * (5.70 + 10.0 / 1.27)
    assert annual_fixed_costs_gbp(s) == pytest.approx(expected, rel=1e-4)


@pytest.mark.parametrize("target", [500.0, 3_000.0, 40_000.0, 91_520.0])
@pytest.mark.parametrize("growth", [0.15, 0.35, 2.0])
def test_capital_for_income_is_the_exact_inverse_of_income_from_capital(
    target: float, growth: float
) -> None:
    # The inversion is the risk: `capital_for_income` solves T = P − fixed − (P − exempt)·rate for
    # P, and a quietly wrong inversion still returns a confident number of years. Round-trip it
    # through the FORWARD function the ramp chart uses — which also pins the two to each other, so
    # a change to the tax rules on one side can't silently diverge from the other.
    s = _s()
    capital = capital_for_income(target, growth, s)
    assert capital is not None
    # `abs` because `capital_for_income` rounds its answer to the cent, and growth multiplies that
    # half-cent back up — a few pence of round-trip error, not a wrong inversion.
    assert income_from_capital(capital, growth, s) == pytest.approx(target, rel=1e-6, abs=0.05)


def test_income_from_capital_never_goes_negative() -> None:
    # A tiny account earns less than the box costs to run. That is £0 of income, not a negative
    # salary — the ramp chart's y-axis starts at zero and a negative would puncture it.
    s = _s()
    assert income_from_capital(100.0, 0.05, s) == 0.0
    assert income_from_capital(0.0, 0.5, s) == 0.0


def test_capital_for_income_skips_the_tax_term_below_the_annual_allowance() -> None:
    # Small target: gross profit lands under the CGT exempt amount, so the bill is zero and the
    # capital needed is just "profit = target + running costs".
    s = _s()
    target = 500.0
    growth = 0.5
    capital = capital_for_income(target, growth, s)
    assert capital is not None
    profit_gbp = capital * growth / s.portfolio_gbpusd_rate
    assert profit_gbp < s.portfolio_cgt_annual_exempt_gbp
    assert profit_gbp == pytest.approx(target + annual_fixed_costs_gbp(s), rel=1e-6)


def test_capital_for_income_needs_a_positive_growth_rate() -> None:
    s = _s()
    assert capital_for_income(10_000.0, 0.0, s) is None
    assert capital_for_income(10_000.0, -0.2, s) is None


def test_capital_for_income_is_monotone_in_both_arguments() -> None:
    s = _s()
    a = capital_for_income(10_000.0, 0.4, s)
    b = capital_for_income(20_000.0, 0.4, s)
    c = capital_for_income(10_000.0, 0.8, s)
    assert a is not None and b is not None and c is not None
    assert b > a  # more income needs more capital
    assert c < a  # a better edge needs less of it


def test_years_to_capital_compounds() -> None:
    assert years_to_capital(200.0, 100.0, 1.0) == pytest.approx(1.0)  # doubling at +100%/yr
    assert years_to_capital(400.0, 100.0, 1.0) == pytest.approx(2.0)
    assert years_to_capital(100.0, 500.0, 0.5) == 0.0  # already past it
    assert years_to_capital(1000.0, 100.0, 0.0) is None
    assert years_to_capital(1000.0, 0.0, 0.5) is None


def test_annualised_growth_handles_a_wiped_out_path() -> None:
    assert _annualised(200.0, 100.0, 1.0) == pytest.approx(1.0)
    assert _annualised(100.0, 100.0, 2.0) == pytest.approx(0.0)
    assert _annualised(0.0, 100.0, 1.0) == pytest.approx(-1.0)  # −100%, not a crash
    assert _annualised(100.0, 0.0, 1.0) == 0.0


def test_income_ladder_shows_the_position_size_each_rung_implies() -> None:
    # The whole ladder assumes percentage returns are scale-free, which for small-cap momentum they
    # are not. The position column is where that assumption becomes visible instead of buried.
    s = _s(portfolio_projection_days=252, portfolio_max_trades_per_day=2)
    out = build_projection(_winning_book(s), s)
    rungs = [r for r in out["ladder"] if r["capital_usd"] is not None]
    assert rungs, "a winning book should produce a priced ladder"
    for rung in rungs:
        assert rung["position_usd"] == pytest.approx(
            round(rung["capital_usd"] * s.portfolio_position_fraction, 2)
        )
    # Ascending by income, and the day rate is the last rung.
    assert out["ladder"][-1]["label"] == "Day rate"
    assert out["ladder"][-1]["gbp_per_year"] == pytest.approx(day_rate_net_annual_gbp(s))
    monthly = [r["gbp_per_month"] for r in out["ladder"]]
    assert monthly == sorted(monthly)


def test_income_ramp_rises_and_sizes_its_horizon_to_contain_the_crossing() -> None:
    # The ramp is the direct answer to "when can I rely on this instead of the day rate", so the
    # crossing must be ON the chart — a horizon that stopped one year short would render a picture
    # whose whole point is off the right edge.
    s = _s()
    target = day_rate_net_annual_gbp(s)
    ramp = income_ramp(1_000.0, {"p25": 0.4, "p50": 0.8, "p75": 1.5}, target, s)
    assert ramp["target_gbp"] == pytest.approx(target)
    assert ramp["years"] == list(range(len(ramp["years"])))
    for series in ramp["series"].values():
        assert series == sorted(series)  # reinvesting more capital never pays less
        assert len(series) == len(ramp["years"])
    assert max(ramp["series"]["p50"]) >= target
    # Higher growth pays more at every year, so the three lines never cross.
    for y in range(len(ramp["years"])):
        assert ramp["series"]["p25"][y] <= ramp["series"]["p50"][y] <= ramp["series"]["p75"][y]


def test_income_ramp_is_bounded_when_the_crossing_is_far_away_or_impossible() -> None:
    s = _s()
    target = day_rate_net_annual_gbp(s)
    # A barely-positive edge would otherwise plot to year 40 — arithmetic, not a plan.
    slow = income_ramp(500.0, {"p50": 0.01}, target, s)
    assert len(slow["years"]) == 16  # years 0..15, the cap
    # No edge at all: still a valid (flat, zero) ramp rather than a crash or an empty chart.
    dead = income_ramp(500.0, {"p50": -0.5}, target, s)
    assert len(dead["years"]) == 6  # years 0..5, the floor
    assert set(dead["series"]["p50"]) == {0.0}


def test_an_absurd_growth_rate_is_flagged_rather_than_quoted() -> None:
    # Fixed-fractional compounding turns a short lucky run into hundreds of times the account per
    # year, and `capital_for_income` then DIVIDES by that rate — so the page would state, in
    # crisp dollars, that replacing a £91k salary needs $551 of capital. The arithmetic is right
    # and the answer is garbage; the flag is what lets the page say so instead of printing it.
    s = _s(portfolio_projection_days=252, portfolio_max_trades_per_day=2)
    hot = build_projection(_winning_book(s), s)
    assert hot["growth"]["p50"] > 9.0
    assert hot["growth_implausible"] is True
    # The ladder is still emitted (its shape is meaningful) — it is the flag, not a blank, that
    # carries the warning, so the page can dim it rather than losing the information.
    assert hot["ladder"][-1]["capital_usd"] is not None

    calm = build_projection(
        _book(0.45, seed=5, s=_s(portfolio_risk_fraction=0.01, portfolio_max_trades_per_day=2)), s
    )
    assert 0 < calm["growth"]["p50"] < 9.0
    assert calm["growth_implausible"] is False


def test_day_rate_years_ranks_slower_growth_as_a_longer_wait() -> None:
    s = _s(portfolio_projection_days=252, portfolio_max_trades_per_day=2)
    out = build_projection(_winning_book(s), s)
    years = out["day_rate_years"]
    if years["p25"] is not None and years["p75"] is not None:
        assert years["p25"] >= years["p75"]  # the pessimistic growth quartile waits longer
