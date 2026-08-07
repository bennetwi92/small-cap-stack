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


# ------------------------------------------------ CLAUDE.md stays navigable and number-free (#539)

CLAUDE_MD = REPO_ROOT / "CLAUDE.md"

#: `"Section Name" above` / `` `x` below `` — a phrase followed by a direction.
_DIRECTIONAL = re.compile(r'(?:"([^"]+)"|`([^`]+)`)\s+(above|below)\b', re.I)


def test_claude_md_cross_references_point_the_right_way() -> None:
    """CLAUDE.md is the first file a session reads, and a "see X above" when X is below teaches
    the reader to stop following its pointers — which costs more than the wrong hop (#539).

    Only references that quote a real section heading are judged; prose like "the remote-work
    limits above" names no section and can't be checked, so #539's other one was found by reading.
    That limit is why this test exists *and* why it isn't sufficient on its own.
    """
    lines = CLAUDE_MD.read_text().splitlines()
    sections = {
        m.group(1).strip().lower(): i + 1
        for i, ln in enumerate(lines)
        if (m := re.match(r"^#{2,3}\s+(.+?)\s*$", ln))
    }
    assert len(sections) > 10, "no section headings found — has the format changed?"

    wrong: list[str] = []
    for i, ln in enumerate(lines, 1):
        for match in _DIRECTIONAL.finditer(ln):
            phrase = (match.group(1) or match.group(2) or "").strip().lower()
            direction = match.group(3).lower()
            target = next((v for k, v in sections.items() if phrase and phrase in k), None)
            if target is None:
                continue
            actual = "above" if target < i else "below"
            if actual != direction:
                wrong.append(f"line {i}: says {direction!r} of {phrase!r}, which is {actual}")
    assert not wrong, "CLAUDE.md points the wrong way:\n  " + "\n  ".join(wrong)


def test_claude_md_does_not_restate_the_strategy_numbers() -> None:
    """#551's rule, enforced: `research/strategy.md` is generated from `config.py` and is the only
    place the numbers live. Seven surfaces once stated them and disagreed on four price bands.

    ⚠️ This is also why **#539's headline ask was declined**. It wanted CLAUDE.md to state the
    paper book's own price band and trigger window — filed when the book had separate
    `portfolio_entry_price_*` / `portfolio_premarket_cutoff` settings. #567 has since moved
    selection into the engine (there is now one band and one window, `select_*`), so the two-funnel
    confusion the issue described no longer exists — and writing the numbers back into CLAUDE.md
    would recreate the exact drift #551 removed.
    """
    text = CLAUDE_MD.read_text()
    # Price bands and clock times are the two shapes that drifted. Issue references (#551) and
    # cron/section numbering are not that.
    offenders = [
        f"line {i}: {ln.strip()[:90]}"
        for i, ln in enumerate(text.splitlines(), 1)
        if re.search(r"\$\d+\s*[-–]\s*\$?\d+", ln)  # a price band
        or re.search(r"\b0[45]:\d\d\s*[-–]\s*\d\d:\d\d\b", ln)  # a trading window
    ]
    assert not offenders, (
        "CLAUDE.md restates strategy numbers; link research/strategy.md instead (#551):\n  "
        + "\n  ".join(offenders)
    )


#: `[text](path)` — a markdown link. Anchors, external URLs and mail links are not files.
_MD_LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")


def _linking_docs() -> list[Path]:
    return [p for p in (REPO_ROOT / "CLAUDE.md", REPO_ROOT / "README.md") if p.is_file()] + (
        _research_docs()
    )


@pytest.mark.parametrize("doc", _linking_docs(), ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_every_relative_link_resolves(doc: Path) -> None:
    """A link that 404s is worse than no link: it says "the answer is over there" and isn't.

    #540 moved five blocks of CLAUDE.md narrative out to the files that already owned it, replacing
    each with a pointer — which converts a maintenance problem (duplicated prose drifting) into a
    link-rot one. This is the guard that makes that trade safe. It complements
    `test_every_research_doc_is_referenced_somewhere`: that one catches a doc nothing points *at*,
    this one catches a pointer aimed at nothing.
    """
    broken = []
    for target in _MD_LINK.findall(doc.read_text()):
        if target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        path = (doc.parent / target.split("#", 1)[0]).resolve()
        if not path.exists():
            broken.append(f"{target} -> {path.relative_to(REPO_ROOT)}")
    assert not broken, f"{doc.relative_to(REPO_ROOT)} links to files that do not exist:\n  " + (
        "\n  ".join(broken)
    )


def test_the_link_check_actually_looks_at_links() -> None:
    """A regex that matched nothing would make every case above pass on an empty list."""
    counts = {p: len(_MD_LINK.findall(p.read_text())) for p in _linking_docs()}
    assert sum(counts.values()) > 30, counts
    assert counts[REPO_ROOT / "CLAUDE.md"] >= 3, (
        "CLAUDE.md's pointers are what #540 relies on — it trades duplicated prose for links"
    )
