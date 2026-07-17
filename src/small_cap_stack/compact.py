"""Admin command: compact a dataset's closed ``dt=`` partitions into one file each (#319).

The store's one-file-per-append writer left `scanner_hits` with ~32k one-row files for ~32k rows,
and for this store **read cost tracks file count** (CLAUDE.md): every read/query opens each
file's footer, so the every-60s `_data_counts` query burned ~17.6s/tick against the legacy
files even after #267 fixed the writer. Compaction rewrites each closed partition's files into a
single Parquet file with the **same rows** — never deduped, filtered, or reordered beyond the
existing arbitrary cross-file order (rows are raw events; duplicates are meaningful).

This is the **sanctioned exception** to the store's append-only immutability (`storage.py`); the
partition's *contents* stay immutable — only the file layout changes. Consequence: the portfolio
candidate cache (`payload.py`) fingerprints partition file names/sizes/mtimes, so every compacted
day's fingerprint busts and the next `build_portfolio_payload` re-extracts those days once
(~2.5s/day of compute; peak memory unchanged, see #273).

Safety:

- **Refuses today's (ET) partition** — it is being appended to live. Only strictly-older dates.
- Every partition is verified before the swap: the compacted file must hold the same schema and
  the exact same multiset of rows as the originals, or that partition is left untouched.
- The swap is two directory renames (old out, new in) with the originals kept until the new
  layout is in place — but `Store.read`/`query` glob file paths *then* open them, so a reader
  racing the swap can still error on a vanished path. **Run it with the app stopped** (the
  sanctioned mode, `deploy/RUNBOOK.md`); a verified restic snapshot must exist first.

Usage (box, app stopped — or against a copy of the store)::

    python -m small_cap_stack.compact --dataset scanner_hits --start 2026-07-01 --end 2026-07-16
    python -m small_cap_stack.compact --dataset scanner_hits --date 2026-07-01 --data-dir /copy
"""

from __future__ import annotations

import argparse
import os
import shutil
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from uuid import uuid4

import polars as pl

from .clock import now_et
from .logging import configure_logging, get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class CompactionResult:
    """One partition's outcome. ``files_before == files_after`` means it was left as-is."""

    dataset: str
    dt: date
    files_before: int
    files_after: int
    rows: int


def _partition_dir(data_dir: Path, dataset: str, dt: date) -> Path:
    return data_dir / dataset / f"dt={dt.isoformat()}"


def _read_all(files: list[Path]) -> pl.DataFrame:
    """Concatenate the partition's files by column name (same tolerance as ``Store.read``)."""
    return pl.concat([pl.read_parquet(f) for f in files], how="diagonal_relaxed")


def _same_rows(before: pl.DataFrame, after: pl.DataFrame) -> bool:
    """Order-insensitive multiset equality: same schema, same rows, duplicates preserved."""
    if before.schema != after.schema or before.height != after.height:
        return False
    cols = before.columns
    return before.sort(cols).equals(after.sort(cols))


def compact_partition(data_dir: Path, dataset: str, dt: date, *, today: date) -> CompactionResult:
    """Rewrite one closed partition into a single file, verifying contents before the swap."""
    if dt >= today:
        raise ValueError(f"refusing dt={dt}: not strictly before today ({today}) — live partition")
    part = _partition_dir(data_dir, dataset, dt)
    files = sorted(part.glob("*.parquet"))
    if len(files) <= 1:
        rows = _read_all(files).height if files else 0
        return CompactionResult(dataset, dt, len(files), len(files), rows)

    before = _read_all(files)
    tmp = data_dir / dataset / f".compact.dt={dt.isoformat()}.tmp"
    old = data_dir / dataset / f".compact.dt={dt.isoformat()}.old"
    for leftover in (tmp, old):  # a previous crashed run; the dt= dir is still the source of truth
        if leftover.exists():
            raise RuntimeError(f"leftover {leftover} exists — resolve the previous run first")
    tmp.mkdir(parents=True)
    out = tmp / f"part-{uuid4().hex}.parquet"
    before.write_parquet(out)
    with open(out, "rb") as fh:  # flush the new file to disk before any rename touches the layout
        os.fsync(fh.fileno())

    after = pl.read_parquet(out)
    if not _same_rows(before, after):
        shutil.rmtree(tmp)
        raise RuntimeError(f"verification failed for {part} — originals left untouched")

    part.rename(old)
    tmp.rename(part)
    shutil.rmtree(old)
    log.info(
        "compact.partition_done",
        dataset=dataset,
        dt=dt.isoformat(),
        files_before=len(files),
        rows=before.height,
    )
    return CompactionResult(dataset, dt, len(files), 1, before.height)


def compact_dataset(
    data_dir: Path, dataset: str, start: date, end: date, *, today: date
) -> list[CompactionResult]:
    """Compact every existing partition of ``dataset`` in ``[start, end]`` (strictly pre-today)."""
    if end >= today:
        raise ValueError(f"refusing end={end}: range must stay strictly before today ({today})")
    results = []
    d = start
    while d <= end:
        if _partition_dir(data_dir, dataset, d).exists():
            results.append(compact_partition(data_dir, dataset, d, today=today))
        d += timedelta(days=1)
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dataset", required=True, help="dataset dir name, e.g. scanner_hits")
    parser.add_argument("--data-dir", default="./data", help="store root (default ./data)")
    dates = parser.add_mutually_exclusive_group(required=True)
    dates.add_argument("--date", help="single partition date YYYY-MM-DD")
    dates.add_argument("--start", help="range start YYYY-MM-DD (needs --end)")
    parser.add_argument("--end", help="range end YYYY-MM-DD, inclusive")
    args = parser.parse_args(argv)
    if (args.start is None) != (args.end is None):
        parser.error("--start and --end go together")

    configure_logging(level="INFO", json_logs=False)
    start = date.fromisoformat(args.date or args.start)
    end = date.fromisoformat(args.date or args.end)
    results = compact_dataset(Path(args.data_dir), args.dataset, start, end, today=now_et().date())
    for r in results:
        marker = "compacted" if r.files_after < r.files_before else "unchanged"
        print(f"dt={r.dt} {marker}: {r.files_before} -> {r.files_after} files, {r.rows} rows")
    total_before = sum(r.files_before for r in results)
    total_after = sum(r.files_after for r in results)
    print(f"total: {total_before} -> {total_after} files across {len(results)} partitions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
