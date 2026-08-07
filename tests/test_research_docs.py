"""`research/` is the documentation root, and a doc nothing links to rots unread (#542).

`review-workbench-spec.md` sat with **zero** inbound references for a month. Nothing pointed a
reader at it and nothing marked it stale, so a *proposal* — still headed `Status: proposed`, still
naming files that no longer exist and one module that was never built — sat in the documentation
root reading like a description of the live page. Two published reports made a similar mistake
about the float gate (#551), which is what put a `correction:` field on reports.

The rule that would have caught it is cheap and, measured before writing it, had **no** exceptions:
20 research docs, 19 referenced, 1 orphan — the orphan being exactly the rotten one. So this is a
guard rather than a guard-with-a-list-of-excuses, and it stays that way only if a new doc gets
linked when it lands.
"""

from __future__ import annotations

import re
from functools import cache
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
RESEARCH = REPO_ROOT / "research"

#: Where a doc may legitimately be referenced from — prose, code comments, workflows, the runbook,
#: the Makefile. `scripts/` and root-level files are in deliberately: `scripts/fetch_fixtures.sh`
#: cites `decisions.md` and the `Makefile` cites `strategy.md`, so leaving them out would report a
#: doc referenced only from there as an orphan.
_SEARCH_ROOTS = ("src", "tests", "spikes", "docs", "deploy", "scripts", ".github")


def _research_docs() -> list[Path]:
    return sorted(RESEARCH.rglob("*.md"))


@cache
def _corpus() -> dict[Path, str]:
    """Every file that could carry a reference, keyed by path. Cached — this is parametrised over
    every doc, and re-reading ~240 files per case turns 0.06s into 1.2s for no benefit."""
    paths = [p for p in REPO_ROOT.glob("*") if p.is_file()]  # README, CLAUDE, Makefile, …
    paths.extend(RESEARCH.rglob("*.md"))
    for root in _SEARCH_ROOTS:
        # A renamed or mistyped root makes `rglob` return nothing and the guard silently shrink —
        # `docs/` in particular is renameable now (#486). Fail loudly instead of failing open.
        assert (REPO_ROOT / root).is_dir(), f"_SEARCH_ROOTS names {root}/, which does not exist"
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
        # Tolerant of the forms a human actually writes: a blockquote or list prefix, `**Status**:`
        # as well as `**Status:**`, and any capitalisation. None of these occur today; the point is
        # that they can't sneak past later.
        if re.search(
            r"^[>\-\s]*\*\*Status\*{0,2}:\*{0,2}\s*proposed\b",
            doc.read_text(encoding="utf-8"),
            re.M | re.I,
        )
    ]
    assert not stale, (
        f"{stale} still say 'Status: proposed'. If the thing shipped, say so (and strike the old "
        "status rather than overwriting it — the record is the point); if it didn't, date it."
    )
