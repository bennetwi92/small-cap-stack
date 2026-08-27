"""`research/strategy.md` must never disagree with `config.py`.

The spec is the one place allowed to restate a strategy number in prose, and it earns that only by
being generated. These tests are what make the claim true: the committed file has to match what
`Settings` renders today, and the renderer has to actually read `Settings` rather than carry its
own copy of the numbers (which is the failure mode the whole exercise exists to end — #551).
"""

from __future__ import annotations

from datetime import time
from pathlib import Path

import pytest

from small_cap_stack.config import Settings
from small_cap_stack.strategy_doc import (
    BEGIN_MARKER,
    END_MARKER,
    STRATEGY_DOC,
    StrategyDocError,
    doc_is_current,
    main,
    render_block,
    splice,
    write_doc,
)
from tests.support import settings

_REPO_ROOT = Path(__file__).resolve().parents[1]
_DOC = _REPO_ROOT / STRATEGY_DOC


def _settings(**overrides: object) -> Settings:
    return settings(**overrides)


def _skeleton() -> str:
    return f"# spec\n\nprose above\n\n{BEGIN_MARKER}\nstale\n{END_MARKER}\n\nprose below\n"


def test_committed_doc_is_current() -> None:
    """The one that fails in CI when someone changes a knob and forgets `make strategy`."""
    assert doc_is_current(_DOC, _settings()), (
        f"{STRATEGY_DOC} is stale against config.py — run `make strategy`"
    )


def test_doc_exists_and_keeps_its_markers() -> None:
    text = _DOC.read_text(encoding="utf-8")
    assert text.count(BEGIN_MARKER) == 1
    assert text.count(END_MARKER) == 1
    assert text.index(BEGIN_MARKER) < text.index(END_MARKER)


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("scan_min_price", 7.5, "$7.50"),
        ("scan_max_price", 33.0, "$33.00"),
        ("scan_change_pct", 12.5, "> 12.5%"),
        ("scan_min_5m_volume", 250_000, "> 250,000"),
        ("bull_flag_max_pole", 6, "≤ 6 higher highs"),
        ("bull_flag_max_cons", 3, "≤ 3 candles"),
        ("bull_flag_min_pole_pct", 0.035, "≥ 3.5%"),
        ("bull_flag_max_retracement", 0.4, "≤ 40% of the pole"),
        ("bull_flag_exhaustion_cap", 3, "4th+ contiguous cycle"),
        ("entry_staleness_min", 15, "≤ 15 min"),
        ("select_price_min", 7.5, "$7.50 ≤ `entry_fill`"),
        ("select_price_max", 25.0, "≤ $25.00"),
        ("portfolio_max_trades_per_day", 4, "4, taken first-by-trigger-time"),
        ("portfolio_start_equity_usd", 1500.0, "$1,500.00"),
        ("float_max_shares", 5_000_000, "5,000,000"),
    ],
)
def test_every_rendered_value_tracks_its_setting(field: str, value: object, expected: str) -> None:
    """A rendered number must come from `Settings`, not from a literal in the renderer.

    Each case changes one knob away from its default and asserts the new value reaches the block —
    so a table row that was quietly hardcoded fails here instead of going stale in the doc.
    """
    block = render_block(_settings(**{field: value}))
    assert expected in block, f"{field} does not reach the generated block"
    assert expected not in render_block(_settings()), (
        f"the {field} case does not distinguish anything — it matches the default too"
    )


def test_retired_price_bands_cannot_reappear() -> None:
    """#551: four price bands were live at once. Only the two real ones may be rendered.

    #608 widened the selection band to the scan's own $1–$50, briefly making the two coincide in
    value; #643 raised the selection floor to $3.00, so they differ again. Either way they are
    asserted by their distinct rendered forms (a range for the scan, a bounded `entry_fill`
    expression for selection), so a future edit that merged them into one row would still fail —
    that is the conflation #551 was about, and it does not depend on the values agreeing."""
    block = render_block(_settings())
    assert "$1.00 – $50.00" in block  # the scan — floor unchanged throughout
    assert "$3.00 ≤ `entry_fill` ≤ $20.00" in block  # the book (D-45, #694)
    assert "$3.00 ≤ `entry_fill` ≤ $50.00" not in block  # the 2026-08-08 → 2026-08-26 band (#643)
    assert "$1.00 ≤ `entry_fill` ≤ $50.00" not in block  # the 2026-08-07 collection band (#608)
    assert "$2.00 ≤ `entry_fill` ≤ $20.00" not in block  # the 2026-07-31 → 2026-08-07 band (#386)
    assert "$2.00 – $10.00" not in block  # the 2026-06-29 brief
    assert "$1.00 – $20.00" not in block  # broker-costs' modelled universe


