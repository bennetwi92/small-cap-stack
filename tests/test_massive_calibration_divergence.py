"""The divergence classifier behind spike #428's out-of-sample validation.

The whole point of the validation is the size of the ``unexplained`` bucket, so the tests that
matter are the ones that stop a divergence being filed under a *named* mechanism it does not
actually fit. Each case below pins one branch of :func:`classify_divergence` against the evidence
that branch claims to rest on.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, date, datetime, timedelta

from tests.spike_import import load_spike

from small_cap_stack.config import Settings
from small_cap_stack.rmetrics import RMetrics

mc = load_spike("massive_calibration")

HIT = datetime(2026, 7, 30, 12, 0, tzinfo=UTC)  # 08:00 ET, mid pre-market
SETTINGS = Settings()


def row(**kw: object) -> object:
    base = mc.VendorRow(
        symbol="AAA",
        trading_date=date(2026, 7, 30),
        prev_close=2.0,
        actual_hit=HIT,
        ibkr_hit=None,
        vendor_hit=HIT,
        ibkr_bars=10,
        vendor_bars=10,
        volume_ratio=1.0,
        matched_bars=10,
        ohlc_max_diff=0.0,
        actual=RMetrics(setup_found=False),
        vendor=RMetrics(setup_found=False),
        ibkr_recon=RMetrics(setup_found=False),
        actual_entry_at=None,
        vendor_entry_at=None,
        vendor_first_bar_at=HIT - timedelta(hours=4),
    )
    return dataclasses.replace(base, **kw)


def test_exact_truth_gives_a_point_delta_and_a_decidable_verdict() -> None:
    r = row(vendor_hit=HIT + timedelta(minutes=2), hit_quantum_sec=0)
    assert r.delta_vendor_min == 2.0
    assert r.delta_vendor_bounds == (2.0, 2.0)
    assert r.within_5min is True
    assert mc.classify_divergence(r, SETTINGS)[0] == "matched"


def test_quantised_truth_widens_the_delta_into_an_interval() -> None:
    """A bar-floored appearance is known only to within its bar, so the delta is a band and the
    point estimate sits at its middle — never at the floor, which would read ~2.5 min late."""
    r = row(vendor_hit=HIT + timedelta(minutes=4), hit_quantum_sec=300)
    assert r.delta_vendor_bounds == (-1.0, 4.0)
    assert r.delta_vendor_min == 1.5
    assert r.within_5min is True


def test_within_5min_abstains_when_the_quantum_straddles_the_boundary() -> None:
    """Neither True nor False: the quantum alone decides the verdict, so claiming either would be
    inventing precision. These are counted separately rather than folded into a rate."""
    r = row(vendor_hit=HIT + timedelta(minutes=8), hit_quantum_sec=300)
    assert r.delta_vendor_bounds == (3.0, 8.0)
    assert r.within_5min is None


def test_early_at_a_higher_price_is_the_rank_cap() -> None:
    """#432's proof: the gates passed EARLIER at a HIGHER price than at the live appearance. The
    change gate is monotone in price, so no previous close can order the later/lower bar first —
    only a capacity limit explains it."""
    r = row(
        vendor_hit=HIT - timedelta(minutes=30),
        hit_quantum_sec=300,
        vendor_price_at_recon=2.15,
        vendor_price_at_live=2.14,
    )
    mech, detail = mc.classify_divergence(r, SETTINGS)
    assert mech == "rank-cap"
    assert "2.15" in detail and "2.14" in detail


def test_early_at_a_lower_price_that_a_higher_prev_close_would_fix_is_the_reference() -> None:
    r = row(
        vendor_hit=HIT - timedelta(minutes=30),
        hit_quantum_sec=300,
        vendor_price_at_recon=2.00,
        vendor_price_at_live=2.50,
        implied_feasible=True,
        implied_low=2.40,  # above Massive's 2.00 prev close -> a higher reference delays us
        implied_high=2.60,
    )
    assert mc.classify_divergence(r, SETTINGS)[0] == "change-reference"


def test_early_with_neither_signature_is_left_unexplained() -> None:
    """The bucket that bounds a backtest. It must not absorb a case that fits a named mechanism,
    and no named mechanism may absorb a case that fits none."""
    r = row(
        vendor_hit=HIT - timedelta(minutes=30),
        hit_quantum_sec=300,
        vendor_price_at_recon=2.00,
        vendor_price_at_live=2.50,
        implied_feasible=False,
    )
    assert mc.classify_divergence(r, SETTINGS)[0] == "unexplained-early"


def test_late_when_the_vendor_tape_starts_after_the_appearance_is_coverage() -> None:
    r = row(
        vendor_hit=HIT + timedelta(minutes=40),
        hit_quantum_sec=300,
        vendor_first_bar_at=HIT + timedelta(minutes=20),
    )
    assert mc.classify_divergence(r, SETTINGS)[0] == "coverage"


def test_late_that_a_lower_prev_close_would_fix_is_the_reference() -> None:
    """#433: IBKR's change reference sits BELOW the consolidated previous close, so our stricter
    gate clears late. The interval says how much lower the reference must be."""
    r = row(
        vendor_hit=HIT + timedelta(minutes=40),
        hit_quantum_sec=300,
        prev_close=2.00,
        implied_feasible=True,
        implied_low=1.80,
        implied_high=1.99,  # our 2.00 sits above it -> a lower reference fires on the live bar
    )
    mech, detail = mc.classify_divergence(r, SETTINGS)
    assert mech == "change-reference"
    assert "#433" in detail


def test_late_because_the_vendor_tape_had_not_printed_100k_is_the_volume_basis() -> None:
    r = row(
        vendor_hit=HIT + timedelta(minutes=40),
        hit_quantum_sec=300,
        implied_feasible=False,
        vendor_vol5m_at_live=SETTINGS.scan_min_5m_volume - 1,
        ibkr_vol5m_at_live=SETTINGS.scan_min_5m_volume + 1,
    )
    assert mc.classify_divergence(r, SETTINGS)[0] == "volume-basis"


def test_late_with_none_of_the_signatures_is_left_unexplained() -> None:
    r = row(
        vendor_hit=HIT + timedelta(minutes=40),
        hit_quantum_sec=300,
        implied_feasible=False,
        vendor_vol5m_at_live=SETTINGS.scan_min_5m_volume + 5_000,
        ibkr_vol5m_at_live=SETTINGS.scan_min_5m_volume + 5_000,
    )
    assert mc.classify_divergence(r, SETTINGS)[0] == "unexplained-late"


def test_a_vendor_that_never_fires_is_not_silently_a_match() -> None:
    r = row(vendor_hit=None, hit_quantum_sec=300)
    assert r.delta_vendor_min is None
    assert mc.classify_divergence(r, SETTINGS)[0] == "no-vendor-hit"


def test_live_window_cases_keeps_only_in_window_appearances() -> None:
    make = lambda hit: mc.Case(  # noqa: E731
        symbol="AAA", trading_date=date(2026, 7, 30), first_hit=hit, bars=[]
    )
    premarket = make(HIT)  # 08:00 ET
    regular = make(datetime(2026, 7, 30, 15, 0, tzinfo=UTC))  # 11:00 ET
    kept = mc.live_window_cases([premarket, regular], mc.PREMARKET)
    assert kept == [premarket]
