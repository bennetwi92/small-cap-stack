"""Tests for the dashboard state exporter (#68)."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from small_cap_stack.config import Settings
from small_cap_stack.dashboard import (
    StatusInputs,
    build_charts,
    build_index,
    build_stats,
    build_status,
    charts_path,
    read_json,
    upsert_index_date,
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


def test_build_status_total_is_cross_history_today_is_scoped(tmp_path: Path) -> None:
    # The count query (#246) must keep `total` cross-history while `today` stays scoped to the
    # trading-date prefix — scoping the whole read to one dt= partition would corrupt `total`.
    store = Store(tmp_path)
    _seed(store)  # a 2026-06-29 day
    prior = date(2026, 6, 26)
    store.append(
        "bars",
        [
            {
                "opportunity_id": "2026-06-26:OLD",
                "symbol": "OLD",
                "bar_start_utc": datetime(2026, 6, 26, 13, 0, tzinfo=UTC),
                "open": 1.0,
                "high": 2.0,
                "low": 0.5,
                "close": 1.5,
                "volume": 1e3,
            }
        ],
        partition_date=prior,
    )
    data = build_status(store, _inputs())["data"]  # trading_date is 2026-06-29
    assert data["bars"] == {"today": 2, "total": 3}  # today's 2 distinct bars, 3 across history


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
    # Review context (#109): per-source float + headline text ride along with the chart.
    assert azi["floats"] == [{"source": "yfinance", "float": 8_000_000}]
    assert azi["news"] == [{"ts": None, "provider": "DJ-N", "headline": "h"}]


def test_build_charts_empty_store(tmp_path: Path) -> None:
    payload = build_charts(Store(tmp_path), _settings(), _DAY, _NOW)
    assert payload["charts"] == []


def _run_bar(
    oid: str, i: int, o: float, h: float, low: float, c: float, vol: float = 1000.0
) -> dict:  # type: ignore[type-arg]
    return {
        "opportunity_id": oid,
        "symbol": "RUN",
        "bar_start_utc": _TS1 + timedelta(minutes=5 * i),
        "open": o,
        "high": h,
        "low": low,
        "close": c,
        "volume": vol,
    }


def _run_flag(oid: str, base: int) -> list:  # type: ignore[type-arg]
    return [
        _run_bar(oid, base + 0, 5.0, 5.8, 4.6, 5.7),
        _run_bar(oid, base + 1, 5.7, 6.5, 5.6, 6.4, vol=2000),
        _run_bar(oid, base + 2, 6.4, 6.1, 5.6, 5.7),
        _run_bar(oid, base + 3, 5.7, 7.0, 5.7, 6.9),
    ]


def test_build_charts_shares_float_and_news_across_runs(tmp_path: Path) -> None:
    # News & fundamentals are captured per symbol/day, not per run — a re-entry's two runs must both
    # carry the same float sources and headlines (#109).
    store = Store(tmp_path)
    oid = "2026-06-29:RUN"
    store.append(
        "opportunities",
        [
            {
                "opportunity_id": oid,
                "symbol": "RUN",
                "con_id": 3,
                "trading_date": _DAY,
                "first_seen_utc": _TS1,
                "first_rank": 0,
            }
        ],
        partition_date=_DAY,
    )
    # 85-min hit gap => two runs (mirrors the report's re-entry segmentation).
    store.append(
        "scanner_hits",
        [
            {
                "opportunity_id": oid,
                "symbol": "RUN",
                "ts_utc": _TS1 + timedelta(minutes=m),
                "rank": 0,
            }
            for m in (0, 5, 90, 95)
        ],
        partition_date=_DAY,
    )
    store.append("bars", _run_flag(oid, 0) + _run_flag(oid, 18), partition_date=_DAY)
    store.append(
        "news",
        [
            {
                "opportunity_id": oid,
                "symbol": "RUN",
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
                "opportunity_id": oid,
                "symbol": "RUN",
                "ts_utc": _TS1,
                "float_shares": 9_000_000,
                "shares_outstanding": 12_000_000,
                "short_percent": 0.2,
                "source": "yfinance",
            },
            {
                "opportunity_id": oid,
                "symbol": "RUN",
                "ts_utc": _TS1,
                "float_shares": 8_000_000,
                "shares_outstanding": None,
                "short_percent": None,
                "source": "fmp",
            },
        ],
        partition_date=_DAY,
    )

    payload = build_charts(store, _settings(), _DAY, _NOW)
    runs = [c for c in payload["charts"] if c["symbol"] == "RUN"]
    assert [c["opportunity_id"] for c in runs] == ["2026-06-29:RUN#1", "2026-06-29:RUN#2"]
    expected_floats = [
        {"source": "fmp", "float": 8_000_000},  # fmp leads (priority) despite being stored second
        {"source": "yfinance", "float": 9_000_000},
    ]
    for c in runs:
        assert c["floats"] == expected_floats
        assert c["news"] == [{"ts": None, "provider": "DJ-N", "headline": "h"}]


# Full-day slicing (#141): the chart renders the whole trading day (04:00–16:00 ET), not the run
# window the analysis measures. Times are UTC; ET is UTC-4 in summer, so 12:00Z = 08:00 ET.
_FD_DAY = date(2026, 6, 29)
_FD_HIT = datetime(2026, 6, 29, 14, 0, tzinfo=UTC)  # scanner hit 10:00 ET


def _seed_full_day(store: Store) -> None:
    store.append(
        "opportunities",
        [
            {
                "opportunity_id": "2026-06-29:FDY",
                "symbol": "FDY",
                "con_id": 9,
                "trading_date": _FD_DAY,
                "first_seen_utc": _FD_HIT,
                "first_rank": 0,
            }
        ],
        partition_date=_FD_DAY,
    )
    store.append(
        "scanner_hits",
        [{"opportunity_id": "2026-06-29:FDY", "symbol": "FDY", "ts_utc": _FD_HIT, "rank": 0}],
        partition_date=_FD_DAY,
    )
    # Two early bars (08:00 / 09:00 ET) sit *before* the run's lookback window (13:30Z), plus a
    # 04:00 pre-market bar (08:00Z), one pre-open bar at 03:59 ET that must be excluded, and the
    # run bars at 10:00/10:05 ET.
    times = [
        datetime(2026, 6, 29, 7, 59, tzinfo=UTC),  # 03:59 ET — before chart_start, excluded
        datetime(2026, 6, 29, 8, 0, tzinfo=UTC),  # 04:00 ET — first bar of the day
        datetime(2026, 6, 29, 12, 0, tzinfo=UTC),  # 08:00 ET
        datetime(2026, 6, 29, 13, 0, tzinfo=UTC),  # 09:00 ET
        _FD_HIT,  # 10:00 ET (run bar)
        datetime(2026, 6, 29, 14, 5, tzinfo=UTC),  # 10:05 ET (run bar)
    ]
    store.append(
        "bars",
        [
            {
                "opportunity_id": "2026-06-29:FDY",
                "symbol": "FDY",
                "bar_start_utc": t,
                "open": 5.0,
                "high": 6.0,
                "low": 4.9,
                "close": 5.5,
                "volume": 1e3,
            }
            for t in times
        ],
        partition_date=_FD_DAY,
    )


def test_build_charts_renders_full_day_not_run_window(tmp_path: Path) -> None:
    store = Store(tmp_path)
    _seed_full_day(store)
    payload = build_charts(store, _settings(), _FD_DAY, _NOW)

    chart = payload["charts"][0]
    ts = [b["t"] for b in chart["bars"]]
    # The 03:59 ET bar is dropped (before chart_start); the 04:00 ET bar and both early bars — which
    # fall outside the run's [hit-30m, ...) window — are all present alongside the run bars.
    assert int(datetime(2026, 6, 29, 7, 59, tzinfo=UTC).timestamp()) not in ts
    assert int(datetime(2026, 6, 29, 8, 0, tzinfo=UTC).timestamp()) == ts[0]
    assert int(datetime(2026, 6, 29, 12, 0, tzinfo=UTC).timestamp()) in ts
    assert len(ts) == 5  # all but the excluded pre-04:00 bar
    assert ts == sorted(ts)


def _charts_payload() -> dict:
    return {
        "generated_utc": "t",
        "trading_date": "2026-07-01",
        "charts": [
            {
                "opportunity_id": "2026-07-01:AHMA",
                "symbol": "AHMA",
                "run": 1,
                "run_count": 2,
                "bars": [],
                "levels": {},
                "markers": {},
                "triggered": True,
                "stopped_out": False,
                "max_r": 2.3,
            },
            {
                "opportunity_id": "2026-07-01:AHMA#2",
                "symbol": "AHMA",
                "run": 2,
                "run_count": 2,
                "bars": [],
                "levels": {},
                "markers": {},
                "triggered": False,
                "stopped_out": False,
                "max_r": None,
            },
        ],
    }


def test_build_index_projects_and_sorts_dates_newest_first() -> None:
    older = {"generated_utc": "t", "trading_date": "2026-06-30", "charts": []}
    idx = build_index([(date(2026, 6, 30), older), (date(2026, 7, 1), _charts_payload())], _NOW)

    assert idx["generated_utc"] == _NOW.isoformat()
    assert [d["date"] for d in idx["dates"]] == ["2026-07-01", "2026-06-30"]  # newest first
    opps = idx["dates"][0]["opportunities"]
    assert opps[0] == {
        "opportunity_id": "2026-07-01:AHMA",
        "symbol": "AHMA",
        "run": 1,
        "run_count": 2,
        "triggered": True,
        "max_r": 2.3,
    }
    assert opps[1]["run"] == 2 and opps[1]["max_r"] is None
    assert idx["dates"][1]["opportunities"] == []


def test_upsert_index_date_replaces_and_reorders() -> None:
    base = build_index([(date(2026, 6, 30), {"charts": []})], _NOW)
    updated = upsert_index_date(base, date(2026, 7, 1), _charts_payload(), _NOW)
    assert [d["date"] for d in updated["dates"]] == ["2026-07-01", "2026-06-30"]

    # Re-upserting the same date replaces (not duplicates) its entry.
    again = upsert_index_date(updated, date(2026, 7, 1), {"charts": []}, _NOW)
    dates = [d["date"] for d in again["dates"]]
    assert dates == ["2026-07-01", "2026-06-30"] and dates.count("2026-07-01") == 1
    assert again["dates"][0]["opportunities"] == []


def test_upsert_index_date_from_missing_index() -> None:
    idx = upsert_index_date(None, date(2026, 7, 1), _charts_payload(), _NOW)
    assert [d["date"] for d in idx["dates"]] == ["2026-07-01"]
    assert len(idx["dates"][0]["opportunities"]) == 2


def test_charts_path_and_read_json_roundtrip(tmp_path: Path) -> None:
    out = tmp_path / "dashboard"
    p = charts_path(out, date(2026, 7, 1))
    assert p == out / "charts" / "2026-07-01.json"

    assert read_json(p) is None  # missing file
    write_json(p, _charts_payload())
    assert read_json(p)["trading_date"] == "2026-07-01"

    bad = out / "bad.json"
    bad.write_text("{not json")
    assert read_json(bad) is None


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
