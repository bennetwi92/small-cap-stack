"""The ground-truth loaders behind spike #428's out-of-sample validation.

Spike code is exempt from mypy and carries no tests by default (CLAUDE.md), and most of it should
stay that way. These two loaders are the exception, because the validation's headline numbers are
only as good as the appearance times they read:

* :func:`load_dashboard_cases` reads the published dashboard payload, whose appearance marker is
  **floored to its 5-min bar**. Whether that floor is modelled or silently treated as exact is the
  difference between a defensible appearance delta and one biased by ~3 minutes, so the quantum and
  the mid-quantum gating instant are pinned here rather than trusted.
* :func:`load_export_cases` reads a ``data-export`` parquet slice. The 2026-08-04 session could not
  dispatch that Action (its proxy blocks ``/actions/*``), so this is the one thing standing between
  that path and untested dead code.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import polars as pl
import pytest

from tests.spike_import import load_spike

recon = load_spike("scanner_reconstruct")


def _bar(t: datetime, close: float = 2.0, volume: float = 1000.0) -> dict[str, object]:
    return {"t": int(t.timestamp()), "o": close, "h": close, "l": close, "c": close, "v": volume}


def _write_charts(tmp_path: Path, day: str, marker: datetime, bar0: datetime) -> Path:
    charts = tmp_path / "charts"
    charts.mkdir()
    (charts / f"{day}.json").write_text(
        json.dumps(
            {
                "trading_date": day,
                "charts": [
                    {
                        "opportunity_id": f"{day}:AAA",
                        "symbol": "AAA",
                        "run": 1,
                        "markers": {"first_hit": int(marker.timestamp())},
                        "bars": [_bar(bar0 + timedelta(minutes=5 * i)) for i in range(4)],
                    },
                    {  # a re-entry: same symbol-day, must NOT become a second case
                        "opportunity_id": f"{day}:AAA#2",
                        "symbol": "AAA",
                        "run": 2,
                        "markers": {"first_hit": int((marker + timedelta(hours=1)).timestamp())},
                        "bars": [_bar(bar0 + timedelta(minutes=5 * i)) for i in range(4)],
                    },
                ],
            }
        )
    )
    return charts


def test_dashboard_marker_is_treated_as_a_floor_not_an_exact_time(tmp_path: Path) -> None:
    marker = datetime(2026, 7, 30, 12, 0, tzinfo=UTC)
    charts = _write_charts(tmp_path, "2026-07-30", marker, marker)
    (case,) = recon.load_dashboard_cases(charts)

    assert case.hit_quantum_sec == recon.DASHBOARD_HIT_QUANTUM_SEC == 300
    assert case.first_hit == case.hit_lo == marker
    assert case.hit_hi == marker + timedelta(seconds=300)
    # Strictly inside the bar: the appearance can never BE the bar start (the scanner samples on a
    # 60s cadence within it), and handing `detect_day` the start would let a bar that opened at the
    # marker count as takeable when live it could not have been.
    assert case.hit_lo < case.gating_hit < case.hit_hi


def test_dashboard_keeps_only_the_first_run_of_a_symbol_day(tmp_path: Path) -> None:
    marker = datetime(2026, 7, 30, 12, 0, tzinfo=UTC)
    cases = recon.load_dashboard_cases(_write_charts(tmp_path, "2026-07-30", marker, marker))
    assert [(c.symbol, c.trading_date) for c in cases] == [("AAA", date(2026, 7, 30))]


def test_dashboard_stats_upgrades_only_its_own_date_to_exact(tmp_path: Path) -> None:
    """`stats.json` covers ONE trading date; a symbol that also ran on another day must not
    inherit that day's appearance. Keying the lookup on symbol alone silently did exactly that."""
    marker = datetime(2026, 7, 30, 12, 0, tzinfo=UTC)
    charts = _write_charts(tmp_path, "2026-07-30", marker, marker)
    other = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)
    (charts / "2026-08-03.json").write_text(
        json.dumps(
            {
                "trading_date": "2026-08-03",
                "charts": [
                    {
                        "symbol": "AAA",
                        "run": 1,
                        "markers": {"first_hit": int(other.timestamp())},
                        "bars": [_bar(other)],
                    }
                ],
            }
        )
    )
    precise = other + timedelta(seconds=178)
    stats = tmp_path / "stats.json"
    stats.write_text(
        json.dumps(
            {
                "opportunities": [
                    {
                        "symbol": "AAA",
                        "run": 1,
                        "trading_date": "2026-08-03",
                        "first_hit": precise.isoformat(),
                    }
                ]
            }
        )
    )
    by_date = {c.trading_date: c for c in recon.load_dashboard_cases(charts, stats=stats)}

    assert by_date[date(2026, 8, 3)].hit_quantum_sec == 0
    assert by_date[date(2026, 8, 3)].first_hit == precise
    assert by_date[date(2026, 8, 3)].gating_hit == precise  # exact needs no mid-quantum nudge
    assert by_date[date(2026, 7, 30)].hit_quantum_sec == 300  # untouched by another day's stats
    assert by_date[date(2026, 7, 30)].first_hit == marker


