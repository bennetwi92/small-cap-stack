"""ib_async adapter implementing the supervisor's Transport protocol.

Thin live glue (exercised against a real Gateway, not unit-tested): wires ib_async events to
the supervisor's model, resyncs orders/positions/account on connect, replays market-data
subscriptions, and routes connectivity error codes (1100/1101/1102).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import cast

from ib_async import IB, Contract

from ..config import Settings
from ..logging import get_logger
from .errors import ConnAction, classify_connection_error
from .retry import RetryPolicy
from .subscriptions import SubscriptionRegistry
from .supervisor import ConnectionSupervisor, Hook

log = get_logger(__name__)


class IBKRTransport:
    """Wraps ``ib_async.IB`` to satisfy ``Transport`` and own resync/replay."""

    def __init__(self, settings: Settings, registry: SubscriptionRegistry | None = None) -> None:
        self._s = settings
        self.ib = IB()
        self.registry = registry or SubscriptionRegistry()
        self._disconnected = asyncio.Event()
        self._disconnected.set()  # starts disconnected
        self.ib.disconnectedEvent += self._on_ib_disconnected
        self.ib.errorEvent += self._on_ib_error

    # --- Transport protocol -------------------------------------------------------------

    @property
    def disconnected(self) -> asyncio.Event:
        return self._disconnected

    async def connect(self) -> None:
        await self.ib.connectAsync(
            self._s.ibkr_host,
            self._s.ibkr_port,
            clientId=self._s.ibkr_client_id,
            timeout=15,
        )

    def disconnect(self) -> None:
        self.ib.disconnect()

    def is_connected(self) -> bool:
        return bool(self.ib.isConnected())

    # --- resync (supervisor on_connect hook) --------------------------------------------

    async def resync(self) -> None:
        """Rebuild local state after a (re)connect, then replay subscriptions."""
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

    def _on_ib_error(self, reqId: int, code: int, msg: str, *_: object) -> None:
        action = classify_connection_error(code)
        if action is ConnAction.RESUBSCRIBE:
            log.warning("ibkr.data_lost_resubscribing", code=code)
            asyncio.get_event_loop().create_task(self.replay_subscriptions())
        elif action is ConnAction.CONNECTIVITY_LOST:
            log.warning("ibkr.connectivity_lost", code=code)
        elif action is ConnAction.DATA_OK:
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
