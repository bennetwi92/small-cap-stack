"""Published reports: markdown analyses authored into the repo, indexed for the dashboard.

A *report* is a markdown file in ``docs/reports/`` carrying a small front-matter header (title,
publish date, summary, tags). This module is the **index builder**: it parses every report's front
matter and writes ``docs/reports/index.json``, which the Reports page fetches to render the list
(newest first). The markdown itself is fetched and rendered client-side.

Why the repo and not the box: a report is prose authored alongside the code, so it ships through
the normal PR flow and GitHub Pages serves it straight out of ``docs/``. The `dashboard-data`
branch is force-pushed from the VPS each publish cycle, so anything written there by hand is
overwritten — reports must not live on it.

Usage::

    python -m small_cap_stack.reports new --title "Float gate revisited" --tags strategy,data
    python -m small_cap_stack.reports build          # regenerate docs/reports/index.json
    python -m small_cap_stack.reports build --check  # non-zero exit if the index is stale
"""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

#: Where reports live, relative to the repo root. `docs/` is the GitHub Pages source (see
#: CLAUDE.md — it is the frontend, not documentation), so anything under it is served as-is.
REPORTS_DIRNAME = "docs/reports"
INDEX_FILENAME = "index.json"

_FM_FENCE = "---"
_REQUIRED_KEYS = ("title", "published")
#: `correction` (#551): a report is a **dated** analysis and is never silently rewritten — but a
#: reader has no way to tell a current one from a superseded one, so all of them read as live. This
#: is one line of free text ("no longer true: …", "superseded by …") rendered as a warning banner on
#: the list row and above the body. Free text rather than a `superseded_by` slug because the two
#: cases it has to cover — a wrong premise and a later report overtaking this one — do not share a
#: shape, and one flexible field beats two rigid ones.
_OPTIONAL_KEYS = ("summary", "tags", "author", "correction")
_KNOWN_KEYS = frozenset(_REQUIRED_KEYS + _OPTIONAL_KEYS)
_DEFAULT_AUTHOR = "Claude"

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


class ReportError(ValueError):
    """A report file is malformed (bad front matter, missing/unknown key, bad date)."""


@dataclass(frozen=True)
class Report:
    """One published report, as the Reports page sees it."""

    slug: str  # filename stem — the `?r=` URL parameter
    file: str  # filename, fetched relative to docs/reports/
    title: str
    published: str  # ISO date (YYYY-MM-DD) or full ISO datetime, verbatim from front matter
    summary: str
    tags: list[str]
    author: str
    words: int
    correction: str  # "" when the report still stands as published


def slugify(text: str) -> str:
    """Lowercase ASCII slug: ``"Float gate — revisited"`` -> ``"float-gate-revisited"``."""
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return _SLUG_STRIP.sub("-", ascii_text.lower()).strip("-")


def count_words(body: str) -> int:
    """Rough reading length: whitespace tokens carrying at least one alphanumeric character.

    Markdown punctuation (``#``, ``|``, ``-``, fence lines) is not prose, so it isn't counted —
    the figure is only there to tell a two-minute read from a ten-minute one.
    """
    return sum(1 for token in body.split() if any(c.isalnum() for c in token))


