"""Warrior Trading transcript library — findings synthesis.

`warrior_library.py` (#304) collects transcripts; the analysis pass that ran
over them (an LLM extraction per video: mood, market read, chasing/fading
setups, catalysts, lessons) was never aggregated into an actual finding —
`data/warrior-library/analysis_records.json` sat there unread. This harness
closes that loop: it aggregates the 200 records and prints the frequency
tables a rule's provenance can be checked against.

Usage:
    python spikes/warrior_library_synthesis.py
    python spikes/warrior_library_synthesis.py --json data/spikes/warrior_synthesis.json

This is a spike (exempt from mypy/tests); it is still ruff-linted.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

RECORDS = Path("data/warrior-library/analysis_records.json")


def load_records(path: Path = RECORDS) -> list[dict[str, Any]]:
    return json.loads(path.read_text())


def counter_field(records: list[dict[str, Any]], key: str) -> Counter[str]:
    """Count a scalar field (e.g. market_temp, day_result) across records."""
    return Counter(str(r.get(key)).strip().lower() for r in records if r.get(key) is not None)


def counter_list_field(records: list[dict[str, Any]], key: str) -> Counter[str]:
    """Count entries within a list-valued field (e.g. chasing, catalysts)."""
    counts: Counter[str] = Counter()
    for r in records:
        for entry in r.get(key) or []:
            counts[str(entry).strip().lower()] += 1
    return counts


def synthesize(records: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "n_records": len(records),
        "by_type": dict(counter_field(records, "type")),
        "market_temp": dict(counter_field(records, "market_temp")),
        "day_result": dict(counter_field(records, "day_result")),
        "chasing_top25": counter_list_field(records, "chasing").most_common(25),
        "fading_top25": counter_list_field(records, "fading").most_common(25),
        "catalysts_top20": counter_list_field(records, "catalysts").most_common(20),
        "lessons_top20": counter_list_field(records, "lessons").most_common(20),
    }


def render(summary: dict[str, Any]) -> str:
    lines = [f"records: {summary['n_records']}", f"by type: {summary['by_type']}", ""]
    lines.append(f"market_temp: {summary['market_temp']}")
    lines.append(f"day_result: {summary['day_result']}")
    lines.append("")
    for label, key in (
        ("CHASING", "chasing_top25"),
        ("FADING", "fading_top25"),
        ("CATALYSTS", "catalysts_top20"),
        ("LESSONS", "lessons_top20"),
    ):
        lines.append(f"=== {label} ===")
        for phrase, count in summary[key]:
            lines.append(f"{count:3d}  {phrase}")
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--records", type=Path, default=RECORDS)
    parser.add_argument("--json", type=Path, help="also write the summary as JSON")
    args = parser.parse_args()

    records = load_records(args.records)
    summary = synthesize(records)
    print(render(summary))

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(summary, indent=2))
        print(f"wrote {args.json}")


if __name__ == "__main__":
    main()
