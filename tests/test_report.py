"""Integration test for the EOD report (#19) over a populated Store."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from small_cap_stack.bullflag import detect_with_settings
from small_cap_stack.capture import Bar
from small_cap_stack.config import Settings
from small_cap_stack.report import (
    OpportunityAnalysis,
    _segment_runs,
    _to_markdown,
    build_eod_report,
)
from small_cap_stack.storage import Store

_DAY = date(2026, 6, 29)
_T0 = datetime(2026, 6, 29, 14, 0, tzinfo=UTC)


def _settings() -> Settings:
    return Settings(_env_file=None)  # type: ignore[call-arg]


def _bar_row(oid: str, sym: str, i: int, o: float, h: float, low: float, c: float) -> dict:  # type: ignore[type-arg]
    return {
        "opportunity_id": oid,
        "symbol": sym,
        "bar_start_utc": _T0 + timedelta(minutes=5 * i),
        "open": o,
        "high": h,
        "low": low,
        "close": c,
        "volume": 1000.0,
    }


def _seed(store: Store) -> None:
    # AZI: a clean bull flag that triggers and runs to ~3R.
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
            },
            {
                "opportunity_id": "2026-06-29:DUD",
                "symbol": "DUD",
                "con_id": 2,
                "trading_date": _DAY,
                "first_seen_utc": _T0,
                "first_rank": 1,
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
            },
        ],
        partition_date=_DAY,
    )
    store.append(
        "fundamentals",
        [
            {
                "opportunity_id": "2026-06-29:AZI",
                "symbol": "AZI",
                "ts_utc": _T0,
                "float_shares": 8_000_000,
                "shares_outstanding": 12_000_000,
                "short_percent": 0.2,
                "source": "yfinance",
            },
        ],
        partition_date=_DAY,
    )
    store.append(
        "bars",
        [
            _bar_row("2026-06-29:AZI", "AZI", 0, 5.0, 6.2, 4.9, 6.0),  # pole (green)
            _bar_row("2026-06-29:AZI", "AZI", 1, 6.0, 6.1, 5.6, 5.7),  # flag (red)
            _bar_row("2026-06-29:AZI", "AZI", 2, 5.7, 7.0, 5.7, 6.9),  # trigger + run
            _bar_row("2026-06-29:AZI", "AZI", 3, 6.9, 7.64, 6.8, 7.5),  # Max R ~3
            # DUD: no setup (all red), no news, no fundamentals
            _bar_row("2026-06-29:DUD", "DUD", 0, 6.0, 6.1, 5.9, 5.95),
            _bar_row("2026-06-29:DUD", "DUD", 1, 5.95, 6.0, 5.8, 5.85),
        ],
        partition_date=_DAY,
    )
    store.append(
        "scanner_hits",
        [
            {"opportunity_id": "2026-06-29:AZI", "symbol": "AZI", "ts_utc": _T0, "rank": 0},
            {"opportunity_id": "2026-06-29:AZI", "symbol": "AZI", "ts_utc": _T0, "rank": 0},
            {"opportunity_id": "2026-06-29:DUD", "symbol": "DUD", "ts_utc": _T0, "rank": 1},
        ],
        partition_date=_DAY,
    )


def test_eod_report(tmp_path: Path) -> None:
    store = Store(tmp_path)
    _seed(store)
    report = build_eod_report(store, _settings(), _DAY)

    assert report.aggregates["opportunities"] == 2
    assert report.aggregates["with_news"] == 1
    assert report.aggregates["float_ok"] == 1
    assert report.aggregates["bull_flag"] == 1
    assert report.aggregates["triggered"] == 1
    assert report.aggregates["reached_2r"] == 1

    by_sym = {a.symbol: a for a in report.analyses}
    azi = by_sym["AZI"]
    assert azi.triggered and azi.max_r is not None and azi.max_r >= 2.0
    assert azi.float_shares == 8_000_000 and azi.float_ok is True
    assert azi.scanner_hits == 2
    assert azi.flag_len == 1 and azi.retracement is not None  # traded setup's shape (#98)
    dud = by_sym["DUD"]
    assert not dud.triggered and not dud.bull_flag and dud.float_shares is None

    assert "EOD report" in report.markdown and "AZI" in report.markdown


def test_duplicate_raw_rows_are_deduped_on_read(tmp_path: Path) -> None:
    # Seed twice (simulating a mid-day restart re-opening names + re-fetching bars/news, so every
    # raw row is duplicated). The report must dedup opportunities/bars/news on read and stay exact.
    store = Store(tmp_path)
    _seed(store)
    _seed(store)

    report = build_eod_report(store, _settings(), _DAY)
    assert report.aggregates["opportunities"] == 2  # AZI + DUD, not 4
    azi = {a.symbol: a for a in report.analyses}["AZI"]
    assert azi.bars == 4  # deduped from 8 raw rows
    assert azi.news_count == 1  # single article, not double-counted
    assert azi.triggered and azi.max_r is not None and azi.max_r >= 2.0


def test_analysis_excludes_after_hours_bars(tmp_path: Path) -> None:
    # Bars at/after the 16:00 ET regular close (capture_end) must not enter the analysis, even
    # though they're stored raw (#93). _T0 (14:00 UTC) is 10:00 ET, so i=78 (+390min) = 16:30 ET.
    store = Store(tmp_path)
    oid = "2026-06-29:AH"
    store.append(
        "opportunities",
        [
            {
                "opportunity_id": oid,
                "symbol": "AH",
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
        [{"opportunity_id": oid, "symbol": "AH", "ts_utc": _T0, "rank": 0}],
        partition_date=_DAY,
    )
    store.append(
        "bars",
        [
            _bar_row(oid, "AH", 0, 5.0, 6.2, 4.9, 6.0),  # pole
            _bar_row(oid, "AH", 1, 6.0, 6.1, 5.6, 5.7),  # flag
            _bar_row(oid, "AH", 2, 5.7, 6.3, 5.7, 6.2),  # trigger (modest high 6.3)
            _bar_row(oid, "AH", 78, 6.2, 9.9, 6.2, 9.8),  # 16:30 ET after-hours spike — excluded
        ],
        partition_date=_DAY,
    )

    ah = build_eod_report(store, _settings(), _DAY).analyses[0]
    assert ah.bars == 3  # after-hours bar dropped from the analysis
    assert ah.triggered and ah.max_r is not None and ah.max_r < 1.0  # not the 9.9 spike (~6.8R)


def test_eod_report_empty(tmp_path: Path) -> None:
    report = build_eod_report(Store(tmp_path), _settings(), _DAY)
    assert report.aggregates["opportunities"] == 0
    assert "No opportunities" in report.markdown


def _analysis(sym: str, max_r: float | None) -> OpportunityAnalysis:
    return OpportunityAnalysis(
        opportunity_id=sym,
        symbol=sym,
        scanner_hits=1,
        bars=4,
        news_count=0,
        float_shares=None,
        short_percent=None,
        float_ok=None,
        has_news=False,
        bull_flag=True,
        triggered=max_r is not None,
        entry=6.15,
        stop=5.6,
        max_r=max_r,
        mae_r=0.1,
        stopped_out=(max_r == 0.0),
    )


def test_markdown_sort_keeps_zero_max_r_above_untriggered() -> None:
    # A triggered same-bar stop-out has max_r == 0.0 (a real value) and must sort ABOVE an
    # untriggered (max_r None) row, even when None is listed first (regression for `max_r or ...`).
    agg = dict.fromkeys(
        (
            "opportunities",
            "with_news",
            "with_recent_news",
            "float_ok",
            "bull_flag",
            "triggered",
            "reached_1r",
            "reached_2r",
            "reached_3r",
        ),
        0,
    )
    md = _to_markdown(
        _DAY, [_analysis("NONE", None), _analysis("ZERO", 0.0), _analysis("HALF", 0.5)], agg
    )
    order = [
        ln.split("|")[1].strip()
        for ln in md.splitlines()
        if ln.startswith("| ") and "name" not in ln
    ]
    assert order == ["HALF", "ZERO", "NONE"]


def test_segment_runs_gap_rule() -> None:
    def t(m: int) -> datetime:
        return _T0 + timedelta(minutes=m)

    assert _segment_runs([], 60) == []
    assert _segment_runs([t(0), t(1), t(2)], 60) == [t(0)]  # continuous -> one run
    assert _segment_runs([t(0), t(40)], 60) == [t(0)]  # <60min gap -> same run
    assert _segment_runs([t(0), t(60)], 60) == [t(0), t(60)]  # exactly 60 -> new run (>=)
    assert _segment_runs([t(0), t(5), t(90), t(95)], 60) == [t(0), t(90)]  # fade then re-pop


def _flag(oid: str, sym: str, base_i: int) -> list:  # type: ignore[type-arg]
    # pole (green) / flag (red) / trigger / run-up — a setup that triggers to ~ several R.
    return [
        _bar_row(oid, sym, base_i + 0, 5.0, 6.2, 4.9, 6.0),
        _bar_row(oid, sym, base_i + 1, 6.0, 6.1, 5.6, 5.7),
        _bar_row(oid, sym, base_i + 2, 5.7, 7.0, 5.7, 6.9),
        _bar_row(oid, sym, base_i + 3, 6.9, 7.64, 6.8, 7.5),
    ]


def test_reentry_segments_into_two_runs(tmp_path: Path) -> None:
    # RUN pops at 14:00 (bars i=0..3), fades, then pops again at 15:30 (bars i=18..21) — an
    # 85-min gap in scanner hits => two distinct opportunities, each analysed on its own bars.
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
                "first_seen_utc": _T0,
                "first_rank": 0,
            }
        ],
        partition_date=_DAY,
    )
    store.append(
        "scanner_hits",
        [
            {
                "opportunity_id": oid,
                "symbol": "RUN",
                "ts_utc": _T0 + timedelta(minutes=m),
                "rank": 0,
            }
            for m in (0, 5, 90, 95)
        ],
        partition_date=_DAY,
    )
    store.append("bars", _flag(oid, "RUN", 0) + _flag(oid, "RUN", 18), partition_date=_DAY)

    report = build_eod_report(store, _settings(), _DAY)
    assert report.aggregates["opportunities"] == 2  # segmented, not 1
    by_id = {a.opportunity_id: a for a in report.analyses}
    assert set(by_id) == {"2026-06-29:RUN#1", "2026-06-29:RUN#2"}
    r1, r2 = by_id["2026-06-29:RUN#1"], by_id["2026-06-29:RUN#2"]
    assert (r1.run, r1.run_count) == (1, 2) and (r2.run, r2.run_count) == (2, 2)
    assert r1.bars == 4 and r2.bars == 4  # each run sees only its own bars
    assert r1.triggered and r2.triggered  # each pop is a distinct entry
    assert r1.scanner_hits == 2 and r2.scanner_hits == 2


def test_bull_flag_true_when_setup_forms_then_breaks_out_midwindow(tmp_path: Path) -> None:
    # Regression for #112: bull_flag now comes from the R-metrics pass (rm.setup_found), which scans
    # every prefix — so a flag that forms then breaks out *before* the window ends still sets
    # bull_flag. A single end-of-window detect() would miss it: the last candle here is a green
    # breakout/run-up, not a trailing red flag, so detect() over the whole window returns None.
    store = Store(tmp_path)
    oid = "2026-06-29:MID"
    store.append(
        "opportunities",
        [
            {
                "opportunity_id": oid,
                "symbol": "MID",
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
        [{"opportunity_id": oid, "symbol": "MID", "ts_utc": _T0, "rank": 0}],
        partition_date=_DAY,
    )
    rows = _flag(oid, "MID", 0)  # pole / flag / trigger / run-up — ends on a green breakout candle
    store.append("bars", rows, partition_date=_DAY)

    # A naive end-of-window detect misses the setup (the window ends mid-run, not on a flag)...
    obars = [
        Bar(
            start=r["bar_start_utc"],
            open=r["open"],
            high=r["high"],
            low=r["low"],
            close=r["close"],
            volume=r["volume"],
        )
        for r in rows
    ]
    assert detect_with_settings(obars, _settings()) is None
    # ...but bull_flag is still True because the setup formed and triggered earlier in the window.
    mid = build_eod_report(store, _settings(), _DAY).analyses[0]
    assert mid.bull_flag is True and mid.triggered is True


def _news_row(oid: str, ts: datetime | None, aid: str) -> dict:  # type: ignore[type-arg]
    return {
        "opportunity_id": oid,
        "symbol": "RUN",
        "time": "raw",
        "ts_utc": ts,
        "provider": "DJ-N",
        "headline": aid,
        "article_id": aid,
    }


def test_news_attributed_to_run_by_publish_time(tmp_path: Path) -> None:
    # Two runs (pops at 14:00 and 15:30). A story dated into each run's window belongs to that run;
    # an undated (unparseable/legacy) story falls back to run 1 (#97).
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
                "first_seen_utc": _T0,
                "first_rank": 0,
            }
        ],
        partition_date=_DAY,
    )
    store.append(
        "scanner_hits",
        [
            {
                "opportunity_id": oid,
                "symbol": "RUN",
                "ts_utc": _T0 + timedelta(minutes=m),
                "rank": 0,
            }
            for m in (0, 5, 90, 95)
        ],
        partition_date=_DAY,
    )
    store.append("bars", _flag(oid, "RUN", 0) + _flag(oid, "RUN", 18), partition_date=_DAY)
    store.append(
        "news",
        [
            _news_row(oid, _T0 + timedelta(minutes=2), "early"),  # run 1 window
            _news_row(oid, _T0 + timedelta(minutes=92), "late"),  # run 2 window
            _news_row(oid, None, "undated"),  # falls back to run 1
        ],
        partition_date=_DAY,
    )

    by_id = {a.opportunity_id: a for a in build_eod_report(store, _settings(), _DAY).analyses}
    assert by_id["2026-06-29:RUN#1"].news_count == 2  # early + undated
    assert by_id["2026-06-29:RUN#2"].news_count == 1  # late only


def test_news_recent_flags_today_or_yesterday(tmp_path: Path) -> None:
    # A tighter recency signal than 7-day has_news (#101): today/yesterday (ET) is 'recent'; a
    # 5-day-old story still counts as has_news but is NOT recent.
    store = Store(tmp_path)
    cases = {"TODAY": _T0, "YEST": _T0 - timedelta(days=1), "OLD": _T0 - timedelta(days=5)}
    for sym in cases:
        oid = f"2026-06-29:{sym}"
        store.append(
            "opportunities",
            [
                {
                    "opportunity_id": oid,
                    "symbol": sym,
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
            [{"opportunity_id": oid, "symbol": sym, "ts_utc": _T0, "rank": 0}],
            partition_date=_DAY,
        )
    store.append(
        "news",
        [_news_row(f"2026-06-29:{sym}", ts, f"{sym}-story") for sym, ts in cases.items()],
        partition_date=_DAY,
    )

    report = build_eod_report(store, _settings(), _DAY)
    by_id = {a.opportunity_id: a for a in report.analyses}
    assert by_id["2026-06-29:TODAY"].news_recent is True
    assert by_id["2026-06-29:YEST"].news_recent is True
    assert by_id["2026-06-29:OLD"].news_recent is False  # 5-day-old story is not 'recent'
    assert report.aggregates["with_recent_news"] == 2  # TODAY + YEST, not OLD
