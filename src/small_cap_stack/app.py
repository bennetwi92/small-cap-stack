"""The long-lived asyncio application: scheduler + IBKR connection + capture loop.

Phase-1 runtime. Owns the IBKR connection supervisor, the scheduler, and the capture service.
A periodic `tick` drives the real work: scan for candidates during the scan window and record
each flagged opportunity's evolving record (bars/news) until the capture window closes.
"""

from __future__ import annotations

import asyncio
import signal
from datetime import UTC, datetime, timedelta

from .capture import CaptureService
from .clock import ET_NAME, now_et, within_window
from .config import Settings, get_settings
from .dashboard import (
    StatusInputs,
    build_charts,
    build_stats,
    build_status,
    charts_path,
    read_json,
    upsert_index_date,
    write_json,
    write_json_if_changed,
)
from .fundamentals import YFinanceFundamentals
from .ibkr.subscriptions import SubscriptionRegistry
from .ibkr.supervisor import ConnectionSupervisor
from .ibkr.transport import IBKRTransport
from .logging import configure_logging, get_logger
from .marketdata import IBKRMarketData
from .monitoring import (
    COLD_DISCONNECTS,
    IBKR_CONNECTED,
    SCAN_TICKS,
    Heartbeat,
    metric_value,
    start_metrics_server,
)
from .report import EodReport, analysis_records, build_eod_report
from .scanner import Scanner
from .scheduler import build_scheduler
from .storage import Store

log = get_logger(__name__)


