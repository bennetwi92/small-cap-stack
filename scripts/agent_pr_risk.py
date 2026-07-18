#!/usr/bin/env python3
"""Decide whether an agent-authored PR is low-risk enough to auto-merge.

Called by ``.github/workflows/claude.yml`` after it opens a PR for an
``@claude build``/``@claude fix``. The policy is deliberately **fail-closed**:
anything sensitive or uncertain stays open for a one-tap human merge. Only a
genuinely trivial change auto-merges once CI is green. See RUNBOOK.md §13.

Kept as a standalone, unit-tested script (``tests/test_agent_pr_risk.py``) so the
policy is an explicit, readable check — not a vibe buried in shell.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable

# Changed-file globs that ALWAYS block auto-merge (defence in depth for the task
# guardrails: no engine/strategy code, no workflow files, no infra/deploy scripts).
# - .github/workflows/: a bad merge here could weaken the automation/security posture.
# - deploy/ + scripts/: how the box is deployed and operated.
# - src/small_cap_stack/bullflag/: the bull-flag detection engine — the product.
SENSITIVE_PREFIXES: tuple[str, ...] = (
    ".github/workflows/",
    "deploy/",
    "scripts/",
    "src/small_cap_stack/bullflag/",
)

# Individual files that gate the strategy or the build/infra even though their
# parent directory holds mergeable code too.
SENSITIVE_FILES: frozenset[str] = frozenset(
    {
        "Dockerfile",
        "Makefile",
        "docker-compose.yml",
        "pyproject.toml",
        "src/small_cap_stack/config.py",  # single source of truth for the rules (#302)
        "src/small_cap_stack/rmetrics.py",  # R-metrics
        "src/small_cap_stack/charts.py",  # engine consumer
        "src/small_cap_stack/day.py",  # detect_day live detector
    }
)

# Belt-and-suspenders: any path whose name implies engine/strategy code.
SENSITIVE_SUBSTRINGS: tuple[str, ...] = ("engine", "strategy")

MAX_CHANGED_LINES = 50


def _is_sensitive(path: str) -> bool:
    """True if a changed path must block auto-merge."""
    p = path.strip()
    if not p:
        return False
    if p in SENSITIVE_FILES:
        return True
    if any(p.startswith(prefix) for prefix in SENSITIVE_PREFIXES):
        return True
    lower = p.lower()
    return any(token in lower for token in SENSITIVE_SUBSTRINGS)


def evaluate(
    changed_files: Iterable[str],
    changed_lines: int,
    issue_labels: Iterable[str],
) -> tuple[bool, str]:
    """Return ``(auto_merge, reason)`` for an agent PR.

    Auto-merge only when EVERY condition holds:
      * no changed file is sensitive (engine/strategy, workflows, infra/deploy),
      * total additions + deletions <= ``MAX_CHANGED_LINES``,
      * the source issue is labelled ``trivial`` OR carries no ``strategy`` label.
    Every other case — including missing data — holds the PR for a human.
    """
    files = [f.strip() for f in changed_files if f.strip()]
    if not files:
        return False, "no changed files reported — cannot assess risk, holding for review"

    sensitive = sorted({f for f in files if _is_sensitive(f)})
    if sensitive:
        return False, f"touches sensitive path(s): {', '.join(sensitive)}"

    if changed_lines > MAX_CHANGED_LINES:
        return False, f"{changed_lines} changed lines exceeds the {MAX_CHANGED_LINES}-line cap"

    labels = {label.strip().lower() for label in issue_labels if label.strip()}
    if "strategy" in labels and "trivial" not in labels:
        return False, "source issue is labelled `strategy` (and not `trivial`)"

    return True, f"low-risk: {len(files)} file(s), {changed_lines} changed line(s)"


def _split(raw: str, *seps: str) -> list[str]:
    """Split ``raw`` on any of ``seps`` (commas and newlines) into clean tokens."""
    out = raw
    for sep in seps:
        out = out.replace(sep, "\n")
    return [tok for tok in out.split("\n") if tok.strip()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--changed-files",
        default="",
        help="Changed file paths, newline- or comma-separated.",
    )
    parser.add_argument(
        "--changed-lines",
        type=int,
        default=0,
        help="Total additions + deletions in the PR.",
    )
    parser.add_argument(
        "--issue-labels",
        default="",
        help="Source-issue label names, comma-separated.",
    )
    args = parser.parse_args(argv)

    auto_merge, reason = evaluate(
        _split(args.changed_files, ",", "\n"),
        args.changed_lines,
        _split(args.issue_labels, ",", "\n"),
    )
    json.dump({"automerge": auto_merge, "reason": reason}, sys.stdout)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
