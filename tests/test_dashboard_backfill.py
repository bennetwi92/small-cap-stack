"""Tests for the on-demand dashboard back-fill command (regenerate stats.json + charts.json)."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from small_cap_stack.config import Settings
from small_cap_stack.dashboard_backfill import _parse_date, regenerate
from small_cap_stack.storage import Store

_DAY = date(2026, 6, 29)
_T0 = datetime(2026, 6, 29, 14, 0, tzinfo=UTC)


def _settings(tmp_path: Path) -> Settings:
    return Settings(_env_file=None, data_dir=tmp_path)  # type: ignore[call-arg]


def _seed(store: Store) -> None:
    store.append(
        "opportunities",
        [
            {
                "opportunity_id": "2026-06-29:AZI",
                "symbol": "AZI",
                "con_id": 1,
                "trading_date": _DAY,
                "first_seen_utc": _T0,
                "first_rank": 0,
            }
        ],
        partition_date=_DAY,
    )
    store.append(
        "scanner_hits",
        [{"opportunity_id": "2026-06-29:AZI", "symbol": "AZI", "ts_utc": _T0, "rank": 0}],
        partition_date=_DAY,
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
                "opportunity_id": "2026-06-29:AZI",
                "symbol": "AZI",
                "bar_start_utc": _T0 + timedelta(minutes=5 * i),
                "open": o,
                "high": h,
                "low": low,
                "close": c,
                "volume": 1e3,
            }
            for i, o, h, low, c in bars
        ],
        partition_date=_DAY,
    )


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