def test_float_and_news_are_rendered_as_not_gated() -> None:
    """The claim eight surfaces got wrong. If float ever becomes a gate, this section moves."""
    block = render_block(_settings())
    not_gated = block.split("### 4. Collected, never gated")[1]
    assert "float_gate" in not_gated
    assert "news_gate" in not_gated
    # Three, not four: the quality score was removed entirely in #690 (§D-44), so it is no
    # longer something to describe as "collected but not gated".
    assert not_gated.count("**No.**") == 3


def test_the_scan_and_selection_windows_are_rendered_as_separate_rules() -> None:
    """Two different windows, and conflating them is what #551 was about.

    The scan bounds what gets captured; the appearance windows (D-45, #694) bound what is
    takeable. A reader who merges them must not conclude the tracker stops looking at 09:00.
    """
    block = render_block(_settings())
    assert "| Scan window | 04:00 ET – 11:59 ET |" in block
    assert "[04:00 ET, 05:00 ET) or [06:00 ET, 07:00 ET) or [08:00 ET, 09:00 ET)" in block

    # Pin the distinction against the defaults rather than the strings: move one, and only one row
    # moves. A single window rendered twice would fail here.
    moved = render_block(_settings(select_appearance_windows=((time(6, 0), time(7, 0)),)))
    assert "| Scan window | 04:00 ET – 11:59 ET |" in moved
    assert "[06:00 ET, 07:00 ET)" in moved


def test_the_entry_cutoff_and_shares_band_render_their_disabled_forms() -> None:
    block = render_block(_settings(select_entry_cutoff=None, select_max_shares_outstanding=None))
    assert "| **Selection** — entry cutoff | — disabled |" in block
    assert "| **Selection** — shares outstanding | — disabled |" in block

    live = render_block(_settings())
    assert "trigger bar opens ≤ 09:30 ET" in live
    assert "≤ 50,000,000 (a missing datum is kept, not dropped)" in live


def test_disabled_knobs_render_as_disabled() -> None:
    """`breakeven_r=0` and `risk_rungs=1` ship inert — the doc must say so, not print the number."""
    block = render_block(_settings())
    assert "| Breakeven arm | disabled |" in block
    assert "| Risk throttle | off (always active) |" in block

    live = render_block(_settings(portfolio_breakeven_r=1.5, portfolio_risk_rungs=3))
    assert "| Breakeven arm | 1.5R |" in live
    assert "3 rungs, 2 same-direction days a step" in live


def test_splice_replaces_only_the_marked_region() -> None:
    spliced = splice(_skeleton(), f"{BEGIN_MARKER}\nfresh\n{END_MARKER}")
    assert "prose above" in spliced
    assert "prose below" in spliced
    assert "fresh" in spliced
    assert "stale" not in spliced


def test_splice_rejects_missing_markers() -> None:
    with pytest.raises(StrategyDocError, match="missing its generated markers"):
        splice("# spec\n\nno markers here\n", render_block(_settings()))


def test_splice_rejects_reversed_markers() -> None:
    with pytest.raises(StrategyDocError, match="END marker precedes"):
        splice(f"{END_MARKER}\n{BEGIN_MARKER}\n", render_block(_settings()))


def test_write_doc_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "strategy.md"
    path.write_text(_skeleton(), encoding="utf-8")
    assert write_doc(path, _settings()) is True
    assert write_doc(path, _settings()) is False
    assert doc_is_current(path, _settings())


def test_write_doc_rejects_a_missing_file(tmp_path: Path) -> None:
    with pytest.raises(StrategyDocError, match="does not exist"):
        write_doc(tmp_path / "nope.md", _settings())


def test_check_mode_exit_codes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "strategy.md"
    path.write_text(_skeleton(), encoding="utf-8")

    assert main(["--path", str(path), "build", "--check"]) == 1
    assert "STALE" in capsys.readouterr().out

    assert main(["--path", str(path), "build"]) == 0
    assert main(["--path", str(path), "build", "--check"]) == 0
    assert "up to date" in capsys.readouterr().out
