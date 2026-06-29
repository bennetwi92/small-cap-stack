"""Reconnect-and-resync supervisor — the thin custom layer over the Gateway connection.

Transport-agnostic and fully testable: it drives any object satisfying the ``Transport``
protocol (the real one wraps ``ib_async.IB``; tests use a fake). Responsibilities:
- connect with exponential backoff
- on every (re)connect, run resync callbacks (orders/positions/account + subscription replay)
- block until the connection drops, then reconnect
- distinguish an *expected* drop (the daily Gateway restart window) from a *cold* failure,
  alerting a human only on the latter
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Protocol

from ..logging import get_logger
from .retry import RetryPolicy

log = get_logger(__name__)

Hook = Callable[[], Awaitable[None]]
Sleep = Callable[[float], Awaitable[None]]


class Transport(Protocol):
    """Minimal connection surface the supervisor needs."""

    @property
    def disconnected(self) -> asyncio.Event:
        """Set when the connection is down; cleared by the supervisor on connect."""

    async def connect(self) -> None: ...

    def disconnect(self) -> None: ...

    def is_connected(self) -> bool: ...


def _never() -> bool:
    return False


async def _noop() -> None:
    return None


class ConnectionSupervisor:
    """Keeps a Transport connected, resyncing on every (re)connect."""

    def __init__(
        self,
        transport: Transport,
        *,
        retry: RetryPolicy | None = None,
        on_connect: Hook | None = None,
        on_cold_disconnect: Hook | None = None,
        is_expected_restart: Callable[[], bool] | None = None,
        sleep: Sleep | None = None,
    ) -> None:
        self._t = transport
        self._retry = retry or RetryPolicy()
        self._on_connect = on_connect or _noop
        self._on_cold_disconnect = on_cold_disconnect or _noop
        self._is_expected_restart = is_expected_restart or _never
        self._sleep = sleep or asyncio.sleep
        self._stopped = False

    def stop(self) -> None:
        """Ask the supervisor to exit; unblocks a pending disconnect wait."""
        self._stopped = True
        self._t.disconnected.set()

    async def run(self) -> None:
        """Run until ``stop()`` is called."""
        self._stopped = False
        attempt = 0
        while not self._stopped:
            try:
                await self._t.connect()
            except Exception as exc:  # noqa: BLE001 — any connect failure should retry
                attempt += 1
                delay = self._retry.delay(attempt)
                log.warning("ibkr.connect_failed", attempt=attempt, delay=delay, error=str(exc))
                await self._sleep(delay)
                continue

            attempt = 0
            self._t.disconnected.clear()
            log.info("ibkr.connected")
            await self._on_connect()

            await self._t.disconnected.wait()
            if self._stopped:
                break

            if self._is_expected_restart():
                log.info("ibkr.disconnected", expected=True)
            else:
                log.warning("ibkr.disconnected", expected=False)
                await self._on_cold_disconnect()

        self._t.disconnect()
        log.info("ibkr.supervisor_stopped")
