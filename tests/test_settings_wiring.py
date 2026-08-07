"""The settings-to-engine wiring is itself the thing under test (#302).

Both detectors are configured entirely from ``Settings``. Before #302 that was assumed rather than
checked: ``detect_day_with_settings`` silently omitted the caps, so they fell through to
``detect_day``'s defaults and ``config``'s values were fiction the live engine never read. Nothing
failed — the engine simply ignored the config, and the two detectors disagreed with each other.

These tests fail loudly if a knob is ever added to ``Settings`` (or to a detector) without being
wired through, which is the only way that class of bug gets caught.
"""

from __future__ import annotations

import ast
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from small_cap_stack.bullflag import day as day_mod
from small_cap_stack.bullflag import gates as gates_mod
from small_cap_stack.bullflag import setup as setup_mod
from small_cap_stack.capture import Bar
from small_cap_stack.config import Settings
from tests.support import settings

# A local anchor, deliberately not `support.T0`: nothing here depends on 08:00 vs 10:00 (both
# are inside 04:00–11:59), but the flat 200k volume below is load-bearing, so this module
# keeps its own builder and its own anchor rather than half-sharing.
_T0 = datetime(2026, 6, 29, 12, 0, tzinfo=UTC)  # 08:00 ET — inside the scan window


def _bar(i: int, o: float, h: float, low: float, c: float, vol: float = 200_000.0) -> Bar:
    """The i'th 5-min bar of the toy day. Volume is flat, so the pole's peak bar ties with the
    consolidation's — `vol_peak_gt_cons` is strict, so the caller drops the consolidation's."""
    return Bar(start=_T0 + timedelta(minutes=5 * i), open=o, high=h, low=low, close=c, volume=vol)


def _distinct_settings_at_tick(trigger_ticks: int) -> Settings:
    return settings(
        bull_flag_trigger_offset_ticks=trigger_ticks,
        bull_flag_min_pole_pct=0.0,  # the toy bars are a ~10% move, but keep the gate out of it
    )


# The params both detectors take that must come from Settings, mapped to their Settings field.
# `min_pole` is deliberately absent: detect_day has no such parameter (its pole comes from the
# cycle walk), so only the end-anchored detector reads it.
_SHARED = {
    "max_pole": "bull_flag_max_pole",
    "max_cons": "bull_flag_max_cons",
    "min_pole_pct": "bull_flag_min_pole_pct",
    "max_retracement": "bull_flag_max_retracement",
    "max_peak_wick": "bull_flag_max_peak_wick",
    "atr_window": "bull_flag_atr_window",
}


def _distinct_settings() -> Settings:
    """Settings whose every relevant value differs from the detectors' function defaults, so a
    param that is NOT wired keeps its default and the assertion catches it."""
    return settings(
        bull_flag_max_pole=7,
        bull_flag_max_cons=5,
        bull_flag_min_pole_pct=0.09,
        bull_flag_max_retracement=0.42,
        bull_flag_max_peak_wick=0.37,
        bull_flag_atr_window=11,
        bull_flag_trigger_offset_ticks=2,
        bull_flag_fill_offset_ticks=6,
        bull_flag_exhaustion_cap=5,
        entry_staleness_min=17,
        tick_size=0.05,
    )


def test_detect_day_with_settings_forwards_every_shared_rule() -> None:
    """The LIVE path (rmetrics / charts). A cap left unpassed here silently reverts to
    detect_day's default — exactly the #302 bug."""
    s = _distinct_settings()
    with patch.object(day_mod, "detect_day", return_value=None) as spy:
        day_mod.detect_day_with_settings([], s, None)
    kw: dict[str, Any] = spy.call_args.kwargs
    for param, field in _SHARED.items():
        assert kw[param] == getattr(s, field), f"detect_day({param}=) is not wired to {field}"


