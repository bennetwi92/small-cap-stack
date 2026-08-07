"""The decision log's status index has to stay true, or it is worse than none (#541).

An index that says LIVE for a reversed decision actively misleads, where no index merely costs you
a read. So the table is generated from each section's `**Status:**` line and this module fails when
the committed table drifts — the contract `make strategy` and `make reports` already have.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from small_cap_stack.decisions import (
    BEGIN,
    DECISIONS_MD,
    END,
    STATES,
    Decision,
    build,
    main,
    parse,
    render,
    slug,
)

TEXT = DECISIONS_MD.read_text()


def test_the_committed_index_is_not_stale() -> None:
    """Run `make decisions`."""
    assert build(TEXT) == TEXT, "research/decisions.md's index is stale — run `make decisions`"


def test_every_section_is_indexed() -> None:
    """A section the index skips is exactly the invisible-status problem #541 was about."""
    decisions = parse(TEXT)
    assert len(decisions) == TEXT.count("\n## "), "some `##` section is missing from the index"
    assert len(decisions) > 30


def test_ids_are_sequential_and_unique() -> None:
    """IDs are stable references — a duplicate or a gap means one was reused or dropped."""
    ids = [d.id for d in parse(TEXT)]
    assert ids == [f"{n:02d}" for n in range(1, len(ids) + 1)]


def test_every_anchor_matches_a_real_heading() -> None:
    """The index links by slug. A row pointing at nothing is a dead link on every row like it."""
    headings = {slug(ln.removeprefix("## ")) for ln in TEXT.splitlines() if ln.startswith("## ")}
    anchors = [d.anchor for d in parse(TEXT)]
    assert set(anchors) <= headings
    assert len(set(anchors)) == len(anchors), "two decisions slug to the same anchor"


def test_no_status_note_can_break_the_table() -> None:
    """A `|` in a note would silently split a row into extra columns."""
    for d in parse(TEXT):
        assert "|" not in d.status, f"D-{d.id}'s status contains a pipe"
        assert "|" not in d.topic, f"D-{d.id}'s topic contains a pipe"


def test_cross_references_between_decisions_resolve() -> None:
    """`superseded by D-16` is only useful if D-16 exists — a typo'd ID reads as authoritative."""
    ids = {d.id for d in parse(TEXT)}
    dangling = [
        (d.id, ref)
        for d in parse(TEXT)
        for ref in re.findall(r"\bD-(\d{2})\b", d.status)
        if ref not in ids
    ]
    assert not dangling, f"status lines cite decisions that do not exist: {dangling}"


def test_the_three_supersession_styles_are_gone_from_headings() -> None:
    """#541's complaint: `### ⚠️ REVERSED`-style headings made status a body-reading exercise.

    Subsections may still narrate a reversal in prose — that is the record — but the *top-level*
    heading grammar carries no status, so the status line is the only place to look.
    """
    for line in TEXT.splitlines():
        if line.startswith("## "):
            assert not re.search(r"REVERSED|SUPERSEDED|AMENDED|DECISION|CONFIRMED", line), line


def test_a_reversed_decision_is_findable_from_the_index_alone() -> None:
    """The whole point. D-23's throttle ships off; nothing in its heading says so."""
    reversed_ = [d for d in parse(TEXT) if d.state == "REVERSED"]
    assert reversed_, "expected the risk throttle to still be recorded as reversed"
    assert all(d.note for d in reversed_), "a REVERSED entry must say what reversed it"


def test_states_are_the_documented_three() -> None:
    assert {d.state for d in parse(TEXT)} <= set(STATES)


# --- the parser's own guards -------------------------------------------------------------------


def test_a_heading_off_the_grammar_is_rejected() -> None:
    with pytest.raises(ValueError, match="does not match the one format"):
        parse("## Some old-style heading (DECISION 2026-07-02, #112)\n\n**Status:** LIVE\n")


def test_a_section_with_no_status_line_is_rejected() -> None:
    with pytest.raises(ValueError, match="no `\\*\\*Status:"):
        parse("## D-01 — Topic (2026-06-29)\n\nprose, but no status line\n")


def test_a_trailing_section_with_no_body_at_all_is_rejected() -> None:
    with pytest.raises(ValueError, match="no `\\*\\*Status:"):
        parse("## D-01 — Topic (2026-06-29)\n")


def test_an_unknown_state_is_rejected() -> None:
    with pytest.raises(ValueError, match="no `\\*\\*Status:"):
        parse("## D-01 — Topic (2026-06-29)\n\n**Status:** MAYBE\n")


def test_refs_are_optional() -> None:
    (d,) = parse("## D-01 — Topic (2026-06-29)\n\n**Status:** LIVE\n")
    assert (d.refs, d.note, d.status) == ("", "", "LIVE")
    assert "| — |" in render([d])


def test_a_topic_may_contain_its_own_parentheses() -> None:
    (d,) = parse("## D-02 — Principle (from Q11) (2026-06-29, #62)\n\n**Status:** LIVE\n")
    assert d.topic == "Principle (from Q11)"
    assert d.refs == "#62"


def test_render_round_trips_through_the_markers() -> None:
    d = Decision("01", "Topic", "2026-06-29", "#1", "LIVE", "note")
    out = render([d])
    assert out.startswith(BEGIN) and out.endswith(END)
    assert "| [D-01](#d-01--topic-2026-06-29-1) | 2026-06-29 | Topic | #1 | LIVE — note |" in out


def test_build_replaces_the_previous_index_rather_than_appending() -> None:
    doc = (
        f"intro\n\n{BEGIN}\nstale junk\n{END}\n\n## D-01 — Topic (2026-06-29)\n\n**Status:** LIVE\n"
    )
    once = build(doc)
    assert "stale junk" not in once
    assert build(once) == once, "generating twice must be a no-op"
    assert once.count(BEGIN) == 1


# --- the CLI ------------------------------------------------------------------------------------

STALE = f"intro\n\n{BEGIN}\nstale\n{END}\n\n## D-01 — Topic (2026-06-29)\n\n**Status:** LIVE\n"


def test_check_exits_nonzero_on_a_stale_index(tmp_path: Path) -> None:
    """What a CI gate would call. Exiting 0 on a stale index is the whole failure mode."""
    p = tmp_path / "decisions.md"
    p.write_text(STALE)
    assert main(["--path", str(p), "build", "--check"]) == 1
    assert p.read_text() == STALE, "--check must not write"


def test_build_writes_and_is_then_current(tmp_path: Path) -> None:
    p = tmp_path / "decisions.md"
    p.write_text(STALE)
    assert main(["--path", str(p), "build"]) == 0
    assert "stale" not in p.read_text()
    assert main(["--path", str(p), "build", "--check"]) == 0
    assert main(["--path", str(p), "build"]) == 0, "a second build is a no-op, not an error"


def test_the_committed_log_passes_its_own_check() -> None:
    assert main(["--path", str(DECISIONS_MD), "build", "--check"]) == 0


def test_slug_strips_the_punctuation_the_headings_actually_use() -> None:
    assert slug("D-10 — Band widens to $1–$50 (2026-07-02, #126)") == (
        "d-10--band-widens-to-150-2026-07-02-126"
    )
    assert (
        slug("D-09 — `setup_count` retired (2026-07-02)") == "d-09--setup_count-retired-2026-07-02"
    )
