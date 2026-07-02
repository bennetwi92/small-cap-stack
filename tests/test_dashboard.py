"""Tests for the dashboard state exporter (#68)."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

from small_cap_stack.config import Settings
from small_cap_stack.dashboard import (
    StatusInputs,
    build_charts,
    build_stats,
    build_status,
    write_json,
    write_json_if_changed,
)
from small_cap_stack.report import EodReport, OpportunityAnalysis
from small_cap_stack.storage import Store

_DAY = date(2026, 6, 29)
_TS1 = datetime(2026, 6, 29, 13, 0, tzinfo=UTC)
_TS2 = datetime(2026, 6, 29, 13, 1, tzinfo=UTC)  # a later tick
_NOW = datetime(2026, 6, 29, 13, 1, 30, tzinfo=UTC)


def _seed(store: Store) -> None:
    store.append(
        "opportunities",
        [
            {
                "opportunity_id": "2026-06-29:AZI",
                "symbol": "AZI",
                "con_id": 1,
                "trading_date": _DAY,
                "first_seen_utc": _TS1,
                "first_rank": 0,
            },
            {
                "opportunity_id": "2026-06-29:DUD",
                "symbol": "DUD",
                "con_id": 2,
                "trading_date": _DAY,
                "first_seen_utc": _TS1,
                "first_rank": 1,
            },
            # duplicate row (a restart re-opened AZI) — must not double-count
            {
                "opportunity_id": "2026-06-29:AZI",
                "symbol": "AZI",
                "con_id": 1,
                "trading_date": _DAY,
                "first_seen_utc": _TS1,
                "first_rank": 0,
            },
        ],
        partition_date=_DAY,
    )
    store.append(
        "scanner_hits",
        [
            {"opportunity_id": "2026-06-29:AZI", "symbol": "AZI", "ts_utc": _TS1, "rank": 0},
            {"opportunity_id": "2026-06-29:DUD", "symbol": "DUD", "ts_utc": _TS1, "rank": 1},
            # latest tick (_TS2): BZI ranks above AZI
            {"opportunity_id": "2026-06-29:BZI", "symbol": "BZI", "ts_utc": _TS2, "rank": 0},
            {"opportunity_id": "2026-06-29:AZI", "symbol": "AZI", "ts_utc": _TS2, "rank": 1},
        ],
        partition_date=_DAY,
    )
    store.append(
        "bars",
        [
            {
                "opportunity_id": "2026-06-29:AZI",
                "symbol": "AZI",
                "bar_start_utc": _TS1,
                "open": 1.0,
                "high": 2.0,
                "low": 0.5,
                "close": 1.5,
                "volume": 1e3,
            },
            {
                "opportunity_id": "2026-06-29:AZI",
                "symbol": "AZI",
                "bar_start_utc": _TS2,
                "open": 1.5,
                "high": 2.2,
                "low": 1.4,
                "close": 2.0,
                "volume": 1e3,
            },
            # duplicate bar row — distinct count must ignore it
            {
                "opportunity_id": "2026-06-29:AZI",
                "symbol": "AZI",
                "bar_start_utc": _TS2,
                "open": 1.5,
                "high": 2.2,
                "low": 1.4,
                "close": 2.0,
                "volume": 1e3,
            },
        ],
        partition_date=_DAY,
    )
    store.append(
        "news",
        [
            {
                "opportunity_id": "2026-06-29:AZI",
                "symbol": "AZI",
                "time": "t",
                "provider": "DJ-N",
                "headline": "h",
                "article_id": "a1",
            }
        ],
        partition_date=_DAY,
    )
    store.append(
        "fundamentals",
        [
            {
                "opportunity_id": "2026-06-29:AZI",
                "symbol": "AZI",
                "ts_utc": _TS1,
                "float_shares": 8_000_000,
                "shares_outstanding": 12_000_000,
                "short_percent": 0.2,
                "source": "yfinance",
            }
        ],
        partition_date=_DAY,
    )


def _inputs() -> StatusInputs:
    return StatusInputs(
        now=_NOW,
        trading_date=_DAY,
        connected=True,
        trading_mode="paper",
        in_scan_window=True,
        deployed_commit="abc1234",
        scan_ticks_total=42,
        jobs=[("tick", datetime(2026, 6, 29, 13, 2, tzinfo=UTC)), ("eod_bars", None)],
    )


def test_build_status_shape_and_values(tmp_path: Path) -> None:
    store = Store(tmp_path)
    _seed(store)
    st = build_status(store, _inputs())

    assert st["generated_utc"] == _NOW.isoformat()
    assert st["trading_date"] == "2026-06-29"

    svc = st["service"]
    assert svc["connected"] is True and svc["trading_mode"] == "paper"
    assert svc["in_scan_window"] is True and svc["deployed_commit"] == "abc1234"
    assert svc["jobs"] == [
        {"id": "tick", "next_run_utc": "2026-06-29T13:02:00+00:00"},
        {"id": "eod_bars", "next_run_utc": None},
    ]

    scn = st["scanner"]
    assert scn["scan_ticks_total"] == 42
    assert scn["last_scan_utc"] == _TS2.isoformat()
    # latest tick, ordered by rank: BZI (0) before AZI (1)
    assert scn["latest_candidates"] == [
        {"symbol": "BZI", "rank": 0},
        {"symbol": "AZI", "rank": 1},
    ]

    assert st["opportunities"] == {"open_today": 2, "symbols": ["AZI", "DUD"]}


def test_build_status_counts_are_distinct_aware(tmp_path: Path) -> None:
    store = Store(tmp_path)
    _seed(store)
    data = build_status(store, _inputs())["data"]

    assert data["opportunities"] == {"today": 2, "total": 2}  # dup AZI row collapsed
    assert data["bars"] == {"today": 2, "total": 2}  # dup bar row collapsed
    assert data["scanner_hits"] == {"today": 4, "total": 4}  # each hit is a real event
    assert data["news"] == {"today": 1, "total": 1}
    assert data["fundamentals"] == {"today": 1, "total": 1}


def test_build_status_empty_store(tmp_path: Path) -> None:
    st = build_status(Store(tmp_path), _inputs())
    assert st["scanner"]["latest_candidates"] == [] and st["scanner"]["last_scan_utc"] is None
    assert st["opportunities"] == {"open_today": 0, "symbols": []}
    assert st["data"]["bars"] == {"today": 0, "total": 0}


def test_build_stats_from_report() -> None:
    analysis = OpportunityAnalysis(
        opportunity_id="2026-06-29:AZI",
        symbol="AZI",
        scanner_hits=2,
        bars=4,
        news_count=1,
        float_shares=8_000_000,
        short_percent=0.2,
        float_ok=True,
        has_news=True,
        bull_flag=True,
        triggered=True,
        entry=6.15,
        stop=5.6,
        max_r=2.7,
        mae_r=0.3,
        stopped_out=False,
    )
    report = EodReport(_DAY, [analysis], {"opportunities": 1, "triggered": 1}, "md")
    stats = build_stats(report, _NOW)

    assert stats["generated_utc"] == _NOW.isoformat()
    assert stats["trading_date"] == "2026-06-29"
    assert stats["aggregates"]["opportunities"] == 1
    assert stats["opportunities"][0]["symbol"] == "AZI"
    assert stats["opportunities"][0]["max_r"] == 2.7
    assert stats["opportunities"][0]["trading_date"] == _DAY  # analysis_records stamps it


def _settings() -> Settings:
    return Settings(_env_file=None)  # type: ignore[call-arg]


def test_build_charts_shape(tmp_path: Path) -> None:
    store = Store(tmp_path)
    _seed(store)
    payload = build_charts(store, _settings(), _DAY, _NOW)

    assert payload["generated_utc"] == _NOW.isoformat()
    assert payload["trading_date"] == "2026-06-29"
    # Only AZI has bars; DUD (no bars) is skipped so the front-end only gets drawable series.
    assert [c["symbol"] for c in payload["charts"]] == ["AZI"]
    azi = payload["charts"][0]
    assert azi["opportunity_id"] == "2026-06-29:AZI"
    assert azi["run"] == 1 and azi["run_count"] == 1
    assert len(azi["bars"]) == 2  # dup bar row collapsed on read
    assert azi["bars"][0]["t"] == int(_TS1.timestamp())
    assert set(azi["markers"]) == {"first_hit", "entry", "max_r", "stop"}


def test_build_charts_empty_store(tmp_path: Path) -> None:
    payload = build_charts(Store(tmp_path), _settings(), _DAY, _NOW)
    assert payload["charts"] == []


def test_write_json_atomic_and_valid(tmp_path: Path) -> None:
    store = Store(tmp_path)
    _seed(store)
    out = tmp_path / "dashboard" / "status.json"
    write_json(out, build_status(store, _inputs()))

    assert out.exists()
    assert not (tmp_path / "dashboard" / "status.json.tmp").exists()  # tmp cleaned up
    loaded = json.loads(out.read_text())
    assert loaded["service"]["deployed_commit"] == "abc1234"


def test_write_json_if_changed_creates_missing(tmp_path: Path) -> None:
    out = tmp_path / "dashboard" / "stats.json"
    assert write_json_if_changed(out, {"generated_utc": "t0", "trading_date": "2026-06-29"}) is True
    assert json.loads(out.read_text())["trading_date"] == "2026-06-29"


def test_write_json_if_changed_skips_when_only_timestamp_differs(tmp_path: Path) -> None:
    out = tmp_path / "dashboard" / "stats.json"
    write_json(out, {"generated_utc": "t0", "trading_date": "2026-06-29", "n": 1})
    # Same content, newer stamp -> no write, so the on-disk stamp is preserved (front-end won't
    # see a new generated_utc and redraw / reset the chart).
    assert (
        write_json_if_changed(out, {"generated_utc": "t1", "trading_date": "2026-06-29", "n": 1})
        is False
    )
    assert json.loads(out.read_text())["generated_utc"] == "t0"


def test_write_json_if_changed_writes_when_content_differs(tmp_path: Path) -> None:
    out = tmp_path / "dashboard" / "stats.json"
    write_json(out, {"generated_utc": "t0", "trading_date": "2026-06-29", "n": 1})
    assert (
        write_json_if_changed(out, {"generated_utc": "t1", "trading_date": "2026-06-30", "n": 2})
        is True
    )
    loaded = json.loads(out.read_text())
    assert loaded["trading_date"] == "2026-06-30" and loaded["generated_utc"] == "t1"


def test_write_json_if_changed_ignores_generated_utc_across_real_payloads(tmp_path: Path) -> None:
    # A real stats payload (dates/datetimes serialised via the default hook) must diff correctly:
    # rebuilding it with only a fresh generated_utc is a no-op write.
    store = Store(tmp_path)
    _seed(store)
    payload = build_charts(store, _settings(), _DAY, _NOW)
    out = tmp_path / "dashboard" / "charts.json"
    assert write_json_if_changed(out, payload) is True
    later = build_charts(store, _settings(), _DAY, datetime(2026, 6, 29, 21, 0, tzinfo=UTC))
    assert write_json_if_changed(out, later) is False
