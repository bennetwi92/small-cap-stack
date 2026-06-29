"""Tests for the gate engine (#15)."""

from __future__ import annotations

from datetime import UTC, datetime

from small_cap_stack.config import Settings
from small_cap_stack.gates import (
    GateInputs,
    evaluate,
    failed_names,
    passed_all,
)


def _settings(**o: object) -> Settings:
    return Settings(_env_file=None, **o)  # type: ignore[call-arg]


def _passing_inputs() -> GateInputs:
    # 14:00 UTC == 10:00 ET, inside the 04:00–11:59 window.
    return GateInputs(
        ts_utc=datetime(2026, 6, 29, 14, 0, tzinfo=UTC),
        price=5.0,
        change_pct=25.0,
        volume_5m=250_000,
        float_shares=8_000_000,
        has_recent_news=True,
        tradable=True,
        bull_flag=True,
    )


def test_all_gates_pass() -> None:
    results = evaluate(_passing_inputs(), _settings())
    assert passed_all(results)
    assert failed_names(results) == []
    assert {r.name for r in results} == {
        "price",
        "change_pct",
        "volume_5m",
        "float",
        "news",
        "tradable",
        "trading_window",
        "bull_flag",
    }


def test_price_band_boundaries() -> None:
    s = _settings()
    base = _passing_inputs()
    for price, ok in [(2.0, True), (10.0, True), (1.99, False), (10.01, False)]:
        results = {r.name: r for r in evaluate(GateInputs(**{**vars(base), "price": price}), s)}
        assert results["price"].passed is ok


def test_thresholds_are_strict() -> None:
    s = _settings()
    base = vars(_passing_inputs())
    # change% and volume use strict > ; float uses strict <
    assert not _gate(base, s, change_pct=10.0)["change_pct"].passed  # not > 10
    assert _gate(base, s, change_pct=10.01)["change_pct"].passed
    assert not _gate(base, s, volume_5m=100_000)["volume_5m"].passed  # not > 100k
    assert not _gate(base, s, float_shares=20_000_000)["float"].passed  # not < 20M
    assert _gate(base, s, float_shares=19_999_999)["float"].passed


def test_missing_inputs_fail_conservatively() -> None:
    s = _settings()
    base = vars(_passing_inputs())
    res = _gate(base, s, price=None, float_shares=None, has_recent_news=None)
    assert res["price"].detail == {"missing": True}
    assert not res["price"].passed
    assert not res["float"].passed
    assert not res["news"].passed


def test_outside_trading_window_fails() -> None:
    s = _settings()
    base = vars(_passing_inputs())
    # 20:00 UTC == 16:00 ET, outside 04:00–11:59
    res = _gate(base, s, ts_utc=datetime(2026, 6, 29, 20, 0, tzinfo=UTC))
    assert not res["trading_window"].passed


def test_blocked_symbol_fails_tradable() -> None:
    s = _settings()
    base = vars(_passing_inputs())
    res = _gate(base, s, tradable=False)
    assert not res["tradable"].passed
    assert "tradable" in failed_names(list(res.values()))


def _gate(base: dict[str, object], s: Settings, **overrides: object) -> dict[str, object]:
    inputs = GateInputs(**{**base, **overrides})  # type: ignore[arg-type]
    return {r.name: r for r in evaluate(inputs, s)}
