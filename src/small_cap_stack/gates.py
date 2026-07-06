"""Gate engine (issue #15): pure, replayable strategy gates over an opportunity's facts.

Each gate is a pure function of ``GateInputs`` (facts derived from the raw record) + settings,
returning a ``GateResult`` with a pass/fail and transparent detail. Because they're pure and
re-runnable, gate definitions/thresholds can change and be recomputed over the cached raw data
(store-raw / compute-on-read). Missing inputs fail conservatively.

The bull-flag gate (#16) and float source (#17) plug in here; tradability comes from #25.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from .clock import ET, within_window
from .config import Settings


@dataclass(frozen=True)
class GateInputs:
    """Facts a gate evaluates, derived from an opportunity's raw record at a moment in time."""

    ts_utc: datetime
    price: float | None = None
    change_pct: float | None = None
    volume_5m: float | None = None
    float_shares: float | None = None
    has_recent_news: bool | None = None
    tradable: bool | None = None
    bull_flag: bool | None = None  # populated by #16


@dataclass(frozen=True)
class GateResult:
    name: str
    passed: bool
    detail: dict[str, object] = field(default_factory=dict)


def _missing(name: str) -> GateResult:
    return GateResult(name, passed=False, detail={"missing": True})


def price_gate(i: GateInputs, s: Settings) -> GateResult:
    if i.price is None:
        return _missing("price")
    ok = s.scan_min_price <= i.price <= s.scan_max_price
    return GateResult(
        "price", ok, {"price": i.price, "min": s.scan_min_price, "max": s.scan_max_price}
    )


def change_gate(i: GateInputs, s: Settings) -> GateResult:
    if i.change_pct is None:
        return _missing("change_pct")
    return GateResult(
        "change_pct",
        i.change_pct > s.scan_change_pct,
        {"change_pct": i.change_pct, "min": s.scan_change_pct},
    )


def volume_gate(i: GateInputs, s: Settings) -> GateResult:
    if i.volume_5m is None:
        return _missing("volume_5m")
    return GateResult(
        "volume_5m",
        i.volume_5m > s.scan_min_5m_volume,
        {"volume_5m": i.volume_5m, "min": s.scan_min_5m_volume},
    )


def float_gate(i: GateInputs, s: Settings) -> GateResult:
    if i.float_shares is None:
        return _missing("float")
    return GateResult(
        "float",
        i.float_shares < s.float_max_shares,
        {"float_shares": i.float_shares, "max": s.float_max_shares},
    )


def news_gate(i: GateInputs, s: Settings) -> GateResult:
    if i.has_recent_news is None:
        return _missing("news")
    return GateResult("news", i.has_recent_news, {"has_recent_news": i.has_recent_news})


def tradable_gate(i: GateInputs, s: Settings) -> GateResult:
    if i.tradable is None:
        return _missing("tradable")
    return GateResult("tradable", i.tradable, {"tradable": i.tradable})


def trading_window_gate(i: GateInputs, s: Settings) -> GateResult:
    # `ts_utc` is stored UTC, but guard a naive value: bare `astimezone` would assume the host's
    # local tz and silently shift the window off ET (#163-C5).
    ts = i.ts_utc if i.ts_utc.tzinfo is not None else i.ts_utc.replace(tzinfo=UTC)
    ts_et = ts.astimezone(ET)
    ok = within_window(ts_et, s.scan_start, s.scan_end)
    return GateResult(
        "trading_window",
        ok,
        {"et": ts_et.strftime("%H:%M"), "window": f"{s.scan_start:%H:%M}-{s.scan_end:%H:%M}"},
    )


def bull_flag_gate(i: GateInputs, s: Settings) -> GateResult:
    if i.bull_flag is None:
        return _missing("bull_flag")
    return GateResult("bull_flag", i.bull_flag, {"bull_flag": i.bull_flag})


Gate = Callable[[GateInputs, Settings], GateResult]

GATES: tuple[Gate, ...] = (
    price_gate,
    change_gate,
    volume_gate,
    float_gate,
    news_gate,
    tradable_gate,
    trading_window_gate,
    bull_flag_gate,
)


def evaluate(inputs: GateInputs, settings: Settings) -> list[GateResult]:
    """Run every gate; returns one result per gate (order = GATES)."""
    return [gate(inputs, settings) for gate in GATES]


def passed_all(results: list[GateResult]) -> bool:
    return all(r.passed for r in results)


def failed_names(results: list[GateResult]) -> list[str]:
    return [r.name for r in results if not r.passed]
