"""Observability (issue #5): Prometheus metrics + a Healthchecks.io dead-man's switch.

Metrics are module-level (the idiomatic prometheus-client pattern) and incremented from the
app/capture/connection layers. The Heartbeat pings Healthchecks.io each tick so an external
service alerts if the process dies or wedges; cold IBKR disconnects trigger a failure ping.
"""

from __future__ import annotations

import asyncio
import urllib.request
from collections.abc import Callable

from prometheus_client import REGISTRY, Counter, Gauge, start_http_server

from .logging import get_logger

log = get_logger(__name__)

# --- metrics ----------------------------------------------------------------------------
SCAN_TICKS = Counter("scs_scan_ticks_total", "Scan/capture ticks executed")
OPPORTUNITIES = Counter("scs_opportunities_total", "Opportunities opened")
BARS_APPENDED = Counter("scs_bars_appended_total", "5-min bars appended")
COLD_DISCONNECTS = Counter("scs_cold_disconnects_total", "Cold (unexpected) IBKR disconnects")
IBKR_CONNECTED = Gauge("scs_ibkr_connected", "1 if connected to IBKR else 0")
# Tick self-reporting (#321): three PRs missed a 36s/60s tick regression because nothing measured
# the tick. These are also surfaced in status.json every tick, so they're readable on the
# dashboard without SSH or a Prometheus scrape.
TICK_SECONDS = Gauge("scs_tick_seconds", "Duration of the last completed tick")
STATUS_BUILD_SECONDS = Gauge("scs_status_build_seconds", "Duration of the last status build")
TICKS_OVER_BUDGET = Counter(
    "scs_ticks_over_budget_total", "Ticks that ran longer than half the tick interval"
)
JOBS_MISSED = Counter(
    "scs_jobs_missed_total",
    "Scheduled jobs skipped entirely (max_instances/misfire) — previously invisible",
)
DATASET_FILES = Gauge(
    "scs_dataset_files",
    "Parquet files per dataset — for this store, read cost tracks file count, not rows",
    ["dataset"],
)


def metric_value(name: str) -> float:
    """Read a metric's current value from the default registry (0.0 if absent)."""
    return REGISTRY.get_sample_value(name) or 0.0


def start_metrics_server(port: int) -> None:
    """Expose /metrics on the given port (no-op-safe to call once at startup)."""
    start_http_server(port)
    log.info("metrics.server_started", port=port)


def _http_get(url: str) -> None:
    with urllib.request.urlopen(url, timeout=5) as resp:  # noqa: S310 — trusted Healthchecks URL
        resp.read()


class Heartbeat:
    """Healthchecks.io dead-man's switch. No-op when no URL is configured."""

    def __init__(
        self, url: str, fetch: Callable[[str], None] | None = None, timeout_sec: float = 10.0
    ) -> None:
        self.url = url.rstrip("/")
        self._fetch = fetch or _http_get
        self.timeout_sec = timeout_sec

    async def ping(self) -> None:
        await self._send(self.url)

    async def fail(self) -> None:
        await self._send(f"{self.url}/fail")

    async def _send(self, url: str) -> None:
        if not self.url:
            return
        try:
            async with asyncio.timeout(self.timeout_sec):
                await asyncio.to_thread(self._fetch, url)
        except Exception:  # noqa: BLE001 — heartbeat is best-effort, never break the loop
            log.warning("heartbeat.failed", url=url)