def test_detect_setup_with_settings_forwards_every_shared_rule() -> None:
    s = _distinct_settings()
    with patch.object(setup_mod, "detect_setup", return_value=None) as spy:
        setup_mod.detect_setup_with_settings([], s)
    kw: dict[str, Any] = spy.call_args.kwargs
    for param, field in _SHARED.items():
        assert kw[param] == getattr(s, field), f"detect_setup({param}=) is not wired to {field}"
    assert kw["min_pole"] == s.bull_flag_min_pole


def test_both_detectors_agree_on_every_shared_rule() -> None:
    """The two detectors ask different questions (whole-day vs end-anchored) but must apply the
    SAME rules. Before #302 they disagreed: the live path ran caps 4/4 + a 2% pole floor while the
    end-anchored one ran the stale 8/6 with the floor silently at 0.0."""
    s = _distinct_settings()
    with patch.object(day_mod, "detect_day", return_value=None) as day_spy:
        day_mod.detect_day_with_settings([], s, None)
    with patch.object(setup_mod, "detect_setup", return_value=None) as setup_spy:
        setup_mod.detect_setup_with_settings([], s)
    for param in _SHARED:
        assert day_spy.call_args.kwargs[param] == setup_spy.call_args.kwargs[param], (
            f"the detectors disagree on {param}"
        )


def test_entry_trigger_and_fill_are_derived_from_settings_ticks() -> None:
    """R is measured at the conservative fill, the trigger decides when it fires (#182/#190) — so
    the two offsets must stay distinct and both scale with tick_size."""
    s = _distinct_settings()
    with patch.object(day_mod, "detect_day", return_value=None) as spy:
        day_mod.detect_day_with_settings([], s, None)
    kw = spy.call_args.kwargs
    assert kw["trigger_offset"] == s.bull_flag_trigger_offset_ticks * s.tick_size
    assert kw["fill_offset"] == s.bull_flag_fill_offset_ticks * s.tick_size
    assert kw["fill_offset"] > kw["trigger_offset"]


def test_the_trigger_offset_moves_the_bar_that_fires_not_just_the_price() -> None:
    """#555: the offset used to price `entry_trigger` must also be the one that finds the bar.

    `detect_day` used a hardcoded tick to locate the breakout bar while pricing `entry_trigger`
    from the setting, so the two only agreed at the shipped 1-tick value. Raising the knob moved
    the published price and not the firing threshold — a bar whose high reached only
    breakout+1 tick would be recorded as triggering at breakout+2, with R measured off a price it
    never touched.

    Bars: a 2-bar pole into a 1-bar rest, then a break that clears the rest's high by exactly one
    tick. At a 1-tick offset that fires; at 2 ticks it must not.
    """
    bars = [
        _bar(0, 1.00, 1.02, 0.99, 1.02),  # pole
        _bar(1, 1.02, 1.10, 1.02, 1.09),  # peak (green, closes strong)
        _bar(2, 1.09, 1.09, 1.05, 1.06, vol=50_000.0),  # rest — lower high, breakout = 1.09
        _bar(3, 1.06, 1.10, 1.06, 1.10),  # high reaches 1.10: exactly +1 tick over the breakout
    ]
    fires = day_mod.detect_day_with_settings(bars, _distinct_settings_at_tick(1), None)
    assert fires is not None and fires.trigger_idx == 3
    assert fires.entry_trigger == pytest.approx(1.10)  # 1.09 + 1 tick

    quiet = day_mod.detect_day_with_settings(bars, _distinct_settings_at_tick(2), None)
    # A 2-tick offset needs 1.11; the bar only reached 1.10, so nothing may fire — and crucially
    # the engine must not report a trigger at a price no bar traded through.
    assert quiet is None or quiet.trigger_idx is None


