"""The long-lived asyncio application: scheduler + IBKR connection + capture loop.

Phase-1 runtime. Owns the IBKR connection supervisor, the scheduler, and the capture service.
A periodic `tick` drives the real work: scan for candidates during the scan window and record
each flagged opportunity's evolving record (bars/news) until the capture window closes.
"""

from __future__ import annotations

import asyncio
import signal
from datetime import timedelta

from .capture import CaptureService
from .clock import ET_NAME, now_et, within_window
from .config import Settings, get_settings
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
        if not self.transport.is_connected():
            return
        now = now_et()
        if within_window(now, self.settings.scan_start, self.settings.scan_end):
            candidates = await self.scanner.scan(self.transport.ib)
            log.info(
                "scan.candidates", count=len(candidates), symbols=[c.symbol for c in candidates]
            )
            await self.capture.on_scan_tick(candidates, now)

    async def _on_scan_start(self) -> None:
        log.info("scan.window_open")

    async def _on_scan_end(self) -> None:
        log.info("scan.window_closed")

    async def _on_eod_bars(self) -> None:
        """Batch-fetch the day's 5-min bars for every flagged opportunity (before the report)."""
        log.info("bars.eod_start")
        if not self.transport.is_connected():
            log.warning("bars.eod_skipped_disconnected")
            return
        await self.capture.capture_day_bars(now_et().date())
        log.info("bars.eod_done")

    async def _on_eod_report(self) -> None:
        log.info("report.eod_start")
        report = build_eod_report(self.store, self.settings, now_et().date())
        if report.analyses:
            self.store.append(
                "analysis", analysis_records(report), partition_date=report.trading_date
            )
            self._write_report_markdown(report)
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
