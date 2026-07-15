"""One-off admin command: (re)generate the dashboard JSON for a past trading date.

The EOD job (:meth:`app.Application._on_eod_report`) is normally the only writer of
``stats.json`` / ``charts.json`` — so a day whose EOD ran on code that predated a dashboard
feature (e.g. the annotated charts, #113) has no such artifact and would otherwise only appear
after the *next* EOD. This command rebuilds those files for an explicit date directly from the
store, so the box can back-fill a day on demand without waiting for 16:30 ET.

It's pure store-read + projection (no IBKR, no live state) — the same functions EOD calls — so it's
safe to run any time. Run it *on the box* (the store lives in the app's docker volume), then trigger
``publish-dashboard`` to push the refreshed files::

    docker exec small-cap-stack-app python -m small_cap_stack.dashboard_backfill --date 2026-07-01

Omit ``--date`` to default to yesterday (ET).
"""

from __future__ import annotations

import argparse
from datetime import UTC, date, timedelta
from typing import Any

import polars as pl

from .clock import now_et
from .config import Settings, get_settings
from .dashboard import (
    build_charts,
    build_index,
    build_stats,
    charts_path,
    read_json,
    upsert_index_date,
    write_json,
)
from .logging import configure_logging, get_logger
from .portfolio import build_portfolio_payload
from .report import build_eod_report
from .storage import Store

log = get_logger(__name__)


def _parse_date(raw: str | None) -> date:
    if raw is None:
        return (now_et() - timedelta(days=1)).date()
    return date.fromisoformat(raw)


def _collected_dates(store: Store) -> list[date]:
    """Every trading date with a captured opportunity, ascending (store-raw / compute-on-read)."""
    opps = store.read("opportunities")
    if opps.is_empty() or "trading_date" not in opps.columns:
        return []
    vals = opps.select(pl.col("trading_date")).unique().to_series().to_list()
    return sorted(d for d in vals if d is not None)


def regenerate(
    trading_date: date,
    settings: Settings | None = None,
    store: Store | None = None,
) -> tuple[int, int]:
    """Rebuild one date's dashboard artifacts; return (opportunities, charts).

    Writes ``stats.json`` + the legacy single-day ``charts.json`` (existing dashboard), the
    never-overwritten ``charts/<date>.json``, and refreshes ``index.json`` for this date (#141).
    """
    settings = settings or get_settings()
    store = store or Store(settings.data_dir)
    now_utc = now_et().astimezone(UTC)
    out = settings.data_dir / "dashboard"

    report = build_eod_report(store, settings, trading_date)
    write_json(out / "stats.json", build_stats(report, now_utc))

    charts = build_charts(store, settings, trading_date, now_utc)
    write_json(out / "charts.json", charts)
    write_json(charts_path(out, trading_date), charts)
    write_json(
        out / "index.json",
        upsert_index_date(read_json(out / "index.json"), trading_date, charts, now_utc),
    )
    # The virtual-portfolio book (#230) is cross-day; rebuild it whenever any date is regenerated.
    write_json(out / "portfolio.json", build_portfolio_payload(store, settings, now_utc))

    n_opps = len(report.analyses)
    n_charts = len(charts["charts"])
    log.info(
        "dashboard.backfill_done",
        trading_date=trading_date.isoformat(),
        opportunities=n_opps,
        charts=n_charts,
        out=str(out),
    )
    return n_opps, n_charts


def regenerate_archive(
    settings: Settings | None = None,
    store: Store | None = None,
) -> tuple[int, int]:
    """Full-archive backfill: dated chart file per collected date + a complete index (#141).

    Populates the review workbench's date picker from day one — enumerates every past date with
    captured bars, writes each ``charts/<date>.json``, and rebuilds ``index.json`` across all of
    them. Also refreshes the newest date's ``stats.json`` + legacy ``charts.json`` so the existing
    single-day dashboard lands on the latest session. Returns (dates, total charts)."""
    settings = settings or get_settings()
    store = store or Store(settings.data_dir)
    now_utc = now_et().astimezone(UTC)
    out = settings.data_dir / "dashboard"

    dates = _collected_dates(store)
    date_charts: list[tuple[date, dict[str, Any]]] = []
    total_charts = 0
    for d in dates:
        charts = build_charts(store, settings, d, now_utc)
        write_json(charts_path(out, d), charts)
        date_charts.append((d, charts))
        total_charts += len(charts["charts"])

    write_json(out / "index.json", build_index(date_charts, now_utc))
    write_json(out / "portfolio.json", build_portfolio_payload(store, settings, now_utc))

    if dates:  # keep the legacy single-day dashboard on the newest session
        latest = dates[-1]
        report = build_eod_report(store, settings, latest)
        write_json(out / "stats.json", build_stats(report, now_utc))
        write_json(out / "charts.json", build_charts(store, settings, latest, now_utc))

    log.info(
        "dashboard.archive_backfill_done",
        dates=len(dates),
        charts=total_charts,
        out=str(out),
    )
    return len(dates), total_charts


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m small_cap_stack.dashboard_backfill",
        description="Regenerate dashboard artifacts for a trading date (or the whole archive).",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--date",
        metavar="YYYY-MM-DD",
        default=None,
        help="Trading date to rebuild (default: yesterday, ET).",
    )
    group.add_argument(
        "--all",
        action="store_true",
        help="Full-archive backfill: dated charts + index for every collected date.",
    )
    args = parser.parse_args()

    settings = get_settings()
    configure_logging(level=settings.log_level, json_logs=settings.json_logs)

    if args.all:
        n_dates, n_charts = regenerate_archive(settings)
        print(  # noqa: T201 — a one-off CLI should report its result on stdout
            f"backfilled dashboard archive: {n_dates} dates, {n_charts} charts"
        )
        return

    trading_date = _parse_date(args.date)
    n_opps, n_charts = regenerate(trading_date)
    print(  # noqa: T201 — a one-off CLI should report its result on stdout
        f"regenerated dashboard for {trading_date.isoformat()}: "
        f"{n_opps} opportunities, {n_charts} charts"
    )


if __name__ == "__main__":
    main()
