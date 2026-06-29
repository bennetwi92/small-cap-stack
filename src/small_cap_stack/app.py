"""The long-lived asyncio application: scheduler + graceful shutdown.

Phase-1 skeleton (issue #12). The scheduled callbacks currently just log and run a placeholder
pipeline; the real scanner/gate/capture/report tasks (#13–#19) and the IBKR connection (#11)
plug in here later.
"""

from __future__ import annotations

import asyncio
import signal

from .config import Settings, get_settings
from .logging import configure_logging, get_logger
from .pipeline import DagResult, Task, run_dag
from .scheduler import build_scheduler

log = get_logger(__name__)


class Application:
    """Owns the scheduler and the process lifecycle."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._shutdown = asyncio.Event()
        self.scheduler = build_scheduler(
            settings,
            on_scan_start=self._on_scan_start,
            on_scan_end=self._on_scan_end,
            on_eod_report=self._on_eod_report,
        )

    async def run(self) -> None:
        self._install_signal_handlers()
        self.scheduler.start()
        log.info(
            "app.started",
            mode=self.settings.ibkr_trading_mode,
            tz=self.settings.timezone,
            scan_window=f"{self.settings.scan_start:%H:%M}-{self.settings.scan_end:%H:%M}",
        )
        try:
            await self._shutdown.wait()
        finally:
            self.scheduler.shutdown(wait=False)
            log.info("app.stopped")

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
        """Placeholder DAG showing the scan→gate→capture dependency shape."""

        async def scan() -> int:
            return 0  # no candidates yet — real scanner is #13

        async def gate() -> int:
            return 0

        async def capture() -> int:
            return 0

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
