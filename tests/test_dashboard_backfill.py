"""Tests for the on-demand dashboard back-fill command (regenerate stats.json + charts.json)."""

from __future__ import annotations

import json
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from small_cap_stack.config import Settings
from small_cap_stack.dashboard import index_entry, index_from_entries
from small_cap_stack.dashboard_backfill import (
    _parse_date,
    main,
    regenerate,
    regenerate_archive,
)
from small_cap_stack.portfolio import collected_dates
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
    # dashboard_backfill delegates to portfolio.collected_dates now (#257) — one implementation.
    assert collected_dates(Store(tmp_path)) == []


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


# --- --all guard (#261) ------------------------------------------------------------------------


def test_all_without_force_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """--all is the flag that OOM-killed the box (#264); it must not fire on a bare invocation."""
    monkeypatch.setattr(sys, "argv", ["dashboard_backfill", "--all"])
    ran: list[str] = []
    monkeypatch.setattr(
        "small_cap_stack.dashboard_backfill.regenerate_archive",
        lambda *a, **k: ran.append("archive") or (0, 0),
    )
    with pytest.raises(SystemExit) as e:
        main()
    assert e.value.code == 2  # argparse usage error
    assert ran == []  # and crucially: the archive rebuild never started


def test_all_with_force_runs_the_archive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["dashboard_backfill", "--all", "--force"])
    ran: list[str] = []
    monkeypatch.setattr(
        "small_cap_stack.dashboard_backfill.regenerate_archive",
        lambda *a, **k: (ran.append("archive"), (2, 5))[1],
    )
    main()
    assert ran == ["archive"]


def test_force_alone_does_not_imply_all(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """--force is a modifier on --all, not a mode: it must not trigger an archive rebuild."""
    monkeypatch.setattr(sys, "argv", ["dashboard_backfill", "--date", "2026-07-01", "--force"])
    ran: list[str] = []
    monkeypatch.setattr(
        "small_cap_stack.dashboard_backfill.regenerate_archive",
        lambda *a, **k: (ran.append("archive"), (0, 0))[1],
    )
    monkeypatch.setattr(
        "small_cap_stack.dashboard_backfill.regenerate",
        lambda d, *a, **k: (ran.append(f"date:{d.isoformat()}"), (0, 0))[1],
    )
    main()
    assert ran == ["date:2026-07-01"]


# --- index memory shape (#261) -----------------------------------------------------------------


def test_archive_index_does_not_retain_full_chart_payloads(tmp_path: Path) -> None:
    """The archive index must be built from per-date rows, not a list of every charts payload.

    Retaining every date's full charts (all bars, all opportunities, all dates) purely to build the
    index is what made --all a memory bomb on the 4 GB box. Pin the row shape so the reduction
    can't quietly regress back to holding payloads.
    """
    chart = {
        "opportunity_id": "2026-07-01:AZI",
        "symbol": "AZI",
        "run": 1,
        "run_count": 1,
        "triggered": True,
        "max_r": 2.5,
        "bars": [{"t": 1, "o": 1.0, "h": 2.0, "low": 0.5, "c": 1.5}] * 200,  # the bulky part
    }
    entry = index_entry(date(2026, 7, 1), {"charts": [chart]})

    assert set(entry) == {"date", "opportunities"}
    assert entry["date"] == "2026-07-01"
    # The row carries only navigation fields — the bars payload is left behind, not retained.
    assert set(entry["opportunities"][0]) == {
        "opportunity_id",
        "symbol",
        "run",
        "run_count",
        "triggered",
        "max_r",
    }

    idx = index_from_entries(
        [
            index_entry(date(2026, 7, 1), {"charts": []}),
            index_entry(date(2026, 7, 2), {"charts": []}),
        ],
        datetime(2026, 7, 3, tzinfo=UTC),
    )
    assert [d["date"] for d in idx["dates"]] == ["2026-07-02", "2026-07-01"]  # newest-first