class Application:
    """Owns the IBKR connection, the scheduler, the capture service, and the lifecycle."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._shutdown = asyncio.Event()
        self._conn_task: asyncio.Task[None] | None = None

        self.subscriptions = SubscriptionRegistry()
        self.transport = IBKRTransport(settings, self.subscriptions)
        self.scanner = Scanner(settings)
        self.store = Store(settings.data_dir)
        self.market_data = IBKRMarketData(self.transport.ib, settings)
        self.capture = CaptureService(
            store=self.store,
            bars=self.market_data,
            news=self.market_data,
            settings=settings,
            fundamentals=YFinanceFundamentals(timeout_sec=settings.fundamentals_timeout_sec),
        )
        self.heartbeat = Heartbeat(
            settings.healthchecks_ping_url, timeout_sec=settings.heartbeat_timeout_sec
        )
        self.supervisor = ConnectionSupervisor(
            self.transport,
            on_connect=self._on_connect,
            on_cold_disconnect=self._alert_cold_disconnect,
            is_expected_restart=self._is_expected_restart,
        )
        self.scheduler = build_scheduler(
            settings,
            on_tick=self._on_tick,
            on_scan_start=self._on_scan_start,
            on_scan_end=self._on_scan_end,
            on_eod_bars=self._on_eod_bars,
            on_eod_report=self._on_eod_report,
            on_eod_backfill=self._on_eod_backfill,
        )

    async def run(self) -> None:
        self._install_signal_handlers()
        if self.settings.metrics_enabled:
            start_metrics_server(self.settings.metrics_port)
        self.scheduler.start()
        self._conn_task = asyncio.create_task(self.supervisor.run(), name="ibkr-supervisor")
        log.info(
            "app.started",
            mode=self.settings.ibkr_trading_mode,
            tz=ET_NAME,
            scan_window=f"{self.settings.scan_start:%H:%M}-{self.settings.scan_end:%H:%M}",
        )
        try:
            await self._shutdown.wait()
        finally:
            # Stop launching new ticks before tearing down the connection, so a tick can't fire
            # against a half-closed Gateway during shutdown.
            self.scheduler.shutdown(wait=False)
            self.supervisor.stop()
            if self._conn_task is not None:
                try:
                    await asyncio.wait_for(self._conn_task, timeout=10)
                except (TimeoutError, asyncio.CancelledError):
                    self._conn_task.cancel()
            log.info("app.stopped")

    def _is_expected_restart(self) -> bool:
        """True during the daily Gateway-restart window (disconnects there aren't cold)."""
        now = now_et()
        r = self.settings.gateway_restart
        window = timedelta(minutes=self.settings.gateway_restart_window_min)
        start = now.replace(hour=r.hour, minute=r.minute, second=0, microsecond=0)
        # Check today's window and the one that began yesterday (it may wrap past midnight).
        return any(s <= now <= s + window for s in (start, start - timedelta(days=1)))

    async def _on_connect(self) -> None:
        IBKR_CONNECTED.set(1)
        await self.transport.resync()

    async def _alert_cold_disconnect(self) -> None:
        IBKR_CONNECTED.set(0)
        COLD_DISCONNECTS.inc()
        log.error("ibkr.cold_disconnect_alert")
        await self.heartbeat.fail()

    def request_shutdown(self) -> None:
        log.info("app.shutdown_requested")
        self._shutdown.set()

    def _install_signal_handlers(self) -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self.request_shutdown)
            except NotImplementedError:  # e.g. on Windows
                log.warning("app.signal_handler_unavailable", signal=sig.name)

    # --- the periodic work loop ---------------------------------------------------------

    async def _on_tick(self) -> None:
        """Intraday discovery: scan for candidates during the scan window (bars come at EOD)."""
        SCAN_TICKS.inc()
        await self.heartbeat.ping()  # dead-man's switch: process is alive
        now = now_et()
        if self.transport.is_connected() and within_window(
            now, self.settings.scan_start, self.settings.scan_end
        ):
            candidates = await self.scanner.scan(self.transport.ib)
            log.info(
                "scan.candidates", count=len(candidates), symbols=[c.symbol for c in candidates]
            )
            await self.capture.on_scan_tick(candidates, now)
        if self.settings.dashboard_enabled:
            self._export_status(now)
            self._refresh_stats_charts(now)

    def _refresh_stats_charts(self, now: datetime) -> None:
        """Catch-up refresh of the EOD stats/charts on the tick (best-effort).

        The 16:30 ET ``eod_report`` cron is no longer the *only* writer of stats.json/charts.json.
        Once the day's bars are in (>= ``eod_bars_fetch``) each tick rebuilds today's stats/charts,
        so the dashboard advances to the completed session even when that single job was missed —
        e.g. a deploy/restart after 16:30 would otherwise leave it stuck on yesterday until the next
        close. Before that time we leave the files untouched so the previous session stays
        reviewable all day (#117). A day with no opportunities is skipped inside
        ``_export_stats_charts`` (a weekend/holiday never overwrites the last real session).
        """
        if now.time() < self.settings.eod_bars_fetch:
            return
        try:
            report = build_eod_report(self.store, self.settings, now.date())
            self._export_stats_charts(report, now.astimezone(UTC))
        except Exception:  # noqa: BLE001 — a dashboard refresh must never break the tick
            log.warning("dashboard.refresh_failed")

    def _export_stats_charts(self, report: EodReport, now_utc: datetime) -> None:
        """Write stats.json + charts.json + the dated review payload for ``report``.

        Best-effort, content-diffed. Shared by the EOD job and the tick refresh. Skips a report with
        no opportunities so a non-trading day never overwrites the last completed session the
        dashboard shows all day. Besides the legacy single-day ``charts.json`` (existing dashboard),
        it publishes the never-overwritten ``charts/<date>.json`` and refreshes ``index.json`` so
        the review workbench (#141) can navigate back through every collected day.
        """
        if not self.settings.dashboard_enabled or not report.analyses:
            return
        out = self.settings.data_dir / "dashboard"
        try:
            write_json_if_changed(out / "stats.json", build_stats(report, now_utc))
        except Exception:  # noqa: BLE001 — a dashboard write must never break the caller
            log.warning("dashboard.stats_write_failed")
        try:
            charts = build_charts(self.store, self.settings, report.trading_date, now_utc)
            write_json_if_changed(out / "charts.json", charts)  # legacy single-day file
            write_json_if_changed(charts_path(out, report.trading_date), charts)
            write_json_if_changed(
                out / "index.json",
                upsert_index_date(
                    read_json(out / "index.json"), report.trading_date, charts, now_utc
                ),
            )
        except Exception:  # noqa: BLE001 — a dashboard write must never break the caller
            log.warning("dashboard.charts_write_failed")

    def _export_status(self, now: datetime) -> None:
        """Write the dashboard status snapshot (#68). Best-effort — never breaks a tick."""
        try:
            inputs = StatusInputs(
                now=now.astimezone(UTC),
                trading_date=now.date(),
                connected=self.transport.is_connected(),
                trading_mode=self.settings.ibkr_trading_mode,
                in_scan_window=within_window(now, self.settings.scan_start, self.settings.scan_end),
                deployed_commit=self.settings.deployed_commit or None,
                scan_ticks_total=int(metric_value("scs_scan_ticks_total")),
                jobs=[(j.id, j.next_run_time) for j in self.scheduler.get_jobs()],
            )
            write_json(
                self.settings.data_dir / "dashboard" / "status.json",
                build_status(self.store, inputs),
            )
        except Exception:  # noqa: BLE001 — a dashboard write must never break the tick
            log.warning("dashboard.status_write_failed")

    async def _on_scan_start(self) -> None:
        log.info("scan.window_open")

    async def _on_scan_end(self) -> None:
        log.info("scan.window_closed")

    async def _on_eod_bars(self) -> None:
        """Batch-fetch the day's 5-min bars + re-fetch news for every flagged opportunity.

        Both run before the report. The news re-fetch (#97) captures stories that broke after a
        symbol's first sighting. Retries on a disconnect / transient failure rather than skipping
        outright (#100); if every attempt fails, the morning back-fill recovers the day.
        """
        log.info("bars.eod_start")
        trading_date = now_et().date()
        for attempt in range(1, self.settings.eod_retry_attempts + 1):
            try:
                if not self.transport.is_connected():
                    raise ConnectionError("ibkr disconnected")
                await self.capture.capture_day_bars(trading_date)
                await self.capture.capture_day_news(trading_date)
                log.info("bars.eod_done")
                return
            except Exception:  # noqa: BLE001 — retry any transient failure; back-fill is the net
                log.warning(
                    "bars.eod_attempt_failed", attempt=attempt, of=self.settings.eod_retry_attempts
                )
                if attempt < self.settings.eod_retry_attempts:
                    await asyncio.sleep(self.settings.eod_retry_delay_sec)
        log.error("bars.eod_failed_after_retries")  # the morning back-fill (#100) will recover it

    async def _on_eod_backfill(self) -> None:
        """Morning catch-up: fill bars for any recent day whose opportunities are missing them.

        Recovers days where the EOD batch never completed (Gateway down at 16:20). Idempotent —
        bars dedup on read — and it refreshes the report markdown for each day it repairs (#100).
        """
        log.info("backfill.start")
        if not self.transport.is_connected():
            log.warning("backfill.skipped_disconnected")
            return
        filled = await self.capture.backfill_recent(
            now_et().date(), days=self.settings.backfill_days
        )
        for d in filled:
            report = build_eod_report(self.store, self.settings, d)
            if report.analyses:
                self._write_report_markdown(report)  # refresh the artifact with the repaired bars
        log.info("backfill.done", days_filled=len(filled))

    async def _on_eod_report(self) -> None:
        log.info("report.eod_start")
        report = build_eod_report(self.store, self.settings, now_et().date())
        if report.analyses:
            self.store.append(
                "analysis", analysis_records(report), partition_date=report.trading_date
            )
            self._write_report_markdown(report)
        self._export_stats_charts(report, now_et().astimezone(UTC))
        log.info("report.eod_done", **report.aggregates)
        self.capture.reset()

    def _write_report_markdown(self, report: EodReport) -> None:
        out = self.settings.data_dir / "reports"
        out.mkdir(parents=True, exist_ok=True)
        (out / f"eod_{report.trading_date.isoformat()}.md").write_text(report.markdown)


async def main() -> None:
    settings = get_settings()
    configure_logging(level=settings.log_level, json_logs=settings.json_logs)
    await Application(settings).run()


def run_sync() -> None:
    """Console-script / systemd entry point."""
    asyncio.run(main())
