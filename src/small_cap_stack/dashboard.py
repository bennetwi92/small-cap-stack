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
from .report import (
    EodReport,
    analysis_records,
    day_chart_bars,
    day_opportunities,
    symbol_runs,
)
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
    """Per-opportunity annotated candlestick payloads for the dashboard (#113, full-day #141).

    Reuses the report's run segmentation (``symbol_runs``) to compute each run's R-metrics over the
    exact bar window the analysis measures — one source of truth — but draws the symbol's **full
    trading day** (``day_chart_bars``, 04:00–16:00 ET) so the review workbench can pan the whole
    session. Markers are timestamps into the run bars, which are a subset of the full day, so they
    still land on the right candles. Runs with no run-window bars (e.g. bars not yet captured) are
    skipped so the front-end only ever gets drawable series.
    """
    opps = day_opportunities(store, trading_date)
    charts: list[dict[str, Any]] = []
    if not opps.is_empty():
        bars = store.read("bars")
        scans = store.read("scanner_hits")
        for row in opps.iter_rows(named=True):
            full_day = day_chart_bars(bars, row["opportunity_id"], settings)
            for run in symbol_runs(row, bars, scans, settings):
                if not run.bars:
                    continue
                cd = build_opportunity_chart(
                    run.bars, settings, first_hit=run.first_hit, chart_bars=full_day
                )
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


def _index_opportunities(charts_payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Project a date's chart payload to its navigation entries (one per opportunity/run)."""
    return [
        {
            "opportunity_id": c["opportunity_id"],
            "symbol": c["symbol"],
            "run": c["run"],
            "run_count": c["run_count"],
            "triggered": c["triggered"],
            "max_r": c["max_r"],
        }
        for c in charts_payload["charts"]
    ]


def build_index(date_charts: list[tuple[date, dict[str, Any]]], now: datetime) -> dict[str, Any]:
    """The review-workbench navigation index over every collected date (#141).

    ``date_charts`` pairs each trading date with its :func:`build_charts` payload. Dates are sorted
    newest-first so the date picker opens on the latest session; each date lists its opportunities
    (mirroring the chart selection list). Used by the full-archive backfill."""
    dates: list[dict[str, Any]] = [
        {"date": d.isoformat(), "opportunities": _index_opportunities(cp)} for d, cp in date_charts
    ]
    dates.sort(key=lambda e: str(e["date"]), reverse=True)
    return {"generated_utc": now.isoformat(), "dates": dates}


def upsert_index_date(
    existing: dict[str, Any] | None,
    trading_date: date,
    charts_payload: dict[str, Any],
    now: datetime,
) -> dict[str, Any]:
    """Insert/replace one date in an existing index, keeping newest-first order (#141).

    The live loop and per-date backfill refresh a single day without recomputing the archive: drop
    any prior entry for ``trading_date``, append the fresh one, re-sort. A malformed/absent existing
    index degrades to a one-date index."""
    prior = existing.get("dates", []) if isinstance(existing, dict) else []
    kept: list[dict[str, Any]] = [
        e for e in prior if isinstance(e, dict) and e.get("date") != trading_date.isoformat()
    ]
    kept.append(
        {"date": trading_date.isoformat(), "opportunities": _index_opportunities(charts_payload)}
    )
    kept.sort(key=lambda e: str(e["date"]), reverse=True)
    return {"generated_utc": now.isoformat(), "dates": kept}


def charts_path(dashboard_dir: Path, trading_date: date) -> Path:
    """Path of the never-overwritten per-date chart file: ``<dir>/charts/<date>.json`` (#141)."""
    return dashboard_dir / "charts" / f"{trading_date.isoformat()}.json"


def read_json(path: Path) -> dict[str, Any] | None:
    """Load a JSON object from ``path``; None if missing or unparsable (a fresh/absent index)."""
    if not path.exists():
        return None
    try:
        loaded = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return loaded if isinstance(loaded, dict) else None


def write_json(path: Path, payload: dict[str, Any]) -> None:
    """Serialise atomically (tmp file + os.replace) so a consumer never sees a partial write."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, default=_json_default, indent=2))
    os.replace(tmp, path)


def _content_key(payload: dict[str, Any]) -> str:
    """Canonical JSON of ``payload`` minus the volatile ``generated_utc`` stamp, for diffing."""
    body = {k: v for k, v in payload.items() if k != "generated_utc"}
    return json.dumps(body, default=_json_default, sort_keys=True)


def write_json_if_changed(path: Path, payload: dict[str, Any]) -> bool:
    """Write ``payload`` only if its content (ignoring ``generated_utc``) differs from disk.

    The stats/charts refresh runs on every tick (app.Application._refresh_stats_charts), not just at
    EOD. Rewriting an unchanged charts.json each tick would bump its ``generated_utc`` and the
    front-end — which redraws whenever that stamp changes — would reset the user's chart zoom/pan on
    every 60s poll. Skipping no-op writes keeps the published file (and its stamp) stable until the
    underlying data actually changes. Returns True iff it wrote.
    """
    if path.exists():
        try:
            existing = json.loads(path.read_text())
        except (OSError, ValueError):
            existing = None
        if isinstance(existing, dict) and _content_key(existing) == _content_key(payload):
            return False
    write_json(path, payload)
    return True
