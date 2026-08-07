"""The two enrichment gates the EOD report counts (#15, pruned in #517).

This file used to test a gate *engine* — eight gates, an `evaluate` runner, `passed_all` /
`failed_names`. Six of those gates and all three helpers had no caller outside this file, so
~120 lines were policing an engine that never ran. What remains tests the two gates `report.py`
actually calls.

⚠️ Neither gate *gates* anything. They feed the EOD report's `float_ok` / `with_recent_news`
counts and nothing else — the point of the tests below is the fail-closed behaviour, because a
missing float that silently read as "passed" would inflate a published count.
"""

from __future__ import annotations

from datetime import UTC, datetime

from small_cap_stack.gates import GateInputs, float_gate, news_gate
from tests.support import settings

_TS = datetime(2026, 6, 29, 14, 0, tzinfo=UTC)  # 10:00 ET


def test_float_gate_passes_under_the_ceiling() -> None:
    r = float_gate(GateInputs(ts_utc=_TS, float_shares=8_000_000), settings())
    assert r.passed
    assert r.detail == {"float_shares": 8_000_000, "max": settings().float_max_shares}


def test_float_gate_is_strict_at_the_boundary() -> None:
    """`<`, not `<=` — a name exactly at the ceiling is not "low float"."""
    ceiling = settings().float_max_shares
    assert not float_gate(GateInputs(ts_utc=_TS, float_shares=ceiling), settings()).passed
    assert float_gate(GateInputs(ts_utc=_TS, float_shares=ceiling - 1), settings()).passed


def test_news_gate_reflects_the_flag() -> None:
    assert news_gate(GateInputs(ts_utc=_TS, has_recent_news=True), settings()).passed
    assert not news_gate(GateInputs(ts_utc=_TS, has_recent_news=False), settings()).passed


def test_both_gates_fail_closed_on_a_missing_input() -> None:
    """A gate with nothing to judge must not report a pass.

    These counts are published on the dashboard, so a missing float reading as "passed" would
    overstate `float_ok` — a fact about the data collection presented as a fact about the market.
    """
    s = settings()
    for gate in (float_gate, news_gate):
        r = gate(GateInputs(ts_utc=_TS), s)
        assert not r.passed, f"{gate.__name__} passed with no input"
        assert r.detail == {"missing": True}
