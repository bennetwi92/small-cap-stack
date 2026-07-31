"""Reports index builder: front-matter parsing, ordering, and the committed index's freshness."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from small_cap_stack.reports import (
    REPORTS_DIRNAME,
    Report,
    ReportError,
    build_index,
    collect_reports,
    count_words,
    index_is_current,
    main,
    parse_front_matter,
    parse_report,
    scaffold_report,
    slugify,
    write_index,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def write(dirpath: Path, name: str, text: str) -> Path:
    path = dirpath / name
    path.write_text(text, encoding="utf-8")
    return path


DOC = """---
title: Float gate revisited
published: 2026-07-20
summary: What the 20M cap costs us.
tags: strategy, data
---

# Body

Four words here now.
"""


# ---------------------------------------------------------------- front matter


def test_parse_front_matter_splits_meta_and_body() -> None:
    meta, body = parse_front_matter(DOC)
    assert meta == {
        "title": "Float gate revisited",
        "published": "2026-07-20",
        "summary": "What the 20M cap costs us.",
        "tags": "strategy, data",
    }
    assert body.startswith("# Body")


def test_parse_front_matter_keeps_colons_in_values() -> None:
    meta, _ = parse_front_matter("---\ntitle: Engine v2: the how\npublished: 2026-07-20\n---\n")
    assert meta["title"] == "Engine v2: the how"


def test_parse_front_matter_tolerates_blank_lines() -> None:
    meta, body = parse_front_matter("---\n\ntitle: T\n\npublished: 2026-07-20\n---\nbody\n")
    assert meta["title"] == "T"
    assert body == "body"


@pytest.mark.parametrize(
    ("text", "match"),
    [
        ("# no front matter\n", "must start with"),
        ("---\ntitle: T\n", "not closed"),
        ("---\ntitle T\n---\n", "not `key: value`"),
        ("---\nsumary: typo\n---\n", "unknown front-matter key"),
        ("---\ntitle: A\ntitle: B\n---\n", "duplicate front-matter key"),
    ],
)
def test_parse_front_matter_rejects_malformed(text: str, match: str) -> None:
    with pytest.raises(ReportError, match=match):
        parse_front_matter(text)


# ---------------------------------------------------------------- one report


def test_parse_report_maps_every_field(tmp_path: Path) -> None:
    report = parse_report(write(tmp_path, "2026-07-20-float-gate.md", DOC))
    assert report == Report(
        slug="2026-07-20-float-gate",
        file="2026-07-20-float-gate.md",
        title="Float gate revisited",
        published="2026-07-20",
        summary="What the 20M cap costs us.",
        tags=["strategy", "data"],
        author="Claude",
        words=5,  # "Body" + the four-word sentence; front matter and the `#` don't count
    )


def test_count_words_ignores_markdown_punctuation() -> None:
    assert count_words("## Heading\n\n- one\n- two\n\n| a | b |\n| --- | --- |\n") == 5


def test_parse_report_respects_an_explicit_author(tmp_path: Path) -> None:
    doc = "---\ntitle: T\npublished: 2026-07-20\nauthor: Ben\n---\nbody\n"
    assert parse_report(write(tmp_path, "r.md", doc)).author == "Ben"


def test_parse_report_accepts_a_full_datetime(tmp_path: Path) -> None:
    doc = "---\ntitle: T\npublished: 2026-07-20T14:30:00+00:00\n---\nbody\n"
    assert parse_report(write(tmp_path, "r.md", doc)).published == "2026-07-20T14:30:00+00:00"


@pytest.mark.parametrize(
    ("doc", "match"),
    [
        ("---\npublished: 2026-07-20\n---\nb\n", "missing required front-matter key"),
        ("---\ntitle: T\n---\nb\n", "missing required front-matter key"),
        ("---\ntitle: T\npublished:\n---\nb\n", "missing required front-matter key"),
        ("---\ntitle: T\npublished: yesterday\n---\nb\n", "not an ISO date"),
    ],
)
def test_parse_report_rejects_bad_metadata(tmp_path: Path, doc: str, match: str) -> None:
    path = write(tmp_path, "bad.md", doc)
    with pytest.raises(ReportError, match=match):
        parse_report(path)


def test_parse_report_error_names_the_file(tmp_path: Path) -> None:
    with pytest.raises(ReportError, match="bad.md:"):
        parse_report(write(tmp_path, "bad.md", "no front matter\n"))


# ---------------------------------------------------------------- the index


def test_collect_reports_orders_newest_first_and_breaks_ties_by_slug(tmp_path: Path) -> None:
    write(tmp_path, "b-old.md", "---\ntitle: Old\npublished: 2026-07-01\n---\nx\n")
    write(tmp_path, "a-new.md", "---\ntitle: New\npublished: 2026-07-30\n---\nx\n")
    write(tmp_path, "c-same.md", "---\ntitle: Same\npublished: 2026-07-30\n---\nx\n")
    assert [r.slug for r in collect_reports(tmp_path)] == ["c-same", "a-new", "b-old"]


def test_collect_reports_sorts_dates_and_datetimes_together(tmp_path: Path) -> None:
    write(tmp_path, "morning.md", "---\ntitle: M\npublished: 2026-07-20T09:00:00+00:00\n---\nx\n")
    write(tmp_path, "midnight.md", "---\ntitle: D\npublished: 2026-07-20\n---\nx\n")
    write(tmp_path, "later.md", "---\ntitle: L\npublished: 2026-07-21\n---\nx\n")
    assert [r.slug for r in collect_reports(tmp_path)] == ["later", "morning", "midnight"]


def test_build_index_is_empty_for_an_empty_directory(tmp_path: Path) -> None:
    payload = build_index(tmp_path, now=datetime(2026, 7, 31, tzinfo=UTC))
    assert payload == {"generated_utc": "2026-07-31T00:00:00+00:00", "reports": []}


def test_write_index_round_trips_to_json(tmp_path: Path) -> None:
    write(tmp_path, "2026-07-20-float-gate.md", DOC)
    payload = json.loads(write_index(tmp_path).read_text(encoding="utf-8"))
    assert [r["title"] for r in payload["reports"]] == ["Float gate revisited"]
    assert payload["reports"][0]["tags"] == ["strategy", "data"]


def test_index_is_current_tracks_the_markdown(tmp_path: Path) -> None:
    assert index_is_current(tmp_path) is False  # no index at all
    write(tmp_path, "a.md", "---\ntitle: A\npublished: 2026-07-20\n---\nx\n")
    write_index(tmp_path)
    assert index_is_current(tmp_path) is True
    write(tmp_path, "b.md", "---\ntitle: B\npublished: 2026-07-21\n---\nx\n")
    assert index_is_current(tmp_path) is False  # a new report makes it stale


def test_index_is_current_survives_a_corrupt_index(tmp_path: Path) -> None:
    (tmp_path / "index.json").write_text("{not json", encoding="utf-8")
    assert index_is_current(tmp_path) is False


# ---------------------------------------------------------------- scaffolding


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Float gate revisited", "float-gate-revisited"),
        ("Engine v2 — the how", "engine-v2-the-how"),
        ("  Spaces  &  symbols!  ", "spaces-symbols"),
    ],
)
def test_slugify(title: str, expected: str) -> None:
    assert slugify(title) == expected


def test_scaffold_report_writes_parseable_front_matter(tmp_path: Path) -> None:
    path = scaffold_report(
        tmp_path,
        title="Float gate revisited",
        published=date(2026, 7, 20),
        summary="s",
        tags="strategy",
    )
    assert path.name == "2026-07-20-float-gate-revisited.md"
    report = parse_report(path)
    assert (report.title, report.published, report.tags) == (
        "Float gate revisited",
        "2026-07-20",
        ["strategy"],
    )


def test_scaffold_report_refuses_to_overwrite(tmp_path: Path) -> None:
    scaffold_report(tmp_path, title="T", published=date(2026, 7, 20))
    with pytest.raises(ReportError, match="already exists"):
        scaffold_report(tmp_path, title="T", published=date(2026, 7, 20))


# ---------------------------------------------------------------- CLI


def test_cli_build_writes_the_index(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write(tmp_path, "a.md", "---\ntitle: A\npublished: 2026-07-20\n---\nx\n")
    assert main(["--dir", str(tmp_path), "build"]) == 0
    assert (tmp_path / "index.json").exists()
    assert "1 report(s)" in capsys.readouterr().out


def test_cli_check_reports_staleness(tmp_path: Path) -> None:
    write(tmp_path, "a.md", "---\ntitle: A\npublished: 2026-07-20\n---\nx\n")
    assert main(["--dir", str(tmp_path), "build", "--check"]) == 1
    main(["--dir", str(tmp_path), "build"])
    assert main(["--dir", str(tmp_path), "build", "--check"]) == 0


def test_cli_new_creates_the_file_and_indexes_it(tmp_path: Path) -> None:
    code = main(
        ["--dir", str(tmp_path), "new", "--title", "Float gate", "--published", "2026-07-20"]
    )
    assert code == 0
    assert (tmp_path / "2026-07-20-float-gate.md").exists()
    payload = json.loads((tmp_path / "index.json").read_text(encoding="utf-8"))
    assert [r["slug"] for r in payload["reports"]] == ["2026-07-20-float-gate"]


# ---------------------------------------------------------------- the real directory


def test_committed_reports_parse_and_the_index_is_current() -> None:
    """Guard: a report added without re-running `make reports` would silently never appear."""
    reports_dir = REPO_ROOT / REPORTS_DIRNAME
    collect_reports(reports_dir)  # every committed report must parse
    assert index_is_current(reports_dir), "docs/reports/index.json is stale — run `make reports`"


def test_pages_source_bypasses_jekyll() -> None:
    """Guard: without `.nojekyll`, Pages renders each report to .html and the raw .md 404s."""
    marker = REPO_ROOT / Path(REPORTS_DIRNAME).parts[0] / ".nojekyll"
    assert marker.exists(), f"{marker.relative_to(REPO_ROOT)} is missing — reports.js fetches .md"
