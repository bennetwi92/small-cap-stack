"""The boundary ledgers (#232/#234/#249): market data, VPS, CGT, withdrawals.

Split out of `test_portfolio.py` in #529. Everything that moves cash across the book's edge, plus
the settled-cash invariant the 50% x 2/day cap discharges by construction. Gap-month billing (#249)
is here rather than 400 lines later, where the original file left it: a month with no collected
data still bills, and that is a ledger rule.
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from small_cap_stack.config import Settings
from small_cap_stack.portfolio import (
    _DataFeeLedger,
    _TaxLedger,
    _VpsLedger,
    _WithdrawalLedger,
    simulate_portfolio,
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
