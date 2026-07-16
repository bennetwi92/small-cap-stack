"""ib_async adapter implementing the supervisor's Transport protocol.

Thin live glue (exercised against a real Gateway, not unit-tested): wires ib_async events to
the supervisor's model, resyncs orders/positions/account on connect, and routes connectivity
error codes (1100/1101/1102). Phase-1 pulls bars/news via one-shot historical requests rather
than streaming, so there are no live market-data subscriptions to replay after a reconnect.
"""

from __future__ import annotations

import asyncio

from ib_async import IB

from ..config import Settings
from ..logging import get_logger
from .errors import ConnAction, classify_connection_error

log = get_logger(__name__)


def client_id_for_attempt(base: int, attempt: int, pool: int) -> int:
    """The client id to connect with on ``attempt`` (0-based), cycling a small pool from ``base``.

    A reconnect after an *unclean* disconnect can hit error 326 (client id still held); rotating to
    the next id sidesteps it. ``attempt`` resets to 0 on a successful (re)sync, so steady state
    keeps ``base`` and only a stuck id bumps upward."""
    return base + (attempt % max(1, pool))


class IBKRTransport:
    """Wraps ``ib_async.IB`` to satisfy ``Transport`` and own the on-connect resync."""

    def __init__(self, settings: Settings) -> None:
        self._s = settings
        self.ib = IB()
        self._disconnected = asyncio.Event()
        self._disconnected.set()  # starts disconnected
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
        """Rebuild local state after a (re)connect (orders/positions)."""
        # Connected: the next reconnect starts from the base client id again, and a fresh connection
        # assumes the data farm is up until a 1100 says otherwise.
        self._connect_attempt = 0
        self._data_farm_ok = True
        orders = await self.ib.reqAllOpenOrdersAsync()
        positions = await self.ib.reqPositionsAsync()
        log.info("ibkr.resynced", open_orders=len(orders), positions=len(positions))

    # --- event handlers -----------------------------------------------------------------

    def _on_ib_disconnected(self) -> None:
        self._disconnected.set()

    def _on_ib_error(self, reqId: int, code: int, msg: str, *_: object) -> None:
        action = classify_connection_error(code)
        if action is ConnAction.RESUBSCRIBE:
            self._data_farm_ok = True  # 1101: link restored (feed live again; nothing to replay)
            log.info("ibkr.connectivity_restored", code=code)
        elif action is ConnAction.CONNECTIVITY_LOST:
            self._data_farm_ok = False  # 1100: farm down -> the feed is dead until 1101/1102
            log.warning("ibkr.connectivity_lost", code=code)
        elif action is ConnAction.DATA_OK:
            self._data_farm_ok = True  # 1102: link restored, subscriptions maintained
            log.info("ibkr.connectivity_restored", code=code)
