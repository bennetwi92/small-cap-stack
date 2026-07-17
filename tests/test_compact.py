"""Tests for the partition compaction tool (#319)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import polars as pl
import pytest

from small_cap_stack.compact import compact_dataset, compact_partition
from small_cap_stack.storage import Store

_TODAY = date(2026, 7, 17)
_D1 = date(2026, 7, 1)
_D2 = date(2026, 7, 2)


def _hit(oid: str, minute: int, rank: int = 0) -> dict[str, object]:
    return {
        "opportunity_id": oid,
        "symbol": oid.split(":")[1],
        "ts_utc": datetime(2026, 7, 1, 13, minute, tzinfo=UTC),
        "rank": rank,
    }


def _seed_many_files(store: Store, dt: date, n: int) -> None:
    for i in range(n):  # one append per row = one file per row, the pre-#267 explosion
        store.append("scanner_hits", [_hit(f"{dt}:AZI", i % 60, rank=i)], partition_date=dt)


def _files(tmp_path: Path, dt: date) -> list[Path]:
    return sorted((tmp_path / "scanner_hits" / f"dt={dt.isoformat()}").glob("*.parquet"))


def test_compact_partition_merges_files_and_preserves_rows(tmp_path: Path) -> None:
    store = Store(tmp_path)
    _seed_many_files(store, _D1, 12)
    # A duplicate row: scanner hits are raw events, duplicates are meaningful and must survive.
    store.append("scanner_hits", [_hit(f"{_D1}:AZI", 0, rank=0)], partition_date=_D1)
    before = store.read("scanner_hits", dt=_D1)
    assert len(_files(tmp_path, _D1)) == 13

    r = compact_partition(tmp_path, "scanner_hits", _D1, today=_TODAY)
    assert (r.files_before, r.files_after, r.rows) == (13, 1, 13)
    assert len(_files(tmp_path, _D1)) == 1

    after = store.read("scanner_hits", dt=_D1)
    assert after.schema == before.schema  # incl. the hive-derived dt column
    cols = list(before.columns)
    assert after.sort(cols).equals(before.sort(cols))  # same multiset, duplicate intact
    assert after.height == 13


def test_compact_refuses_today_and_future(tmp_path: Path) -> None:
    store = Store(tmp_path)
    _seed_many_files(store, _TODAY, 3)
    with pytest.raises(ValueError, match="live partition"):
        compact_partition(tmp_path, "scanner_hits", _TODAY, today=_TODAY)
    with pytest.raises(ValueError, match="strictly before"):
        compact_dataset(tmp_path, "scanner_hits", _D1, _TODAY, today=_TODAY)
    assert len(_files(tmp_path, _TODAY)) == 3  # untouched


def test_compact_dataset_walks_range_and_skips_missing(tmp_path: Path) -> None:
    store = Store(tmp_path)
    _seed_many_files(store, _D1, 4)
    _seed_many_files(store, _D2, 5)
    # 2026-07-03 has no partition — the walk must skip it, not create one.
    results = compact_dataset(tmp_path, "scanner_hits", _D1, date(2026, 7, 3), today=_TODAY)
    assert [(r.dt, r.files_before, r.files_after) for r in results] == [
        (_D1, 4, 1),
        (_D2, 5, 1),
    ]
    assert not (tmp_path / "scanner_hits" / "dt=2026-07-03").exists()


def test_compact_single_file_partition_is_left_alone(tmp_path: Path) -> None:
    store = Store(tmp_path)
    store.append("scanner_hits", [_hit(f"{_D1}:AZI", 0)], partition_date=_D1)
    [only] = _files(tmp_path, _D1)
    r = compact_partition(tmp_path, "scanner_hits", _D1, today=_TODAY)
    assert (r.files_before, r.files_after, r.rows) == (1, 1, 1)
    assert _files(tmp_path, _D1) == [only]  # same file, not rewritten


def test_compact_tolerates_schema_drift_across_files(tmp_path: Path) -> None:
    # Store.read unions by column name; compaction must accept the same drift (e.g. a column
    # that only exists in later files) without dropping rows or columns.
    part = tmp_path / "scanner_hits" / f"dt={_D1.isoformat()}"
    part.mkdir(parents=True)
    pl.DataFrame({"opportunity_id": ["a"], "rank": [0]}).write_parquet(part / "part-1.parquet")
    pl.DataFrame({"opportunity_id": ["b"], "rank": [1], "extra": ["x"]}).write_parquet(
        part / "part-2.parquet"
    )
    r = compact_partition(tmp_path, "scanner_hits", _D1, today=_TODAY)
    assert (r.files_before, r.files_after, r.rows) == (2, 1, 2)
    merged = pl.read_parquet(_files(tmp_path, _D1)[0])
    assert set(merged.columns) == {"opportunity_id", "rank", "extra"}
    assert merged.sort("rank")["extra"].to_list() == [None, "x"]


def test_compact_refuses_leftover_from_crashed_run(tmp_path: Path) -> None:
    store = Store(tmp_path)
    _seed_many_files(store, _D1, 2)
    (tmp_path / "scanner_hits" / f".compact.dt={_D1.isoformat()}.tmp").mkdir()
    with pytest.raises(RuntimeError, match="leftover"):
        compact_partition(tmp_path, "scanner_hits", _D1, today=_TODAY)
    assert len(_files(tmp_path, _D1)) == 2  # untouched
