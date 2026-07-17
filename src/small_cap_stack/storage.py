"""Storage layer (issue #7): append-only Parquet datasets, queried on read via DuckDB.

Implements the "store raw, compute derived on read" principle. Each logical dataset (e.g.
``candidates``, ``bars``, ``snapshots``) is a directory of date-partitioned Parquet files
(``<data_dir>/<dataset>/dt=YYYY-MM-DD/part-<uuid>.parquet``). Writes are append-only and
immutable; all stats/gates are recomputed by querying the raw Parquet with DuckDB, so
methodology can change retroactively without re-collecting data.

**Sanctioned exception (#319):** ``compact.py`` may rewrite a *closed* (strictly pre-today)
partition's files into one file with verified-identical contents — the row set stays immutable,
only the file layout changes (for this store, read cost tracks file count). Everything reasoning
from immutability (e.g. the portfolio candidate cache's file fingerprint) must tolerate a
compaction as "the partition changed, recompute".

No long-running daemon: DuckDB is embedded and opened per query.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Any
from uuid import uuid4

import duckdb
import polars as pl


class Store:
    """Append-only, date-partitioned Parquet datasets with DuckDB query-on-read."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir)

    def append(
        self,
        dataset: str,
        records: Sequence[Mapping[str, Any]],
        *,
        partition_date: date,
    ) -> Path | None:
        """Append records as one immutable Parquet file under the date partition.

        Writing straight to the final name would let a mid-write failure strand a *truncated*
        ``part-<uuid>.parquet``. It is never overwritten (the uuid is never reused) and ``read`` /
        ``query`` glob the whole directory, so DuckDB then errors on it — breaking *every* read of
        that dataset until someone finds and deletes it by hand (#248).

        So: serialise to ``part-<uuid>.parquet.tmp``, fsync it, ``os.replace`` into place, then
        fsync the partition dir. Both halves are load-bearing and cover different failures:

        - ``os.replace`` is atomic, so readers only ever see a complete file, and a process death
          (the OOM-kill CLAUDE.md warns is routine on the CX22) leaves an inert ``.tmp`` — readers
          glob ``*.parquet``, which never matches it.
        - the fsyncs add *durability*, which the rename alone does not give. Without them a
          host-level crash — a kernel panic, or the hard reboot CLAUDE.md documents as the standard
          recovery when the box OOM-thrashes past sshd — can replay the rename's metadata while the
          data blocks are still dirty, committing a truncated file under the *final, globbed* name.
          That is #248 again, and worse: indistinguishable from a legitimate part. Three months of
          collected Parquet is the Phase-1 deliverable, so this is worth the ms.
        """
        rows = [dict(r) for r in records]
        if not rows:
            return None
        part_dir = self.data_dir / dataset / f"dt={partition_date.isoformat()}"
        part_dir.mkdir(parents=True, exist_ok=True)
        path = part_dir / f"part-{uuid4().hex}.parquet"
        tmp = path.with_name(path.name + ".tmp")
        try:
            pl.DataFrame(rows).write_parquet(tmp)
            with open(tmp, "rb") as fh:  # flush the data blocks before the rename commits
                os.fsync(fh.fileno())
            os.replace(tmp, path)  # atomic within the partition dir (same filesystem)
            dir_fd = os.open(part_dir, os.O_RDONLY)  # ...and persist the rename itself
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except BaseException:
            tmp.unlink(missing_ok=True)  # don't leave partials behind on a recoverable failure
            raise
        return path

    async def append_async(
        self,
        dataset: str,
        records: Sequence[Mapping[str, Any]],
        *,
        partition_date: date,
    ) -> Path | None:
        """``append`` off the event loop. **The supported path for any caller on the loop** (#262).

        Parquet serialisation (now plus an fsync) is blocking I/O, and the loop it would block also
        services the IBKR socket. Today's frames are small — 1 row for scanner hits / opportunities,
        ~100–200 for a symbol's bars — so this is hygiene rather than a fix for an observed stall;
        it is here so that stays true as frames grow. Call this, not ``append``, from ``async def``:
        every sync call site left on the loop is how the problem comes back.

        No data race: each append lands on its own uuid path and ``Store`` holds no mutable state.
        That is *not* a claim about capacity — this shares the loop's default executor (6 workers on
        the 2-vCPU box) with the ``fundamentals`` fan-out and the ``monitoring`` heartbeat, so if
        appends ever become slow enough to occupy workers, they can queue behind/ahead of those. At
        ms-scale frames that is latent; a dedicated single-worker executor is the fix if it isn't.
        """
        return await asyncio.to_thread(self.append, dataset, records, partition_date=partition_date)

    def read(self, dataset: str, *, dt: date | None = None) -> pl.DataFrame:
        """Read a dataset (empty frame if it has no data yet).

        ``dt`` scopes the read to a single ``dt=YYYY-MM-DD`` partition — loading just that day's
        files instead of the whole history. The EOD report / dashboard backfill use it to bound
        memory (reading all of ``bars`` for one date otherwise pulls the entire dataset, ~1.4 GB,
        and OOMs the box). The path still carries ``dt=<date>`` so hive partitioning derives the
        ``dt`` column identically to a full read.
        """
        # One filesystem walk: the resolved file list doubles as the emptiness check and is passed
        # straight to DuckDB, which avoids re-globbing the pattern a second time internally. Hive
        # partitioning still derives the ``dt`` column from each path, identical to a glob read.
        root = self.data_dir / dataset
        if dt is not None:
            root = root / f"dt={dt.isoformat()}"
        files = sorted(str(p) for p in root.glob("**/*.parquet"))
        if not files:
            return pl.DataFrame()
        con = duckdb.connect()
        try:
            con.execute("SET TimeZone='UTC'")  # deterministic, host-tz-independent
            # union_by_name tolerates schema drift across append files (e.g. a nullable column
            # that's all-null on some days), matching columns by name rather than position.
            result: pl.DataFrame = con.execute(
                "SELECT * FROM read_parquet(?, hive_partitioning=1, union_by_name=1)", [files]
            ).pl()
            return result
        finally:
            con.close()

    def query(self, sql: str) -> pl.DataFrame:
        """Run SQL with each populated dataset exposed as a view of its raw Parquet."""
        con = duckdb.connect()
        try:
            con.execute("SET TimeZone='UTC'")  # deterministic, host-tz-independent
            for dataset in self.datasets():
                glob = str(self.data_dir / dataset / "**" / "*.parquet").replace("'", "''")
                view = dataset.replace('"', '""')  # quote the identifier defensively
                con.execute(
                    f'CREATE VIEW "{view}" AS '
                    f"SELECT * FROM read_parquet('{glob}', hive_partitioning=1, union_by_name=1)"
                )
            result: pl.DataFrame = con.execute(sql).pl()
            return result
        finally:
            con.close()

    def datasets(self) -> list[str]:
        """Names of datasets that currently hold at least one Parquet file."""
        if not self.data_dir.exists():
            return []
        return sorted(
            p.name for p in self.data_dir.iterdir() if p.is_dir() and any(p.glob("**/*.parquet"))
        )

    def file_counts(self) -> dict[str, int]:
        """Parquet file count per dataset directory.

        For this store the number that prices a read is the FILE count, not rows or bytes —
        every read/query opens each file's footer, so 32k one-row files cost ~40x the same rows
        in a few hundred files (#318/#319). Exposed as a metric/status field so the small-file
        explosion is a number on a chart, not folklore (#321)."""
        if not self.data_dir.exists():
            return {}
        return {
            p.name: sum(1 for _ in p.glob("**/*.parquet"))
            for p in sorted(self.data_dir.iterdir())
            if p.is_dir()
        }
