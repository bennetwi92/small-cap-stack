"""The two enrichment gates the EOD report counts (#15).

⚠️ **Neither of these gates anything.** They are pure functions over an opportunity's captured
facts, and their only caller is `report.py`, which turns them into the `float_ok` /
`with_recent_news` columns. Nothing in the selection path or the paper book reads them — see
`research/strategy.md` §4 and the warning in `fundamentals.py`. If float should ever become a real
filter, the gate goes in the engine's selection tier, not here.

This module used to be a general gate *engine* (#517): eight gates, a `GATES` tuple, an
`evaluate` runner and `passed_all` / `failed_names` helpers, plus five `GateInputs` fields to feed
them. Six of the eight gates, the runner and both helpers had no caller anywhere — the engine-v2
detector (`bullflag/gates.py`, a different module with its own `evaluate`) took over shape gating
in #180, and the scan-side checks live in the IBKR scanner's own query. What was left was ~100
lines of scaffolding that outlived its design, plus ~120 lines of tests policing an engine that
never ran.

Pure and re-runnable, so definitions can change and be recomputed over the cached raw data
(store-raw / compute-on-read). Missing inputs fail conservatively.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .config import Settings


@dataclass(frozen=True)
class GateInputs:
    """Facts a gate evaluates, derived from an opportunity's raw record at a moment in time.

    `float_shares` and `has_recent_news` are what the two live gates read. `ts_utc` is read by
    neither — it is kept as the provenance stamp the caller already has, and so a gate that later
    needs a time doesn't have to reintroduce it.

    `price` / `change_pct` / `volume_5m` / `tradable` / `bull_flag` went with the gates that
    consumed them (#517): no production caller ever populated them, so every gate reading one saw
    `None` and returned the conservative "missing" result.
    """

    ts_utc: datetime
    float_shares: float | None = None
    has_recent_news: bool | None = None


@dataclass(frozen=True)
class GateResult:
    name: str
    passed: bool
    detail: dict[str, object] = field(default_factory=dict)


def _missing(name: str) -> GateResult:
    return GateResult(name, passed=False, detail={"missing": True})


def float_gate(i: GateInputs, s: Settings) -> GateResult:
    """Counted, never enforced — `float_max_shares` gates nothing (see the module docstring)."""
    if i.float_shares is None:
        return _missing("float")
    return GateResult(
        "float",
        i.float_shares < s.float_max_shares,
        {"float_shares": i.float_shares, "max": s.float_max_shares},
    )


def news_gate(i: GateInputs, s: Settings) -> GateResult:  # noqa: ARG001 — see the docstring
    """Counted, never enforced. `s` is unused but kept so both gates share one shape."""
    if i.has_recent_news is None:
        return _missing("news")
    return GateResult("news", i.has_recent_news, {"has_recent_news": i.has_recent_news})
