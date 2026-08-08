"""Risk-adjusted smoothness: Sharpe, Sortino, Ulcer index (#648).

All hand-built series so the expected answer is arithmetic, not a re-run of the implementation."""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import UTC, date, datetime

import pytest

from small_cap_stack.portfolio.metrics import daily_returns, sharpe, sortino, ulcer_index
from small_cap_stack.portfolio.models import PaperTrade, PortfolioResult


def _trade(d: date, net_pnl_usd: float, equity_before: float) -> PaperTrade:
    return PaperTrade(
        trading_date=d,
        symbol="TEST",
        seg_id="a",
        run=1,
        trigger_at=datetime(d.year, d.month, d.day, 9, 0, tzinfo=UTC),
        entry_price=10.0,
        stop=9.0,
        qty=1,
        risk_fraction=0.05,
        risk_usd=1.0,
        risk_pct=0.05,
        sized_by="risk",
        target_r=2.0,
        breakeven_r=1.0,
        realized_r=0.0,
        reason="target",
        exit_price=10.0,
        gross_pnl_usd=net_pnl_usd,
        commission_usd=0.0,
        fees_usd=0.0,
        net_pnl_usd=net_pnl_usd,
        equity_before=equity_before,
        equity_after=equity_before + net_pnl_usd,
    )


def _result(
    trades: Sequence[PaperTrade], equity_curve: Sequence[tuple[date, float]]
) -> PortfolioResult:
    return PortfolioResult(
        start_equity=500.0,
        end_equity=500.0,
        trades=tuple(trades),
        equity_curve=tuple(equity_curve),
        n_trades=len(trades),
        wins=0,
        losses=0,
        win_rate=None,
        total_r=0.0,
        avg_r=None,
        expectancy_usd=None,
        return_pct=0.0,
        max_drawdown_pct=0.0,
        commission_usd=0.0,
        fees_usd=0.0,
        data_fees_usd=0.0,
        total_costs_usd=0.0,
        withdrawals_usd=0.0,
        withdrawals_gbp=0.0,
        tax_paid_usd=0.0,
        tax_paid_gbp=0.0,
        vps_costs_usd=0.0,
        vps_costs_gbp=0.0,
        net_take_home_gbp=0.0,
        cash_flows=(),
        skipped=(),
        skipped_total_r=0.0,
    )


def test_daily_returns_ignores_month_rollover_charges() -> None:
    d1 = date(2026, 1, 30)
    d2 = date(2026, 2, 2)  # month rollover — no trades, but the admin cost still steps equity down
    trades = [_trade(d1, net_pnl_usd=10.0, equity_before=500.0)]
    # 510 -> 480 on d2 is a VPS/data-fee settlement, not a losing session.
    equity_curve = [(d1, 510.0), (d2, 480.0)]
    res = _result(trades, equity_curve)

    assert daily_returns(res) == [pytest.approx(10.0 / 500.0), 0.0]


def test_sharpe_is_mean_over_stdev_annualised() -> None:
    rs = [0.02, 0.04, 0.03]  # mean 0.03, sample stdev 0.01
    assert sharpe(rs, annualise=False) == pytest.approx(3.0)
    assert sharpe(rs) == pytest.approx(3.0 * math.sqrt(252))


def test_sortino_divides_downside_by_the_full_count() -> None:
    rs = [0.02, 0.03, -0.02, 0.01]  # mean 0.01, one losing day out of four
    # Full-count denominator (correct): downside_dev = sqrt(0.02**2 / 4) = 0.01 -> sortino = 1.0
    full_count = 1.0
    # Losing-day-only denominator (wrong): downside_dev = sqrt(0.02**2 / 1) = 0.02 -> sortino = 0.5
    losing_day_only = 0.5
    assert full_count != losing_day_only

    got = sortino(rs, annualise=False)
    assert got == pytest.approx(full_count)
    assert got != pytest.approx(losing_day_only)


def test_sortino_exceeds_sharpe_when_downside_is_rarer_than_total_volatility() -> None:
    rs = [0.02, 0.03, -0.02, 0.01]  # up-swings inflate total stdev; only one day is downside
    assert sortino(rs, annualise=False) > sharpe(rs, annualise=False)


def test_ulcer_index_is_zero_for_a_monotonically_rising_curve() -> None:
    rs = [0.01, 0.02, 0.01, 0.03]
    assert ulcer_index(rs) == pytest.approx(0.0)


def test_ulcer_index_penalises_a_long_shallow_drawdown_more_than_a_brief_deep_one() -> None:
    # Both series draw down 20% from the same peak; the brief one recovers after one period, the
    # long one holds the same depth for four periods before recovering.
    brief = [-0.2, 0.25, 0.0, 0.0, 0.0]  # depths (%): 20, 0, 0, 0, 0
    long = [-0.2, 0.0, 0.0, 0.0, 0.25]  # depths (%): 20, 20, 20, 20, 0

    assert ulcer_index(brief) == pytest.approx(math.sqrt(80))  # sqrt((20**2) / 5)
    assert ulcer_index(long) == pytest.approx(math.sqrt(320))  # sqrt((20**2 * 4) / 5)
    assert ulcer_index(long) > ulcer_index(brief)


def test_metrics_are_none_on_a_series_too_short_to_judge() -> None:
    assert sharpe([]) is None
    assert sortino([]) is None
    assert sharpe([0.01, 0.02]) is None
    assert sortino([0.01, 0.02]) is None

    flat = [0.0, 0.0, 0.0, 0.0]
    assert sharpe(flat) is None
    assert sortino(flat) is None
