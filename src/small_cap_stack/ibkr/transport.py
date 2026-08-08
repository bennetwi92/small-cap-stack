"""ib_async adapter implementing the supervisor's Transport protocol.

Thin live glue: wires ib_async events to the supervisor's model, resyncs orders/positions on
connect, replays registered subscriptions, and routes connectivity error codes (1100/1101/1102).

⚠️ **Phase-2 readiness (#677).** This module was written for a Phase-1 app that places no orders
and holds no streaming subscriptions, and three of its behaviours are wrong the moment either
exists. Two are fixed here; the third is a setting that must be flipped before the first order:

1. ``resync`` used to fetch the open orders and positions and log only their **counts**, discarding
   the state that constitutes recovery. It now keeps them (:class:`BrokerState`) so a caller can
   reconcile against the broker rather than trust local memory.
2. The docstring used to claim there were no subscriptions to replay. True in Phase 1; false from
   the first streamed bar, and a reconnect that silently drops a position's feed means an app-side
   stop can never fire. :meth:`register_subscription` gives resync something to replay, and it is
   a no-op until something registers.
3. ``connect`` rotates the client id after an unclean disconnect (to sidestep error 326). IB scopes
   order events and cancellation rights to the **placing** client id, so rotating while an order is
   working orphans it — the app stops receiving ``orderStatus`` for its own stop and cannot cancel
   it. ``ibkr_pin_client_id`` disables the rotation and **must be true before any order is placed**.

What is deliberately NOT here, because there is nothing yet to build it against: adopting working
orders by ``orderRef`` (needs the OMS, #313) and the per-position feed-staleness watchdog (needs
positions, #313). :class:`BrokerState` is the seam both will read.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from ib_async import IB

from ..config import Settings
from ..logging import get_logger
from ..monitoring import TRADING_MODE_MISMATCH
from .errors import ConnAction, classify_connection_error
from .mode import account_mismatch

log = get_logger(__name__)


@dataclass(frozen=True)
class BrokerState:
    """What the broker says is true, captured at the last :meth:`IBKRTransport.resync`.

    The point is that it is **kept**. The previous implementation fetched exactly this and logged
    `len()` of each, which is a log line rather than a reconciliation: after a crash between placing
    an order and persisting it locally, the app has no record, would re-derive the same setup
    (the engine is compute-on-read) and place a **second** entry — and the ≤2-concurrent guard is
    app-side state that does not know either.

    Empty until something reconciles against it; that consumer is the OMS (#313).
    """

    open_orders: tuple[Any, ...] = ()
    positions: tuple[Any, ...] = ()
    synced_at_client_id: int | None = None

    @property
    def order_refs(self) -> tuple[str, ...]:
        """``orderRef`` of every working order — the broker-side identity an OMS adopts by.

        Deliberately by ``orderRef`` and not by local memory: a deterministic ref
        (``<opportunity_id>|entry``, ``|stop``) survives a restart, a permId does not travel and a
        local dict does not exist after a crash.
        """
        refs = []
        for o in self.open_orders:
            ref = getattr(getattr(o, "order", None), "orderRef", None)
            if ref:
                refs.append(str(ref))
        return tuple(refs)


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
        self._client_id: int | None = None  # the id the live connection actually used
        self.broker_state = BrokerState()  # what the broker said at the last resync (#677)
        self._subscriptions: dict[str, Callable[[], Awaitable[None]]] = {}
        self._data_farm_ok = True  # cleared by error 1100, restored by 1101/1102 (#163-C2)
        self.ib.disconnectedEvent += self._on_ib_disconnected
        self.ib.errorEvent += self._on_ib_error

    # --- Transport protocol -------------------------------------------------------------

    @property
    def disconnected(self) -> asyncio.Event:
        return self._disconnected

    async def connect(self) -> None:
        # Pinned (#677): rotating the id orphans any working order placed under the previous one,
        # because IB scopes order events and cancellation rights to the placing client. Rotation is
        # only safe while the app places no orders.
        if self._s.ibkr_pin_client_id:
            client_id = self._s.ibkr_client_id
        else:
            client_id = client_id_for_attempt(
                self._s.ibkr_client_id, self._connect_attempt, self._s.ibkr_client_id_pool
            )
        self._connect_attempt += 1
        self._client_id = client_id
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

    def register_subscription(self, key: str, resubscribe: Callable[[], Awaitable[None]]) -> None:
        """Register a market-data subscription to be replayed on every reconnect (#677).

        Keyed so re-registering the same stream replaces rather than duplicates it. Nothing
        registers today — Phase 1 pulls bars via one-shot historical requests — so this is a seam,
        and :meth:`resync` replaying an empty dict is why behaviour is unchanged until Gate 5.

        It exists now because the failure it prevents is silent: a reconnect that succeeds while
        quietly dropping a position's feed leaves ``is_connected()`` true, no prices arriving, and
        an app-side stop that can never fire. That looks exactly like a quiet tape.
        """
        self._subscriptions[key] = resubscribe

    def unregister_subscription(self, key: str) -> None:
        self._subscriptions.pop(key, None)

    async def resync(self) -> None:
        """Rebuild local state after a (re)connect: broker truth, then subscription replay."""
        # Connected: the next reconnect starts from the base client id again, and a fresh connection
        # assumes the data farm is up until a 1100 says otherwise.
        self._connect_attempt = 0
        self._data_farm_ok = True
        self._check_trading_mode()
        orders = await self.ib.reqAllOpenOrdersAsync()
        positions = await self.ib.reqPositionsAsync()
        # KEEP it (#677). Logging len() and dropping the rest is what made this a log line rather
        # than a reconciliation; the OMS adopts from here.
        self.broker_state = BrokerState(
            open_orders=tuple(orders),
            positions=tuple(positions),
            synced_at_client_id=self._client_id,
        )
        log.info(
            "ibkr.resynced",
            open_orders=len(orders),
            positions=len(positions),
            order_refs=list(self.broker_state.order_refs),
            client_id=self._client_id,
        )
        # Replay every registered feed. One failure must not abort the rest: a reconnect that
        # restores three of four subscriptions is strictly better than one that restores none, and
        # the failure is logged per key so it is attributable.
        for key, resubscribe in list(self._subscriptions.items()):
            try:
                await resubscribe()
            except Exception:  # noqa: BLE001 — one dead stream must not block the others
                log.warning("ibkr.resubscribe_failed", subscription=key, exc_info=True)
            else:
                log.info("ibkr.resubscribed", subscription=key)

    def _check_trading_mode(self) -> None:
        """Compare the declared mode against the broker's own answer (#663).

        The account ids are the only authoritative source: the settings validator can see
        `IBKR_PORT` but not the Gateway container's `TRADING_MODE`, so the right port pointed at a
        Gateway logged into the other mode passes startup and lands here.

        Logged, deliberately not raised. `ConnectionSupervisor` retries `on_connect` failures
        forever with backoff, so raising would turn a permanent misconfiguration into an unbounded
        warning loop and take the tracker offline with it. The gauge is the durable signal; once
        there is something to disarm (#674), a mismatch should refuse to arm.
        """
        detail = account_mismatch(self._s.ibkr_trading_mode, list(self.ib.managedAccounts()))
        TRADING_MODE_MISMATCH.set(1.0 if detail else 0.0)
        if detail:
            log.critical("ibkr.trading_mode_mismatch", detail=detail)

    # --- event handlers -----------------------------------------------------------------

    def _on_ib_disconnected(self) -> None:
        self._disconnected.set()

    def _on_ib_error(
        self,
        reqId: int,  # noqa: ARG002 — ib_async errorEvent signature
        code: int,
        msg: str,  # noqa: ARG002 — ib_async errorEvent signature
        *_: object,
    ) -> None:
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
