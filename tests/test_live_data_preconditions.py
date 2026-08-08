"""D-43's precondition, enforced where it can be: nothing new may write to the `bars` dataset.

A `keepUpToDate` bar is **unfinalised** — its high/low/close mutate until the bar closes. If one
lands in `bars` alongside the EOD batch's finalised version of the same bar, they collide on
(`opportunity_id`, `bar_start_utc`) and the dedup at `report.py`'s
``.unique(subset="bar_start_utc", keep="first")`` resolves the tie by **store order** — which is
``sorted(glob("**/*.parquet"))``, i.e. sorted by random UUID filename. Which bar survives becomes a
coin flip that can land differently on two consecutive reads of the same day, and compaction then
freezes whichever won.

That would make every R-metric, every golden fixture and every published book number silently
non-reproducible, retroactively, for every day the stream ran. #381 already fought a milder version
of the same ordering bug.

The rule is therefore: **streamed bars go to `live_bars`, never to `bars`.** This test pins the
complete set of writers so adding one has to be a deliberate act that edits this file.
"""

from __future__ import annotations

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "small_cap_stack"

#: Every call site allowed to append to `bars`, and why it is finalised data.
#: file -> what it writes.
FINALISED_BAR_WRITERS = {
    "capture.py": "the end-of-day historical batch — complete bars, fetched after the fact",
    "harvest/runner.py": "the vendor reconstruction — complete bars, rebuilt from minute data",
}

# `append("bars"` / `append_async(\n    "bars"` — the dataset name may sit on the next line.
_APPEND_BARS = re.compile(r"""append(?:_async)?\(\s*["']bars["']""")


def _writers_of_bars() -> dict[str, list[int]]:
    found: dict[str, list[int]] = {}
    for path in sorted(SRC.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for m in _APPEND_BARS.finditer(text):
            rel = str(path.relative_to(SRC))
            found.setdefault(rel, []).append(text.count("\n", 0, m.start()) + 1)
    return found


def test_only_finalised_bar_sources_write_to_the_bars_dataset() -> None:
    """D-43. If this fails because you added a streaming writer, the fix is `live_bars`.

    Do NOT satisfy it by adding your new writer to ``FINALISED_BAR_WRITERS`` unless the bars it
    writes are genuinely complete. A bar that can still change is not a finalised bar, however it
    is fetched.
    """
    found = _writers_of_bars()
    unexpected = {f: lines for f, lines in found.items() if f not in FINALISED_BAR_WRITERS}
    assert not unexpected, (
        f"new writer(s) to the `bars` dataset: {unexpected}.\n"
        "Unfinalised (streamed) bars must go to `live_bars` — see research/decisions.md D-43. "
        "Writing them to `bars` makes which version survives dedup depend on a random UUID "
        "filename, which is silently non-deterministic and retroactive."
    )
    missing = set(FINALISED_BAR_WRITERS) - set(found)
    assert not missing, (
        f"{missing} no longer append to `bars` — if a writer moved, update FINALISED_BAR_WRITERS "
        "so this test keeps meaning something."
    )


def test_the_live_bars_dataset_is_not_read_by_the_replay_path() -> None:
    """The separation is only worth having if the read side keeps it too.

    `bars` is what the detector, the EOD report and the paper book replay. `live_bars` exists to be
    diffed against them, never merged into them. Nothing reads it yet — that is the point; this
    pins the invariant *before* the reader that would break it is written.
    """
    readers = []
    for path in sorted(SRC.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for m in re.finditer(r"""\.read\(\s*["']live_bars["']""", text):
            readers.append(f"{path.relative_to(SRC)}:{text.count(chr(10), 0, m.start()) + 1}")
    assert not readers, (
        f"`live_bars` is read at {readers}. It is the live-vs-replay comparison arm and must not "
        "feed the detector, the EOD report or the paper book — see research/decisions.md D-43."
    )