def test_dashboard_dates_filter(tmp_path: Path) -> None:
    marker = datetime(2026, 7, 30, 12, 0, tzinfo=UTC)
    charts = _write_charts(tmp_path, "2026-07-30", marker, marker)
    assert recon.load_dashboard_cases(charts, dates=[date(2026, 8, 3)]) == []
    assert len(recon.load_dashboard_cases(charts, dates=[date(2026, 7, 30)])) == 1


def test_export_cases_take_the_first_scanner_hit_and_stay_exact(tmp_path: Path) -> None:
    """The export path mirrors `load_store_cases`: the appearance is the first `scanner_hits`
    row, at full precision, so unlike the dashboard path it carries no quantum."""
    day, oid = date(2026, 7, 30), "2026-07-30:AAA"
    first = datetime(2026, 7, 30, 12, 0, 58, tzinfo=UTC)
    pl.DataFrame({"opportunity_id": [oid], "symbol": ["AAA"], "trading_date": [day]}).write_parquet(
        tmp_path / "opportunities_1.parquet"
    )
    pl.DataFrame(
        # deliberately out of order: the FIRST hit is the appearance, not the first row
        {"opportunity_id": [oid, oid], "ts_utc": [first + timedelta(minutes=5), first]}
    ).write_parquet(tmp_path / "scanner_hits_1.parquet")
    starts = [datetime(2026, 7, 30, 12, 0, tzinfo=UTC) + timedelta(minutes=5 * i) for i in range(3)]
    pl.DataFrame(
        {
            "opportunity_id": [oid] * 4,
            # a duplicate row, as the raw store really holds after a restart
            "bar_start_utc": [starts[2], starts[0], starts[1], starts[0]],
            "open": [1.0, 2.0, 3.0, 9.0],
            "high": [1.0, 2.0, 3.0, 9.0],
            "low": [1.0, 2.0, 3.0, 9.0],
            "close": [1.0, 2.0, 3.0, 9.0],
            "volume": [10.0, 20.0, 30.0, 99.0],
        }
    ).write_parquet(tmp_path / "bars_1.parquet")

    (case,) = recon.load_export_cases(tmp_path)
    assert case.symbol == "AAA"
    assert case.trading_date == day
    assert case.first_hit == first
    assert case.hit_quantum_sec == 0
    assert [b.start for b in case.bars] == starts  # deduped and sorted
    assert [b.open for b in case.bars] == [2.0, 3.0, 1.0]  # dupe resolved keep-first


def test_export_cases_reports_a_missing_dataset(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="opportunities"):
        recon.load_export_cases(tmp_path)


def test_fixture_cases_remain_the_exact_regression_baseline() -> None:
    """The 25 committed review cases are the baseline the out-of-sample run is compared against —
    they must keep loading, and must stay exact-precision."""
    cases = recon.load_fixture_cases()
    assert len(cases) == 25
    assert all(c.hit_quantum_sec == 0 for c in cases)
    assert all(c.gating_hit == c.first_hit for c in cases)
