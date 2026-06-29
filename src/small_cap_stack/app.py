"""The long-lived asyncio application: scheduler + IBKR connection + graceful shutdown.

Phase-1 runtime (issues #12, #11, #31). Owns the IBKR connection supervisor and the scheduler.
The scheduled callbacks still run a placeholder pipeline; the real scanner/gate/capture/report
tasks (#13–#19) plug into the same shape.
"""

from __future__ import annotations

import asyncio
import signal
from datetime import timedelta

from .clock import now_et
from .config import Settings, get_settings
from .ibkr.subscriptions import SubscriptionRegistry
from .ibkr.supervisor import ConnectionSupervisor
from .ibkr.transport import IBKRTransport
from .logging import configure_logging, get_logger
from .pipeline import DagResult, Task, run_dag
from .scanner import Candidate, Scanner
from .scheduler import build_scheduler

log = get_logger(__name__)


class Application:
    """Owns the IBKR connection, the scheduler, and the process lifecycle."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._shutdown = asyncio.Event()
        self._conn_task: asyncio.Task[None] | None = None

        self.subscriptions = SubscriptionRegistry()
        self.transport = IBKRTransport(settings, self.subscriptions)
        self.scanner = Scanner(settings)
        self.supervisor = ConnectionSupervisor(
            self.transport,
            on_connect=self.transport.resync,
            on_cold_disconnect=self._alert_cold_disconnect,
            is_expected_restart=self._is_expected_restart,
        )
        self.scheduler = build_scheduler(
            settings,
            on_scan_start=self._on_scan_start,
            on_scan_end=self._on_scan_end,
            on_eod_report=self._on_eod_report,
        )

    async def run(self) -> None:
        self._install_signal_handlers()
        self.scheduler.start()
        self._conn_task = asyncio.create_task(self.supervisor.run(), name="ibkr-supervisor")
        log.info(
            "app.started",
            mode=self.settings.ibkr_trading_mode,
            tz=self.settings.timezone,
            scan_window=f"{self.settings.scan_start:%H:%M}-{self.settings.scan_end:%H:%M}",
        )
        try:
            await self._shutdown.wait()
        finally:
            self.supervisor.stop()
            self.scheduler.shutdown(wait=False)
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
        start = now.replace(hour=r.hour, minute=r.minute, second=0, microsecond=0)
        end = start + timedelta(minutes=self.settings.gateway_restart_window_min)
        return start <= now <= end

    async def _alert_cold_disconnect(self) -> None:
        # TODO(#5): ping Healthchecks.io / alert channel. For now, a loud structured error.
        log.error("ibkr.cold_disconnect_alert")

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

    # --- scheduled callbacks (placeholders for the Phase-1 pipeline) ---------------------

    async def _on_scan_start(self) -> None:
        log.info("scan.window_open")
        result = await self._run_pipeline()
        log.info("scan.tick_complete", ok=result.ok)

    async def _on_scan_end(self) -> None:
        log.info("scan.window_closed")

    async def _on_eod_report(self) -> None:
        log.info("report.eod_start")

    async def _run_pipeline(self) -> DagResult:
        """Scan → gate → capture. Scan is real (#13); gate/capture are placeholders (#14/#15)."""
        candidates: list[Candidate] = []

        async def scan() -> int:
            if not self.transport.is_connected():
                log.warning("scan.skipped_disconnected")
                return 0
            found = await self.scanner.scan(self.transport.ib)
            candidates.extend(found)
            log.info("scan.candidates", count=len(found), symbols=[c.symbol for c in found])
            return len(found)

        async def gate() -> int:
            return 0  # gate engine is #15

        async def capture() -> int:
            return 0  # raw capture is #14

        tasks = [
            Task("scan", scan),
            Task("gate", gate, deps=("scan",)),
            Task("capture", capture, deps=("gate",)),
        ]
        return await run_dag(tasks)


async def main() -> None:
    settings = get_settings()
    configure_logging(level=settings.log_level, json_logs=settings.json_logs)
    await Application(settings).run()


def run_sync() -> None:
    """Console-script / systemd entry point."""
    asyncio.run(main())
