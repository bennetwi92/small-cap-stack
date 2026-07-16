"""Storage layer (issue #7): append-only Parquet datasets, queried on read via DuckDB.

Implements the "store raw, compute derived on read" principle. Each logical dataset (e.g.
``candidates``, ``bars``, ``snapshots``) is a directory of date-partitioned Parquet files
(``<data_dir>/<dataset>/dt=YYYY-MM-DD/part-<uuid>.parquet``). Writes are append-only and
immutable; all stats/gates are recomputed by querying the raw Parquet with DuckDB, so
methodology can change retroactively without re-collecting data.

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

        The write is atomic (temp file + ``os.replace``, same pattern as ``dashboard.write_json``):
        a crash / OOM-kill / disk-full mid-write would otherwise leave a truncated
        ``part-<uuid>.parquet`` that is never overwritten (the uuid is never reused) and that
        ``read`` / ``query`` then choke on — breaking *every* read of the dataset until someone
        finds and deletes it by hand (#248). Readers glob ``*.parquet``, so a leftover ``.tmp`` from
        a killed process is inert rather than poisonous.
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
            os.replace(tmp, path)  # atomic within the partition dir (same filesystem)
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
        """``append`` off the event loop, for callers on the loop that also services IBKR (#262).

        Parquet serialisation is blocking I/O. Today's frames are small (1 row for scanner hits /
        opportunities, ~100–200 for a symbol's bars) so this is hygiene rather than a fix for an
        observed stall — but it keeps ingestion writes off the socket's loop as frame sizes grow.
        Safe to run concurrently: every append lands on its own uuid path and ``Store`` holds no
        mutable state.
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
