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

from .clock import now_et
from .config import Settings, get_settings
from .dashboard import build_charts, build_stats, write_json
from .logging import configure_logging, get_logger
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
    """Rebuild stats.json + charts.json for ``trading_date``; return (opportunities, charts)."""
    settings = settings or get_settings()
    store = store or Store(settings.data_dir)
    now_utc = now_et().astimezone(UTC)
    out = settings.data_dir / "dashboard"

    report = build_eod_report(store, settings, trading_date)
    write_json(out / "stats.json", build_stats(report, now_utc))

    charts = build_charts(store, settings, trading_date, now_utc)
    write_json(out / "charts.json", charts)

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


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m small_cap_stack.dashboard_backfill",
        description="Regenerate dashboard stats.json + charts.json for a past trading date.",
    )
    parser.add_argument(
        "--date",
        metavar="YYYY-MM-DD",
        default=None,
        help="Trading date to rebuild (default: yesterday, ET).",
    )
    args = parser.parse_args()

    settings = get_settings()
    configure_logging(level=settings.log_level, json_logs=settings.json_logs)

    trading_date = _parse_date(args.date)
    n_opps, n_charts = regenerate(trading_date)
    print(  # noqa: T201 — a one-off CLI should report its result on stdout
        f"regenerated dashboard for {trading_date.isoformat()}: "
        f"{n_opps} opportunities, {n_charts} charts"
    )


if __name__ == "__main__":
    main()
