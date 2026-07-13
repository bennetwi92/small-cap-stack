"""Golden acceptance test (#211 stage 4): the core detect_day must reproduce every opportunity the
trader signed off on in the per-opportunity visual review (#194/#102/#196/#198).

Each ``tests/fixtures/review_cases/*.json`` holds one opportunity's full trading day of 5-min bars
plus the expected engine outcome (pole / consolidation / entry / stop, gate verdict, cycle number,
exhaustion). This runs ``detect_day_with_settings`` over each and asserts the outcome, so any future
change to the engine that would regress a signed-off case fails CI. The fixtures graduated here from
``spikes/review_regression.py`` when detect_day landed (it reproduced all 25 exactly).
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from small_cap_stack.bullflag import detect_day_with_settings
from small_cap_stack.capture import Bar
from small_cap_stack.clock import ET
from small_cap_stack.config import Settings

_FIXTURES = sorted((Path(__file__).parent / "fixtures" / "review_cases").glob("*.json"))
_SETTINGS = Settings()


def _outcome(bars: list[Bar], first_hit: datetime | None) -> dict[str, Any]:
    d = detect_day_with_settings(bars, _SETTINGS, first_hit)
    if d is None:
        return {"setup_found": False}
    seg = d.segment

    def t(i: int) -> str:
        return bars[i].start.astimezone(ET).strftime("%H:%M")

    return {
        "setup_found": True,
        "pole_base": t(seg.base_idx),
        "pole_peak": t(seg.peak_idx),
        "pole_len": seg.pole_len,
        "cons_start": t(seg.peak_idx + 1),
        "cons_end": t(seg.cons_end_idx),
        "cons_len": seg.cons_len,
        "entry_time": t(d.trigger_idx) if d.trigger_idx is not None else None,
        "entry_trigger": d.entry_trigger,
        "entry_fill": d.entry_fill,
        "stop": d.stop,
        "passed": d.passed,
        "failing_gates": sorted(g.name for g in d.gates if not g.passed),
        "cycle_num": d.cycle_num,
        "total_significant_cycles": d.total_significant_cycles,
        "exhausted": d.exhausted,
    }


def test_fixtures_present() -> None:
    assert len(_FIXTURES) == 25, "expected 25 committed review-case fixtures"


@pytest.mark.parametrize("path", _FIXTURES, ids=lambda p: p.stem)
def test_detect_day_reproduces_review_case(path: Path) -> None:
    fx = json.loads(path.read_text())
    bars = [
        Bar(
            start=datetime.fromisoformat(r[0]),
            open=r[1],
            high=r[2],
            low=r[3],
            close=r[4],
            volume=r[5],
        )
        for r in fx["bars"]
    ]
    first_hit = datetime.fromisoformat(fx["first_hit"]) if fx["first_hit"] else None
    actual = _outcome(bars, first_hit)
    expected = dict(fx["expected"])
    # failing_gates compared order-independently: detect_day orders peak_green after wick_peak, the
    # spike appended it last — the SET (and so the pass/reject verdict) is what matters.
    if "failing_gates" in expected:
        expected["failing_gates"] = sorted(expected["failing_gates"])
    for key, want in expected.items():
        assert actual.get(key) == want, (
            f"{path.stem}: {key} — expected {want!r}, got {actual.get(key)!r}"
        )
