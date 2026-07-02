"""Dashboard state exporter (#68): project the box's live + stored state to JSON.

Phase-1 dashboard **data producer**. Pure, store-backed projections (same pattern as report.py)
serialised to ``data_dir/dashboard/`` for an outbound publisher (#69) and a GitHub Pages frontend
(#70) to consume. No secrets are ever included. Writes are atomic so a consumer never reads a
half-written file.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import polars as pl

from .charts import build_opportunity_chart
from .config import Settings
from .report import EodReport, analysis_records, day_opportunities, symbol_runs
from .storage import Store

# (dataset, distinct-subset used for a meaningful count | None = raw rows are all distinct events)
_DATASET_COUNTS: tuple[tuple[str, list[str] | None], ...] = (
    ("opportunities", ["opportunity_id"]),
    ("scanner_hits", None),  # each row is a genuine per-tick appearance
    ("bars", ["opportunity_id", "bar_start_utc"]),  # raw store may hold duplicate bar rows
    ("news", ["opportunity_id", "article_id"]),
    ("fundamentals", ["opportunity_id"]),
)


@dataclass(frozen=True)
class StatusInputs:
    """The live (non-stored) runtime bits the app injects, kept plain so projections stay pure."""

    now: datetime
    trading_date: date
    connected: bool
    trading_mode: str
    in_scan_window: bool
    deployed_commit: str | None
    scan_ticks_total: int
    jobs: list[tuple[str, datetime | None]]  # (job_id, next_run_utc)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None


def _json_default(o: Any) -> str:
    if isinstance(o, (date, datetime)):
        return o.isoformat()
    raise TypeError(f"not JSON-serialisable: {type(o)!r}")


def _prefix(trading_date: date) -> str:
    return f"{trading_date.isoformat()}:"  # opportunity_id = "<date>:<SYMBOL>"


def _latest_candidates(
    store: Store, trading_date: date
) -> tuple[list[dict[str, Any]], datetime | None]:
    scans = store.read("scanner_hits")
    if scans.is_empty():
        return [], None
    today = scans.filter(pl.col("opportunity_id").str.starts_with(_prefix(trading_date)))
    if today.is_empty():
        return [], None
    last_ts = today.select(pl.col("ts_utc").max()).item()
    rows = today.filter(pl.col("ts_utc") == last_ts).sort("rank")
    cands = [{"symbol": r["symbol"], "rank": int(r["rank"])} for r in rows.iter_rows(named=True)]
    return cands, last_ts


def _count(df: pl.DataFrame, prefix: str, distinct: list[str] | None) -> dict[str, int]:
    if df.is_empty():
        return {"today": 0, "total": 0}
    base = df.unique(subset=distinct) if distinct else df
    today = base.filter(pl.col("opportunity_id").str.starts_with(prefix))
    return {"today": today.height, "total": base.height}


def _open_opportunities(store: Store, trading_date: date) -> dict[str, Any]:
    opps = store.read("opportunities")
    if opps.is_empty():
        return {"open_today": 0, "symbols": []}
    today = opps.filter(pl.col("trading_date") == trading_date).unique(subset="opportunity_id")
    symbols = sorted(today["symbol"].to_list()) if not today.is_empty() else []
    return {"open_today": len(symbols), "symbols": symbols}


def build_status(store: Store, s: StatusInputs) -> dict[str, Any]:
    """Frequent snapshot: service health, scanner activity, open opportunities, data counts."""
    candidates, last_scan = _latest_candidates(store, s.trading_date)
    prefix = _prefix(s.trading_date)
    data = {name: _count(store.read(name), prefix, distinct) for name, distinct in _DATASET_COUNTS}
    return {
        "generated_utc": s.now.isoformat(),
        "trading_date": s.trading_date.isoformat(),
        "service": {
            "connected": s.connected,
            "trading_mode": s.trading_mode,
            "in_scan_window": s.in_scan_window,
            "deployed_commit": s.deployed_commit,
            "jobs": [{"id": jid, "next_run_utc": _iso(nxt)} for jid, nxt in s.jobs],
        },
        "scanner": {
            "last_scan_utc": _iso(last_scan),
            "scan_ticks_total": s.scan_ticks_total,
            "latest_candidates": candidates,
        },
        "opportunities": _open_opportunities(store, s.trading_date),
        "data": data,
    }


def build_stats(report: EodReport, now: datetime) -> dict[str, Any]:
    """Daily snapshot: the EOD aggregates + per-opportunity analysis rows."""
    return {
        "generated_utc": now.isoformat(),
        "trading_date": report.trading_date.isoformat(),
        "aggregates": report.aggregates,
        "opportunities": analysis_records(report),
    }


def build_charts(
    store: Store, settings: Settings, trading_date: date, now: datetime
) -> dict[str, Any]:
    """Per-opportunity annotated candlestick payloads for the dashboard (#113).

    Reuses the report's run segmentation (``symbol_runs``) so charts are drawn over exactly the same
    per-run bar windows the analysis measures — one source of truth. Runs with no bars (e.g. bars
    not yet captured) are skipped so the front-end only ever gets drawable series.
    """
    opps = day_opportunities(store, trading_date)
    charts: list[dict[str, Any]] = []
    if not opps.is_empty():
        bars = store.read("bars")
        scans = store.read("scanner_hits")
        for row in opps.iter_rows(named=True):
            for run in symbol_runs(row, bars, scans, settings):
                if not run.chart_bars:
                    continue
                # Draw over chart_bars, not the disjoint analysis window: an open trade is followed
                # past a later run's start to its real close, so the chart doesn't cut off (#36).
                cd = build_opportunity_chart(run.chart_bars, settings, first_hit=run.first_hit)
                charts.append(
                    {
                        "opportunity_id": run.seg_id,
                        "symbol": run.symbol,
                        "run": run.idx,
                        "run_count": run.run_count,
                        **asdict(cd),
                    }
                )
    return {
        "generated_utc": now.isoformat(),
        "trading_date": trading_date.isoformat(),
        "charts": charts,
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    """Serialise atomically (tmp file + os.replace) so a consumer never sees a partial write."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, default=_json_default, indent=2))
    os.replace(tmp, path)
