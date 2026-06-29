"""Storage layer (issue #7): append-only Parquet datasets, queried on read via DuckDB.

Implements the "store raw, compute derived on read" principle. Each logical dataset (e.g.
``candidates``, ``bars``, ``snapshots``) is a directory of date-partitioned Parquet files
(``<data_dir>/<dataset>/dt=YYYY-MM-DD/part-<uuid>.parquet``). Writes are append-only and
immutable; all stats/gates are recomputed by querying the raw Parquet with DuckDB, so
methodology can change retroactively without re-collecting data.

No long-running daemon: DuckDB is embedded and opened per query.
"""

from __future__ import annotations

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
        """Append records as one immutable Parquet file under the date partition."""
        rows = [dict(r) for r in records]
        if not rows:
            return None
        part_dir = self.data_dir / dataset / f"dt={partition_date.isoformat()}"
        part_dir.mkdir(parents=True, exist_ok=True)
        path = part_dir / f"part-{uuid4().hex}.parquet"
        pl.DataFrame(rows).write_parquet(path)
        return path

    def read(self, dataset: str) -> pl.DataFrame:
        """Read a whole dataset (empty frame if it has no data yet)."""
        glob = self.data_dir / dataset / "**" / "*.parquet"
        if not any((self.data_dir / dataset).glob("**/*.parquet")):
            return pl.DataFrame()
        con = duckdb.connect()
        try:
            con.execute("SET TimeZone='UTC'")  # deterministic, host-tz-independent
            # union_by_name tolerates schema drift across append files (e.g. a nullable column
            # that's all-null on some days), matching columns by name rather than position.
            result: pl.DataFrame = con.execute(
                "SELECT * FROM read_parquet(?, hive_partitioning=1, union_by_name=1)", [str(glob)]
            ).pl()
            return result
        finally:
            con.close()

    def query(self, sql: str) -> pl.DataFrame:
        """Run SQL with each populated dataset exposed as a view of its raw Parquet."""
        con = duckdb.connect()
        try:
            con.execute("SET TimeZone='UTC'")  # deterministic, host-tz-independent
            for dataset in self._datasets():
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

    def _datasets(self) -> list[str]:
        if not self.data_dir.exists():
            return []
        return sorted(
            p.name for p in self.data_dir.iterdir() if p.is_dir() and any(p.glob("**/*.parquet"))
        )
