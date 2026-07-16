"""Tests for the DuckDB + Parquet storage layer (#7)."""

from __future__ import annotations

import asyncio
from datetime import date
from pathlib import Path

import pytest

from small_cap_stack.storage import Store


def _rows() -> list[dict[str, object]]:
    return [
        {"symbol": "AZI", "rank": 0, "change_pct": 56.7},
        {"symbol": "NNBR", "rank": 1, "change_pct": 38.0},
    ]


def test_append_writes_partitioned_parquet(tmp_path: Path) -> None:
    store = Store(tmp_path)
    path = store.append("candidates", _rows(), partition_date=date(2026, 6, 29))
    assert path is not None
    assert path.parent.name == "dt=2026-06-29"
    assert path.suffix == ".parquet"
    assert path.exists()


def test_append_empty_is_noop(tmp_path: Path) -> None:
    store = Store(tmp_path)
    assert store.append("candidates", [], partition_date=date(2026, 6, 29)) is None


def test_read_roundtrip_includes_partition_column(tmp_path: Path) -> None:
    store = Store(tmp_path)
    store.append("candidates", _rows(), partition_date=date(2026, 6, 29))
    df = store.read("candidates")
    assert df.height == 2
    assert set(df["symbol"].to_list()) == {"AZI", "NNBR"}
    assert "dt" in df.columns  # hive partition surfaced
    assert df["dt"].to_list()[0] == date(2026, 6, 29)


def test_read_missing_dataset_is_empty(tmp_path: Path) -> None:
    assert Store(tmp_path).read("nope").is_empty()


def test_read_scoped_to_one_date_partition(tmp_path: Path) -> None:
    # dt= scopes the read to a single partition (bounds memory for the EOD report / backfill, #180).
    store = Store(tmp_path)
    store.append("bars", [{"symbol": "AZI", "high": 1.0}], partition_date=date(2026, 6, 29))
    store.append("bars", [{"symbol": "NNBR", "high": 2.0}], partition_date=date(2026, 6, 30))
    assert store.read("bars").height == 2  # both days
    one = store.read("bars", dt=date(2026, 6, 30))
    assert one.height == 1  # only the 06-30 partition loaded
    assert one["symbol"].to_list() == ["NNBR"]
    assert one["dt"].to_list() == [date(2026, 6, 30)]  # hive column still derived
    assert store.read("bars", dt=date(2026, 7, 1)).is_empty()  # absent partition -> empty


def test_append_accumulates_across_partitions(tmp_path: Path) -> None:
    store = Store(tmp_path)
    store.append("candidates", _rows(), partition_date=date(2026, 6, 29))
    store.append(
        "candidates",
        [{"symbol": "TSLG", "rank": 0, "change_pct": 12.0}],
        partition_date=date(2026, 6, 30),
    )
    df = store.read("candidates")
    assert df.height == 3
    assert set(df["dt"].to_list()) == {date(2026, 6, 29), date(2026, 6, 30)}


def test_read_tolerates_cross_file_schema_drift(tmp_path: Path) -> None:
    # A nullable column that is all-null on one day and populated on another must still read
    # back as one frame (union_by_name) rather than raising a Parquet schema mismatch.
    store = Store(tmp_path)
    store.append(
        "fundamentals",
        [{"symbol": "AZI", "float_shares": None}],
        partition_date=date(2026, 6, 29),
    )
    store.append(
        "fundamentals",
        [{"symbol": "BZI", "float_shares": 8_000_000}],
        partition_date=date(2026, 6, 30),
    )
    df = store.read("fundamentals")
    assert df.height == 2
    assert set(df["symbol"].to_list()) == {"AZI", "BZI"}
    # also reachable via the query() view path
    out = store.query("SELECT count(*) AS n FROM fundamentals WHERE float_shares IS NULL")
    assert out["n"].to_list() == [1]


def test_query_computes_on_read(tmp_path: Path) -> None:
    store = Store(tmp_path)
    store.append("candidates", _rows(), partition_date=date(2026, 6, 29))
    out = store.query("SELECT count(*) AS n, max(change_pct) AS top FROM candidates")
    assert out["n"].to_list() == [2]
    assert out["top"].to_list() == [56.7]


# --- atomic writes (#248) ---------------------------------------------------------------------


def test_append_leaves_no_tmp_file_behind(tmp_path: Path) -> None:
    store = Store(tmp_path)
    path = store.append("candidates", _rows(), partition_date=date(2026, 6, 29))
    assert path is not None
    assert list(path.parent.glob("*.tmp")) == []


def test_read_ignores_a_leftover_tmp_from_a_killed_write(tmp_path: Path) -> None:
    """A process killed mid-write leaves a truncated ``.tmp``; it must not poison reads (#248)."""
    store = Store(tmp_path)
    store.append("candidates", _rows(), partition_date=date(2026, 6, 29))
    part_dir = tmp_path / "candidates" / "dt=2026-06-29"
    (part_dir / "part-deadbeef.parquet.tmp").write_bytes(b"PAR1-truncated-garbage")

    assert store.read("candidates").height == 2  # would raise if the .tmp were globbed
    assert store.query("SELECT count(*) AS n FROM candidates")["n"].to_list() == [2]


def test_append_cleans_up_tmp_when_the_write_fails(tmp_path: Path) -> None:
    """A failed write must not strand a ``.tmp`` (nor a half-written final part)."""
    store = Store(tmp_path)
    # A column of un-serialisable objects makes write_parquet raise after the tmp path is chosen.
    with pytest.raises(Exception):  # noqa: B017 — polars' concrete type is version-dependent
        store.append("candidates", [{"bad": object()}], partition_date=date(2026, 6, 29))
    part_dir = tmp_path / "candidates" / "dt=2026-06-29"
    if part_dir.exists():
        assert list(part_dir.glob("*.tmp")) == []
        assert list(part_dir.glob("*.parquet")) == []


# --- off-loop writes (#262) -------------------------------------------------------------------


def test_append_async_matches_append(tmp_path: Path) -> None:
    store = Store(tmp_path)
    path = asyncio.run(store.append_async("candidates", _rows(), partition_date=date(2026, 6, 29)))
    assert path is not None and path.exists()
    assert store.read("candidates").height == 2


def test_append_async_empty_is_noop(tmp_path: Path) -> None:
    store = Store(tmp_path)
    assert (
        asyncio.run(store.append_async("candidates", [], partition_date=date(2026, 6, 29))) is None
    )


def test_append_async_does_not_block_the_event_loop(tmp_path: Path) -> None:
    """The write must run off-loop, so other loop work interleaves with it (#262)."""
    store = Store(tmp_path)
    order: list[str] = []

    async def scenario() -> None:
        async def ticker() -> None:
            for _ in range(3):
                await asyncio.sleep(0)
                order.append("loop")

        await asyncio.gather(
            store.append_async("candidates", _rows(), partition_date=date(2026, 6, 29)),
            ticker(),
        )
        order.append("done")

    asyncio.run(scenario())
    # The loop kept running while the write was in flight rather than being stalled until "done".
    assert order.count("loop") == 3
    assert order.index("loop") < order.index("done")
