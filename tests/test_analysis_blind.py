"""The analysis brief's blind list has to name files that actually exist (#731).

`analysis-brief.md` asks the design agent to work **blind**: it lists the surfaces carrying prior
strategy conclusions and tells the agent not to read them. That instruction is only as good as the
paths in it. A blind list naming `research/archive/` after the directory was deleted reads as
thorough while protecting nothing, and — worse — a *renamed* surface silently drops off the list
and becomes readable again.

This is the same failure mode `test_research_docs.py` guards in the other direction: there, a doc
nothing links to rots unread; here, a link to a doc that moved stops blinding anything. Both are
cheap to check and neither has a legitimate exception.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
BRIEF = REPO_ROOT / "research" / "analysis-brief.md"

#: The heading that opens the blind requirement, and the one that closes it.
_SECTION = "### Work blind — this is a hard requirement"

#: A backticked token is a path if it looks like one: a directory, or a file with a known suffix.
#: Prose entries in the table ("git log and issue history") carry no backticks and are skipped.
_PATHISH = re.compile(r"`([\w./-]+(?:/|\.md|\.py))`")


def _blind_section() -> str:
    text = BRIEF.read_text(encoding="utf-8")
    assert _SECTION in text, (
        f"{BRIEF.name} no longer carries {_SECTION!r} — the design pass is only unbiased while "
        "the brief tells the agent what not to read"
    )
    start = text.index(_SECTION)
    rest = text[start + len(_SECTION) :]
    end = rest.find("\n### ")
    return rest if end < 0 else rest[:end]


def _blind_paths() -> list[str]:
    """Paths from the blind *table* only.

    The section also carries an explicit carve-out paragraph naming the raw-capture modules the
    agent *may* read (`capture.py`, `storage.py`, `harvest/`) and the inventory it works from.
    Those are permissions, not prohibitions, and they are written bare rather than repo-relative —
    matching on the table rows keeps the two lists from bleeding into each other.
    """
    rows = [ln for ln in _blind_section().splitlines() if ln.lstrip().startswith("|")]
    return sorted(set(_PATHISH.findall("\n".join(rows))))


def test_blind_section_names_some_paths() -> None:
    """A blind list that parsed to nothing would pass every case below vacuously."""
    paths = _blind_paths()
    assert len(paths) >= 5, f"the blind list parsed to {paths} — the table shape has changed"


@pytest.mark.parametrize("rel", _blind_paths())
def test_blind_path_exists(rel: str) -> None:
    """Every path the brief blinds must exist, or it is blinding nothing."""
    target = REPO_ROOT / rel
    assert target.exists(), (
        f"`{rel}` is on the analysis brief's blind list but does not exist. Either it moved — "
        "update the list, or the agent can read it again — or it is gone and the row should go."
    )


def test_brief_carries_no_prior_findings() -> None:
    """The brief must not re-acquire the priors section that #731 removed.

    The original brief handed the analyst six pre-formed conclusions under "What is already known,
    so you do not re-derive it". Those are exactly the anchor the blind list exists to remove, and
    a well-meaning edit that "restores useful context" would undo the whole pass.
    """
    text = BRIEF.read_text(encoding="utf-8")
    assert "so you do not re-derive it" not in text, (
        "the analysis brief has regained its prior-findings section — the design pass is blind by "
        "construction, and priors belong in the execution pass, after pre-registration"
    )