def _parse_published(raw: str) -> datetime:
    """Sort key for a `published` value: a date sorts as midnight, naive as UTC."""
    try:
        parsed: datetime = datetime.fromisoformat(raw)
    except ValueError:
        try:
            parsed = datetime.combine(date.fromisoformat(raw), datetime.min.time())
        except ValueError as exc:
            raise ReportError(f"`published` is not an ISO date/datetime: {raw!r}") from exc
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def parse_front_matter(text: str) -> tuple[dict[str, str], str]:
    """Split ``---`` front matter from the markdown body.

    Deliberately a tiny YAML *subset* (``key: value`` scalars, one per line) rather than a YAML
    dependency: reports are written by us, and a strict small parser catches typos loudly.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != _FM_FENCE:
        raise ReportError("missing front matter: the file must start with a `---` line")

    meta: dict[str, str] = {}
    for i, line in enumerate(lines[1:], start=1):
        stripped = line.strip()
        if stripped == _FM_FENCE:
            return meta, "\n".join(lines[i + 1 :]).strip("\n")
        if not stripped:
            continue
        key, sep, value = stripped.partition(":")
        if not sep:
            raise ReportError(f"front-matter line is not `key: value`: {stripped!r}")
        key = key.strip().lower()
        if key not in _KNOWN_KEYS:
            raise ReportError(f"unknown front-matter key {key!r} (known: {sorted(_KNOWN_KEYS)})")
        if key in meta:
            raise ReportError(f"duplicate front-matter key {key!r}")
        meta[key] = value.strip()
    raise ReportError("front matter is not closed by a second `---` line")


def parse_report(path: Path) -> Report:
    """Read one markdown file into a :class:`Report` (raises :class:`ReportError` if malformed)."""
    try:
        meta, body = parse_front_matter(path.read_text(encoding="utf-8"))
    except ReportError as exc:
        raise ReportError(f"{path.name}: {exc}") from exc

    missing = [k for k in _REQUIRED_KEYS if not meta.get(k)]
    if missing:
        raise ReportError(f"{path.name}: missing required front-matter key(s): {missing}")

    published = meta["published"]
    try:
        _parse_published(published)
    except ReportError as exc:
        raise ReportError(f"{path.name}: {exc}") from exc

    tags = [t.strip() for t in meta.get("tags", "").split(",") if t.strip()]
    return Report(
        slug=path.stem,
        file=path.name,
        title=meta["title"],
        published=published,
        summary=meta.get("summary", ""),
        tags=tags,
        author=meta.get("author") or _DEFAULT_AUTHOR,
        words=count_words(body),
        correction=meta.get("correction", ""),
    )


def collect_reports(reports_dir: Path) -> list[Report]:
    """Every ``*.md`` under `reports_dir`, newest first (ties broken by slug for determinism)."""
    reports = [parse_report(p) for p in sorted(reports_dir.glob("*.md"))]
    return sorted(reports, key=lambda r: (_parse_published(r.published), r.slug), reverse=True)


def build_index(reports_dir: Path, *, now: datetime | None = None) -> dict[str, Any]:
    """The payload the Reports page fetches: `generated_utc` + the ordered report list."""
    stamp = now or datetime.now(UTC)
    return {
        "generated_utc": stamp.replace(microsecond=0).isoformat(),
        "reports": [asdict(r) for r in collect_reports(reports_dir)],
    }


def write_index(reports_dir: Path, *, now: datetime | None = None) -> Path:
    """Write ``index.json`` into `reports_dir` and return its path."""
    path = reports_dir / INDEX_FILENAME
    payload = build_index(reports_dir, now=now)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def index_is_current(reports_dir: Path) -> bool:
    """True when the committed index matches the markdown on disk (`generated_utc` ignored)."""
    path = reports_dir / INDEX_FILENAME
    if not path.exists():
        return False
    try:
        committed = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    return bool(committed.get("reports") == build_index(reports_dir)["reports"])


SCAFFOLD = """---
title: {title}
published: {published}
summary: {summary}
tags: {tags}
---

# {title}

_Replace this with the analysis._
"""


def scaffold_report(
    reports_dir: Path,
    *,
    title: str,
    published: date,
    summary: str = "",
    tags: str = "",
    slug: str | None = None,
) -> Path:
    """Create ``<date>-<slug>.md`` with front matter filled in; never overwrites a file."""
    stem = f"{published.isoformat()}-{slug or slugify(title)}"
    path = reports_dir / f"{stem}.md"
    if path.exists():
        raise ReportError(f"{path.name} already exists — pick a different slug")
    reports_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(
        SCAFFOLD.format(title=title, published=published.isoformat(), summary=summary, tags=tags),
        encoding="utf-8",
    )
    return path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m small_cap_stack.reports")
    parser.add_argument(
        "--dir",
        type=Path,
        default=None,
        help=f"reports directory (default: <repo>/{REPORTS_DIRNAME})",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    build = sub.add_parser("build", help="regenerate index.json from the markdown files")
    build.add_argument(
        "--check",
        action="store_true",
        help="don't write; exit non-zero if the committed index is stale",
    )

    new = sub.add_parser("new", help="scaffold a new report file")
    new.add_argument("--title", required=True)
    new.add_argument("--summary", default="")
    new.add_argument("--tags", default="", help="comma-separated")
    new.add_argument("--slug", default=None, help="defaults to a slug of the title")
    new.add_argument("--published", default=None, help="ISO date (default: today)")

    args = parser.parse_args(argv)
    reports_dir = args.dir or (_repo_root() / REPORTS_DIRNAME)

    if args.cmd == "new":
        published = date.fromisoformat(args.published) if args.published else datetime.now().date()
        path = scaffold_report(
            reports_dir,
            title=args.title,
            published=published,
            summary=args.summary,
            tags=args.tags,
            slug=args.slug,
        )
        write_index(reports_dir)
        print(path)
        return 0

    if args.check:
        if index_is_current(reports_dir):
            print(f"{reports_dir / INDEX_FILENAME} is up to date")
            return 0
        print(f"{reports_dir / INDEX_FILENAME} is STALE — run `make reports`")
        return 1

    path = write_index(reports_dir)
    count = len(json.loads(path.read_text(encoding="utf-8"))["reports"])
    print(f"wrote {path} ({count} report(s))")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
