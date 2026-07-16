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

from .clock import now_et
from .config import Settings, get_settings
from .dashboard import (
    build_charts,
    build_stats,
    charts_path,
    index_entry,
    index_from_entries,
    read_json,
    upsert_index_date,
    write_json,
)
from .logging import configure_logging, get_logger
from .portfolio import (
    build_portfolio_payload,
    collected_dates,
    portfolio_candidate_cache_dir,
)
from .report import build_eod_report
from .storage import Store

log = get_logger(__name__)


def _parse_date(raw: str | None) -> date:
    if raw is None:
        return (now_et() - timedelta(days=1)).date()
    return date.fromisoformat(raw)


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
    # The candidate cache makes this re-extract only `trading_date` (the day whose data changed) and
    # read every other day from cache, so a single-date backfill stays O(1 day), not O(archive).
    write_json(
        out / "portfolio.json",
        build_portfolio_payload(
            store,
            settings,
            now_utc,
            cache_dir=portfolio_candidate_cache_dir(settings),
            force_dates={trading_date},
        ),
    )

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

    dates = collected_dates(store)
    entries: list[dict[str, Any]] = []
    total_charts = 0
    latest_charts: dict[str, Any] | None = None
    for d in dates:
        charts = build_charts(store, settings, d, now_utc)
        write_json(charts_path(out, d), charts)
        # `dates` is ascending, so the final iteration's payload is the newest session's — keep it
        # for the legacy charts.json below rather than rebuilding the most expensive date twice.
        latest_charts = charts
        # Reduce the date to its index row and drop the payload: accumulating every date's full
        # charts (all bars for all opportunities, all dates) purely to build the index retained the
        # archive for no reason. This removes ONE O(archive) retention — it does not make --all
        # cheap. build_portfolio_payload below still materialises every day's candidates (with
        # their bars) at once, which is the dominant term and why even a --date run can OOM the box
        # (#273). Don't read this loop as "--all is safe now".
        entries.append(index_entry(d, charts))
        total_charts += len(charts["charts"])

    write_json(out / "index.json", index_from_entries(entries, now_utc))
    # Full-archive rebuild: extract every date once and prime the candidate cache for later
    # single-date backfills (a day re-extracts only if its raw partitions or the settings change).
    write_json(
        out / "portfolio.json",
        build_portfolio_payload(
            store, settings, now_utc, cache_dir=portfolio_candidate_cache_dir(settings)
        ),
    )

    if dates and latest_charts is not None:  # keep the legacy dashboard on the newest session
        report = build_eod_report(store, settings, dates[-1])
        write_json(out / "stats.json", build_stats(report, now_utc))
        write_json(out / "charts.json", latest_charts)

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
        help="Full-archive backfill: every collected date. Heavy — requires --force.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Required with --all: confirm you mean the full-archive rebuild.",
    )
    args = parser.parse_args()
    if args.all and not args.force:
        # --all rebuilds every collected date in one process; an OOM-killed backfill took the box's
        # CI runner offline for 5h37m on 2026-07-16 (#264). The callers that dispatch --all (the
        # phone workflows) require their own separate `force` toggle rather than supplying this
        # automatically — a confirmation the caller auto-answers protects nobody.
        parser.error(
            "--all rebuilds every collected date in one process and has OOM-killed the box "
            "(#264, CLAUDE.md). Prefer one date at a time: --date YYYY-MM-DD. "
            "If you really mean the full archive, pass --force."
        )

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