def test_locked_v2_defaults() -> None:
    """Pins the values the engine-v2 review locked (#176/#182). These are the rules the live
    tracker runs and the 25 reviewed fixtures were signed off against — changing one is a strategy
    decision (research/decisions.md), not a tidy-up."""
    s = settings()
    assert s.bull_flag_max_pole == 4
    assert s.bull_flag_max_cons == 4
    assert s.bull_flag_min_pole_pct == 0.02
    assert s.bull_flag_min_pole == 1
    assert s.bull_flag_max_retracement == 0.50
    assert s.bull_flag_max_peak_wick == 0.50
    assert s.bull_flag_trigger_offset_ticks == 1
    assert s.bull_flag_fill_offset_ticks == 3
    assert s.bull_flag_exhaustion_cap == 2
    # A locked v2 rule (#130) that was pinned nowhere as a value until #554 — it survived only
    # implicitly, via test_rmetrics.py's 25-min-in / 40-min-out pair. A break too long after the
    # scanner appearance reads as faded, and 30 is the number that means.
    assert s.entry_staleness_min == 30


def test_legacy_entry_offset_is_gone() -> None:
    """The legacy 5-tick entry died with the anchored detector (#296/#302); v2 uses the
    trigger/fill split. A reappearance means the legacy path is creeping back."""
    assert not hasattr(settings(), "entry_offset_ticks")


# --- the specs must describe the engine that exists (#534) -----------------------------------

# The tests above catch a Settings field that isn't wired to the engine. Nothing caught the
# opposite: a field the *specs* describe that was never added. `research/engine-v2.md` listed
# `bull_flag_eps_ticks` and `bull_flag_score_weights` in its settings table for a year; the first
# is derived (`tokens.token_eps` returns `tick_size / 2`, not 1 tick), the second is a module
# constant (`score.DEFAULT_WEIGHTS`). Both sent readers looking for a config key that isn't there.
#
# Retired names stay legible on purpose — the specs are a historical record, and "this used to be
# called X" is worth reading. Each exemption says why it is not a live field.
_RETIRED_SETTINGS_NAMES = {
    "entry_offset_ticks": "legacy detector's entry; superseded by the trigger/fill split (#182)",
    "bull_flag_max_flag": "renamed to bull_flag_max_cons in #302, no alias kept",
    "bull_flag_eps_ticks": (
        "never a Settings field — the live value is derived in bullflag/tokens.py::token_eps, but "
        "the name IS still getattr-referenced by setup.py's end-anchored wrapper (#513)"
    ),
    "bull_flag_score_weights": "never existed — bullflag/score.py::DEFAULT_WEIGHTS (#534)",
}

_RESEARCH = Path(__file__).resolve().parents[1] / "research"

# Match the name ANYWHERE inside a backticked span, not just flush against the backtick. The
# tighter form missed ~30% of real mentions — `≤ bull_flag_max_pole`, `bull_flag_max_pole = 4`,
# a name wrapped onto its own line — so a phantom knob written as `≤ bull_flag_foo` sailed past.
_SETTINGS_NAME_RE = re.compile(r"`[^`\n]*?\b(bull_flag_\w+|entry_offset_ticks)\b")


def test_the_specs_only_name_settings_fields_that_exist() -> None:
    """Every `bull_flag_*` name the research docs put in backticks is a real field or a known
    retired one — so a newly invented knob fails here rather than in a reader's head."""
    fields = set(Settings.model_fields)
    unknown: list[str] = []
    for path in sorted(_RESEARCH.rglob("*.md")):
        text = path.read_text()
        for match in _SETTINGS_NAME_RE.finditer(text):
            name = match.group(1)
            if name in fields or name in _RETIRED_SETTINGS_NAMES:
                continue
            line = text.count("\n", 0, match.start()) + 1
            unknown.append(f"{path.name}:{line}: {name}")
    assert not unknown, (
        "research docs name Settings fields that do not exist:\n  "
        + "\n  ".join(unknown)
        + "\nEither add the field and wire it through detect_day_with_settings, or point the doc "
        "at where the value really lives (see _RETIRED_SETTINGS_NAMES for the pattern)."
    )


