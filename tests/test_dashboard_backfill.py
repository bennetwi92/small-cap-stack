"""Tests for the on-demand dashboard back-fill command (regenerate stats.json + charts.json)."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from small_cap_stack.config import Settings
from small_cap_stack.dashboard_backfill import (
    _collected_dates,
    _parse_date,
    regenerate,
    regenerate_archive,
)
from small_cap_stack.storage import Store

_DAY = date(2026, 6, 29)
_T0 = datetime(2026, 6, 29, 14, 0, tzinfo=UTC)


def _settings(tmp_path: Path) -> Settings:
    return Settings(_env_file=None, data_dir=tmp_path)  # type: ignore[call-arg]


def _seed_day(store: Store, day: date, symbol: str) -> None:
    t0 = datetime(day.year, day.month, day.day, 14, 0, tzinfo=UTC)
    oid = f"{day.isoformat()}:{symbol}"
    store.append(
        "opportunities",
        [
            {
                "opportunity_id": oid,
                "symbol": symbol,
                "con_id": 1,
                "trading_date": day,
                "first_seen_utc": t0,
                "first_rank": 0,
            }
        ],
        partition_date=day,
    )
    store.append(
        "scanner_hits",
        [{"opportunity_id": oid, "symbol": symbol, "ts_utc": t0, "rank": 0}],
        partition_date=day,
    )
    # A pole then a flag breakout — enough bars for build_charts to emit a drawable series.
    bars = [
        (0, 5.0, 6.2, 4.9, 6.0),
        (1, 6.0, 6.1, 5.6, 5.7),
        (2, 5.7, 6.5, 5.7, 6.4),
    ]
    store.append(
        "bars",
        [
            {
                "opportunity_id": oid,
                "symbol": symbol,
                "bar_start_utc": t0 + timedelta(minutes=5 * i),
                "open": o,
                "high": h,
                "low": low,
                "close": c,
                "volume": 1e3,
            }
            for i, o, h, low, c in bars
        ],
        partition_date=day,
    )


def _seed(store: Store) -> None:
    _seed_day(store, _DAY, "AZI")


def test_regenerate_writes_both_files(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    store = Store(tmp_path)
    _seed(store)

    n_opps, n_charts = regenerate(_DAY, settings=settings, store=store)

    stats_path = tmp_path / "dashboard" / "stats.json"
    charts_path = tmp_path / "dashboard" / "charts.json"
    assert stats_path.exists() and charts_path.exists()

    stats = json.loads(stats_path.read_text())
    charts = json.loads(charts_path.read_text())
    assert stats["trading_date"] == "2026-06-29"
    assert charts["trading_date"] == "2026-06-29"
    assert len(stats["opportunities"]) == n_opps == 1
    assert len(charts["charts"]) == n_charts >= 1

    # Dated per-date file (never overwritten) + the review index are written too (#141).
    dated = json.loads((tmp_path / "dashboard" / "charts" / "2026-06-29.json").read_text())
    assert dated["trading_date"] == "2026-06-29"
    index = json.loads((tmp_path / "dashboard" / "index.json").read_text())
    assert [d["date"] for d in index["dates"]] == ["2026-06-29"]
    assert index["dates"][0]["opportunities"][0]["symbol"] == "AZI"


def test_regenerate_preserves_other_dates_in_index(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    store = Store(tmp_path)
    _seed_day(store, date(2026, 6, 29), "AZI")
    _seed_day(store, date(2026, 6, 30), "BZI")

    regenerate(date(2026, 6, 29), settings=settings, store=store)
    regenerate(date(2026, 6, 30), settings=settings, store=store)

    index = json.loads((tmp_path / "dashboard" / "index.json").read_text())
    # Both dates survive; the second regenerate upserts without dropping the first (newest first).
    assert [d["date"] for d in index["dates"]] == ["2026-06-30", "2026-06-29"]


def test_regenerate_archive_backfills_every_date(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    store = Store(tmp_path)
    _seed_day(store, date(2026, 6, 27), "AZI")
    _seed_day(store, date(2026, 6, 29), "BZI")
    _seed_day(store, date(2026, 6, 30), "CZI")

    n_dates, n_charts = regenerate_archive(settings=settings, store=store)
    assert n_dates == 3 and n_charts >= 3

    out = tmp_path / "dashboard"
    for d in ("2026-06-27", "2026-06-29", "2026-06-30"):
        assert (out / "charts" / f"{d}.json").exists()

    index = json.loads((out / "index.json").read_text())
    assert [d["date"] for d in index["dates"]] == ["2026-06-30", "2026-06-29", "2026-06-27"]
    # Legacy single-day dashboard lands on the newest session.
    assert json.loads((out / "charts.json").read_text())["trading_date"] == "2026-06-30"
    assert json.loads((out / "stats.json").read_text())["trading_date"] == "2026-06-30"


def test_collected_dates_empty_store(tmp_path: Path) -> None:
    assert _collected_dates(Store(tmp_path)) == []


def test_regenerate_archive_empty_store_writes_empty_index(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    n_dates, n_charts = regenerate_archive(settings=settings, store=Store(tmp_path))
    assert n_dates == 0 and n_charts == 0
    index = json.loads((tmp_path / "dashboard" / "index.json").read_text())
    assert index["dates"] == []


def test_regenerate_empty_day_writes_empty_charts(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    store = Store(tmp_path)  # no data seeded

    n_opps, n_charts = regenerate(date(2026, 6, 30), settings=settings, store=store)

    charts = json.loads((tmp_path / "dashboard" / "charts.json").read_text())
    assert n_opps == 0 and n_charts == 0
    assert charts["charts"] == []


def test_parse_date_defaults_to_yesterday() -> None:
    assert _parse_date("2026-07-01") == date(2026, 7, 1)
    # default is strictly before today (ET) — a stable property without freezing the clock
    assert _parse_date(None) < datetime.now(UTC).date() + timedelta(days=1)
