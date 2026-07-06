"""ib_async adapter implementing the supervisor's Transport protocol.

Thin live glue (exercised against a real Gateway, not unit-tested): wires ib_async events to
the supervisor's model, resyncs orders/positions/account on connect, replays market-data
subscriptions, and routes connectivity error codes (1100/1101/1102).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, cast

from ib_async import IB, Contract

from ..config import Settings
from ..logging import get_logger
from .errors import ConnAction, classify_connection_error
from .retry import RetryPolicy
from .subscriptions import SubscriptionRegistry
from .supervisor import ConnectionSupervisor, Hook

log = get_logger(__name__)


def client_id_for_attempt(base: int, attempt: int, pool: int) -> int:
    """The client id to connect with on ``attempt`` (0-based), cycling a small pool from ``base``.

    A reconnect after an *unclean* disconnect can hit error 326 (client id still held); rotating to
    the next id sidesteps it. ``attempt`` resets to 0 on a successful (re)sync, so steady state
    keeps ``base`` and only a stuck id bumps upward."""
    return base + (attempt % max(1, pool))


class IBKRTransport:
    """Wraps ``ib_async.IB`` to satisfy ``Transport`` and own resync/replay."""

    def __init__(self, settings: Settings, registry: SubscriptionRegistry | None = None) -> None:
        self._s = settings
        self.ib = IB()
        self.registry = registry if registry is not None else SubscriptionRegistry()
        self._disconnected = asyncio.Event()
        self._disconnected.set()  # starts disconnected
        self._bg_tasks: set[asyncio.Task[None]] = set()  # keep fire-and-forget tasks alive
        self._connect_attempt = 0  # rotates the client id across reconnect retries (#163-C2)
        self._data_farm_ok = True  # cleared by error 1100, restored by 1101/1102 (#163-C2)
        self.ib.disconnectedEvent += self._on_ib_disconnected
        self.ib.errorEvent += self._on_ib_error

    # --- Transport protocol -------------------------------------------------------------

    @property
    def disconnected(self) -> asyncio.Event:
        return self._disconnected

    async def connect(self) -> None:
        client_id = client_id_for_attempt(
            self._s.ibkr_client_id, self._connect_attempt, self._s.ibkr_client_id_pool
        )
        self._connect_attempt += 1
        await self.ib.connectAsync(
            self._s.ibkr_host,
            self._s.ibkr_port,
            clientId=client_id,
            timeout=self._s.ibkr_connect_timeout_sec,
        )

    def disconnect(self) -> None:
        self.ib.disconnect()

    def is_connected(self) -> bool:
        """Connected *and* the market-data farm is up — a 1100 (farm down) leaves the API socket
        open, so callers that scan/fetch must treat a dead feed as not-connected (#163-C2)."""
        return bool(self.ib.isConnected()) and self._data_farm_ok

    # --- resync (supervisor on_connect hook) --------------------------------------------

    async def resync(self) -> None:
        """Rebuild local state after a (re)connect, then replay subscriptions."""
        # Connected: the next reconnect starts from the base client id again, and a fresh connection
        # assumes the data farm is up until a 1100 says otherwise.
        self._connect_attempt = 0
        self._data_farm_ok = True
        orders = await self.ib.reqAllOpenOrdersAsync()
        positions = await self.ib.reqPositionsAsync()
        log.info("ibkr.resynced", open_orders=len(orders), positions=len(positions))
        await self.replay_subscriptions()

    async def replay_subscriptions(self) -> None:
        items = self.registry.all()
        for _token, contract in items:
            self.ib.reqMktData(cast(Contract, contract))
        if items:
            log.info("ibkr.subscriptions_replayed", count=len(items))

    # --- event handlers -----------------------------------------------------------------

    def _on_ib_disconnected(self) -> None:
        self._disconnected.set()

    def _spawn(self, coro: Any) -> None:
        """Fire-and-forget a coroutine from a sync ib_async callback, retaining a reference and
        logging any failure (an unreferenced task can be GC'd mid-flight and swallow errors)."""
        task = asyncio.get_running_loop().create_task(coro)
        self._bg_tasks.add(task)

        def _done(t: asyncio.Task[None]) -> None:
            self._bg_tasks.discard(t)
            if not t.cancelled() and (exc := t.exception()) is not None:
                log.warning("ibkr.background_task_failed", error=str(exc))

        task.add_done_callback(_done)

    def _on_ib_error(self, reqId: int, code: int, msg: str, *_: object) -> None:
        action = classify_connection_error(code)
        if action is ConnAction.RESUBSCRIBE:
            self._data_farm_ok = True  # 1101: link restored (subscriptions lost) -> replay
            log.warning("ibkr.data_lost_resubscribing", code=code)
            self._spawn(self.replay_subscriptions())
        elif action is ConnAction.CONNECTIVITY_LOST:
            self._data_farm_ok = False  # 1100: farm down -> the feed is dead until 1101/1102
            log.warning("ibkr.connectivity_lost", code=code)
        elif action is ConnAction.DATA_OK:
            self._data_farm_ok = True  # 1102: link restored, subscriptions maintained
            log.info("ibkr.connectivity_restored", code=code)


def build_supervisor(
    settings: Settings,
    registry: SubscriptionRegistry | None = None,
    *,
    retry: RetryPolicy | None = None,
    on_cold_disconnect: Hook | None = None,
    is_expected_restart: Callable[[], bool] | None = None,
) -> ConnectionSupervisor:
    """Assemble a supervisor over a real IBKR transport, with resync wired as on_connect."""
    transport = IBKRTransport(settings, registry)
    return ConnectionSupervisor(
        transport,
        retry=retry,
        on_connect=transport.resync,
        on_cold_disconnect=on_cold_disconnect,
        is_expected_restart=is_expected_restart,
    )