def test_the_retired_names_have_not_quietly_come_back() -> None:
    """The exemption list has to expire. If one of these becomes a real field, the exemption is
    exactly what would hide it from the check above."""
    live = sorted(set(Settings.model_fields) & set(_RETIRED_SETTINGS_NAMES))
    assert not live, f"{live} are listed as retired but now exist — drop them from the exemptions."


def _documented_gate_names() -> set[str]:
    """The gate names in engine-v2.md's §7 table (the rows are ``| `name` | … |``)."""
    text = (_RESEARCH / "engine-v2.md").read_text()
    table = re.search(r"^\| Gate \| Feature \|.*\n\|[-| ]+\|\n((?:\|.*\n)+)", text, re.MULTILINE)
    assert table is not None, "engine-v2.md §7's gate table is gone — restore it or drop this test"
    rows = table.group(1).splitlines()
    return {m.group(1) for row in rows if (m := re.match(r"\|\s*`(\w+)`", row))}


def _bull_flag_md_gate_names() -> set[str]:
    """The gate names in bull-flag.md §5's bullet — the list `gates.py`'s own module docstring
    points readers at, and which drifted the same way §7's table did (#534)."""
    text = (_RESEARCH / "bull-flag.md").read_text()
    bullet = re.search(r"^- \*\*Gates\*\*.*?(?=\n- \*\*Score)", text, re.MULTILINE | re.DOTALL)
    assert bullet is not None, "bull-flag.md §5's gate bullet is gone — restore it or drop this"
    # Only bare-identifier spans, optionally carrying a comparison: `` `pole_len ≤ cap` `` counts,
    # `` `engine-v2.md §7` `` and `` `gate_window=False` `` do not. Commentary is deliberately kept
    # outside this bullet so the list itself stays machine-readable.
    return set(re.findall(r"`(\w+)(?: [≤≥] [^`]+)?`", bullet.group(0)))


def _implemented_gate_names() -> set[str]:
    """Every ``GateResult("name", …)`` constructed in ``gates.evaluate``, read from the AST.

    Parsed rather than called: `evaluate` needs a whole FeatureVector to run, and the names are a
    property of the source, not of any particular shape. Text-scanning the file would also match
    the prose in its own docstrings — the failure mode these guards keep hitting.
    """
    tree = ast.parse(Path(gates_mod.__file__).read_text())
    func = next(
        n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "evaluate"
    )
    return {
        node.args[0].value
        for node in ast.walk(func)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "GateResult"
        and node.args
        and isinstance(node.args[0], ast.Constant)
    }


def test_the_engine_v2_gate_table_matches_the_gates() -> None:
    """§7's table drifted from `gates.evaluate` (#534): it listed a `shape_valid` gate that has
    never existed — a shape that doesn't segment produces no Segment, so there is nothing to gate —
    and omitted `peak_green`, which is what rejects a red-peaked pole. A gate table that disagrees
    with the gates is worse than no table: it reads as authoritative."""
    assert _documented_gate_names() == _implemented_gate_names(), (
        f"engine-v2.md §7 documents {sorted(_documented_gate_names())} but gates.evaluate "
        f"produces {sorted(_implemented_gate_names())}."
    )


def test_the_bull_flag_gate_list_matches_the_gates() -> None:
    """The same list appears twice, in two specs, and `gates.py`'s docstring cites the *other* one
    — so guarding only engine-v2.md would have left the cited copy free to drift (#534)."""
    assert _bull_flag_md_gate_names() == _implemented_gate_names(), (
        f"bull-flag.md §5 lists {sorted(_bull_flag_md_gate_names())} but gates.evaluate "
        f"produces {sorted(_implemented_gate_names())}."
    )


def test_the_gate_name_parser_would_notice_a_new_gate() -> None:
    """The AST reader is only a guard if it actually finds the gates — a parser that silently
    returns nothing makes the comparison above vacuous on both sides."""
    names = _implemented_gate_names()
    assert "peak_green" in names and "cons_holds_base" in names
    assert len(names) >= 8
