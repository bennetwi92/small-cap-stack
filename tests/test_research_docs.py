"""`research/` is the documentation root, and a doc nothing links to rots unread (#542).

`review-workbench-spec.md` sat with **zero** inbound references for a month. Nothing pointed a
reader at it and nothing marked it stale, so it went on describing the review page as read-only,
single-day and clipped — three things #140/#141 had already fixed. Two published reports made the
same mistake about the float gate (#551), which is what put a `correction:` field on reports.

The rule that would have caught it is cheap and, measured, has **no** exceptions: 20 research docs,
19 referenced, 1 orphan — the orphan being exactly the rotten one. So this is a guard rather than a
guard-with-a-list-of-excuses, and it stays that way only if a new doc gets linked when it lands.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
RESEARCH = REPO_ROOT / "research"

#: Where a doc may legitimately be referenced from — prose, code comments, workflows, the runbook.
_SEARCH_ROOTS = ("src", "tests", "spikes", "docs", "deploy", ".github")


def _research_docs() -> list[Path]:
    return sorted(RESEARCH.rglob("*.md"))


def _corpus() -> dict[Path, str]:
    """Every file that could carry a reference, keyed by path."""
    paths = [*REPO_ROOT.glob("*.md"), *RESEARCH.rglob("*.md")]
    for root in _SEARCH_ROOTS:
        paths.extend(p for p in (REPO_ROOT / root).rglob("*") if p.is_file())
    out: dict[Path, str] = {}
    for p in paths:
        # This file names docs as *examples* — `review-workbench-spec.md` in the module docstring
        # above. Counting that as an inbound reference makes the guard self-satisfying: a doc
        # would stay "linked" purely by being mentioned in the test that checks it is linked.
        # Caught by mutation: removing the doc's only real link left the check green.
        if p == Path(__file__).resolve():
            continue
        try:
            out[p] = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue  # binary assets carry no references
    return out


@pytest.mark.parametrize("doc", _research_docs(), ids=lambda p: str(p.relative_to(RESEARCH)))
def test_every_research_doc_is_referenced_somewhere(doc: Path) -> None:
    """A doc with no inbound reference is invisible: nobody reads it, so nobody notices it is
    wrong. Archiving one is fine — `research/archive/` is the record — but the archive entry
    still has to be linked, or the record is just a file nobody will ever open."""
    referrers = [
        str(path.relative_to(REPO_ROOT))
        for path, text in _corpus().items()
        if path != doc and doc.name in text
    ]
    assert referrers, (
        f"research/{doc.relative_to(RESEARCH)} is referenced nowhere in the repo. Link it from "
        "findings-index.md (the research record's index), or from whatever code or doc it "
        "explains — an unreferenced doc goes stale unread, which is what #542 found."
    )


def test_the_reference_check_would_notice_an_orphan() -> None:
    """A corpus that silently comes back empty makes every test above vacuous."""
    corpus = _corpus()
    assert len(corpus) > 100
    assert len(_research_docs()) > 10
    # A name that appears in no file must come back with no referrers. Assembled at runtime so
    # this file — which is itself in the corpus — can't match its own sentinel.
    absent = "no-such" + "-doc-anywhere.md"
    assert not [p for p, text in corpus.items() if absent in text]
    # And a real, definitely-linked doc must come back with several.
    linked = [p for p, text in corpus.items() if "strategy.md" in text]
    assert len(linked) > 3


def test_no_research_doc_still_calls_itself_proposed() -> None:
    """`**Status:** proposed` on a doc describing something that shipped is the specific way
    `review-workbench-spec.md` misled (#542) — it read as a live plan for a page that already
    existed. A superseded doc says so; it doesn't keep its original status line."""
    stale = [
        f"research/{doc.relative_to(RESEARCH)}"
        for doc in _research_docs()
        if re.search(r"^\*\*Status:\*\*\s*proposed\b", doc.read_text(encoding="utf-8"), re.M)
    ]
    assert not stale, (
        f"{stale} still say 'Status: proposed'. If the thing shipped, say so (and strike the old "
        "status rather than overwriting it — the record is the point); if it didn't, date it."
    )
