"""Tests for the IBKR connection layer (pure logic + supervisor via a fake transport)."""

from __future__ import annotations

import asyncio

import pytest

from small_cap_stack.config import Settings
from small_cap_stack.ibkr import (
    ConnAction,
    ConnectionSupervisor,
    RetryPolicy,
    classify_connection_error,
)
from small_cap_stack.ibkr.mode import account_mismatch, mode_for_accounts, mode_for_port
from small_cap_stack.ibkr.transport import (
    IBKRTransport,
    client_id_for_attempt,
)
from small_cap_stack.monitoring import metric_value
from tests.support import settings


def _settings(**overrides: object) -> Settings:
    return settings(**overrides)


# --- RetryPolicy ------------------------------------------------------------------------


def test_retry_backoff_and_cap() -> None:
    p = RetryPolicy(base=1.0, factor=2.0, max_delay=60.0)
    assert [p.delay(n) for n in (1, 2, 3, 4)] == [1.0, 2.0, 4.0, 8.0]
    assert p.delay(20) == 60.0  # capped


def test_retry_rejects_bad_attempt() -> None:
    with pytest.raises(ValueError, match="attempt must be >= 1"):
        RetryPolicy().delay(0)


# --- error classification ---------------------------------------------------------------


def test_classify_connection_error() -> None:
    assert classify_connection_error(1100) is ConnAction.CONNECTIVITY_LOST
    assert classify_connection_error(1101) is ConnAction.RESUBSCRIBE
    assert classify_connection_error(1102) is ConnAction.DATA_OK
    assert classify_connection_error(201) is ConnAction.IGNORE


# --- ConnectionSupervisor (fake transport) ----------------------------------------------


class FakeTransport:
    def __init__(self, fail_connects: int = 0) -> None:
        self.disconnected = asyncio.Event()
        self.disconnected.set()
        self._fail = fail_connects
        self.connect_calls = 0
        self.disconnect_calls = 0
        self.connected = False

    async def connect(self) -> None:
        self.connect_calls += 1
        if self.connect_calls <= self._fail:
            raise ConnectionError("refused")
        self.connected = True

    def disconnect(self) -> None:
        self.disconnect_calls += 1
        self.connected = False

    def is_connected(self) -> bool:
        return self.connected


async def _instant_sleep(_delay: float) -> None:
    return None


# --- IBKRTransport (offline: monkeypatched ib) ------------------------------------------


def test_client_id_for_attempt_cycles_pool() -> None:
    assert [client_id_for_attempt(1, a, 4) for a in range(6)] == [1, 2, 3, 4, 1, 2]
    assert client_id_for_attempt(5, 7, 1) == 5  # a pool of 1 never rotates


# --- paper-vs-live agreement (#663) -----------------------------------------------------


@pytest.mark.parametrize(
    ("port", "expected"),
    [
        (4002, "paper"),
        (4001, "live"),
        (7497, "paper"),
        (7496, "live"),
        (4004, "paper"),
        (4003, "live"),
    ],
)
def test_mode_for_port_reads_every_published_port(port: int, expected: str) -> None:
    assert mode_for_port(port) == expected


def test_mode_for_port_declines_to_guess_an_unknown_port() -> None:
    # None is "no opinion", not "paper". A tunnel must stay startable; see test_config.
    assert mode_for_port(9999) is None


@pytest.mark.parametrize(
    ("accounts", "expected"),
    [
        (["DU1234567"], "paper"),  # individual paper
        (["DF1234567"], "paper"),  # advisor paper
        (["du1234567"], "paper"),  # IBKR is not case-consistent everywhere; neither are humans
        (["U1234567"], "live"),
        (["U1234567", "U7654321"], "live"),
        ([], None),  # the handshake can report accounts late — empty must NEVER read as live
        (["", "  "], None),
        (["DU1234567", "U7654321"], None),  # mixed: contradictory evidence, so take no side
    ],
)
def test_mode_for_accounts(accounts: list[str], expected: str | None) -> None:
    assert mode_for_accounts(accounts) == expected


def test_account_mismatch_is_silent_when_the_broker_agrees_or_cannot_say() -> None:
    assert account_mismatch("paper", ["DU123"]) is None
    assert account_mismatch("live", ["U123"]) is None
    assert account_mismatch("live", []) is None  # nothing to judge yet


def test_account_mismatch_names_both_sides_when_they_disagree() -> None:
    """The dangerous case: settings say paper, the broker says this is real money."""
    detail = account_mismatch("paper", ["U1234567"])
    assert detail is not None
    assert "'paper'" in detail and "'live'" in detail and "U1234567" in detail


def test_resync_flags_a_mode_mismatch_without_raising() -> None:
    """A mismatch must not raise: the supervisor retries `on_connect` forever, so raising would
    turn a permanent misconfiguration into an endless reconnect loop and take the tracker down."""

    async def scenario() -> None:
        t = IBKRTransport(_settings(ibkr_trading_mode="paper"))

        async def fake_orders() -> list[object]:
            return []

        async def fake_positions() -> list[object]:
            return []

        t.ib.reqAllOpenOrdersAsync = fake_orders  # type: ignore[method-assign]
        t.ib.reqPositionsAsync = fake_positions  # type: ignore[method-assign]
        t.ib.managedAccounts = lambda: ["U1234567"]  # type: ignore[method-assign]

        await t.resync()  # must complete normally
        assert metric_value("scs_trading_mode_mismatch") == 1.0

        t.ib.managedAccounts = lambda: ["DU1234567"]  # type: ignore[method-assign]
        await t.resync()
        assert metric_value("scs_trading_mode_mismatch") == 0.0  # clears when it agrees again

    asyncio.run(scenario())


def test_data_farm_error_1100_makes_transport_not_connected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Error 1100 (data farm down) leaves the API socket open, so is_connected() must still report
    # "not connected" — else the tick scans a dead feed (#163-C2). 1102/1101 restore it.
    t = IBKRTransport(_settings())
    monkeypatch.setattr(t.ib, "isConnected", lambda: True)  # pretend the socket is up
    assert t.is_connected() is True
    t._on_ib_error(-1, 1100, "connectivity between IB and TWS lost")
    assert t.is_connected() is False  # socket up but the feed is dead
    t._on_ib_error(-1, 1102, "connectivity restored, data maintained")
    assert t.is_connected() is True


def test_connect_rotates_client_id_across_retries_and_resets_on_resync() -> None:
    async def scenario() -> None:
        t = IBKRTransport(_settings(ibkr_client_id=1, ibkr_client_id_pool=4))
        used: list[int] = []

        async def fake_connect(host: str, port: int, *, clientId: int, timeout: float) -> None:
            used.append(clientId)  # simulate the supervisor retrying a still-held id

        async def fake_orders() -> list[object]:
            return []

        async def fake_positions() -> list[object]:
            return []

        t.ib.connectAsync = fake_connect  # type: ignore[method-assign]
        t.ib.reqAllOpenOrdersAsync = fake_orders  # type: ignore[method-assign]
        t.ib.reqPositionsAsync = fake_positions  # type: ignore[method-assign]

        await t.connect()
        await t.connect()
        await t.connect()
        assert used == [1, 2, 3]  # rotated to sidestep the held id
        await t.resync()  # a successful resync resets the rotation
        await t.connect()
        assert used[-1] == 1  # back to the base id for the next reconnect

    asyncio.run(scenario())


def test_connects_runs_on_connect_then_stops() -> None:
    async def scenario() -> None:
        t = FakeTransport()
        connected = asyncio.Event()
        calls = 0

        async def on_connect() -> None:
            nonlocal calls
            calls += 1
            connected.set()

        sup = ConnectionSupervisor(t, on_connect=on_connect, sleep=_instant_sleep)
        task = asyncio.create_task(sup.run())
        await asyncio.wait_for(connected.wait(), 1)
        sup.stop()
        await asyncio.wait_for(task, 1)

        assert calls == 1
        assert t.connect_calls == 1
        assert t.disconnect_calls == 1

    asyncio.run(scenario())


def test_stop_during_connect_does_not_hang() -> None:
    # If stop() fires while connect() is awaiting (the shutdown signal), it sets the disconnected
    # event — but run() clears that event right after connect, so without a re-check the following
    # wait() would block forever. run() must instead exit promptly.
    async def scenario() -> None:
        t = FakeTransport()
        sup = ConnectionSupervisor(t, sleep=_instant_sleep)
        orig_connect = t.connect

        async def connect_then_stop() -> None:
            await orig_connect()
            sup.stop()  # shutdown lands inside the connect() await

        t.connect = connect_then_stop  # type: ignore[method-assign]
        await asyncio.wait_for(sup.run(), 1)  # must not hang
        assert t.disconnect_calls >= 1  # cleaned up on exit

    asyncio.run(scenario())


def test_retries_with_backoff_until_connected() -> None:
    async def scenario() -> None:
        t = FakeTransport(fail_connects=2)
        delays: list[float] = []
        connected = asyncio.Event()

        async def record_sleep(d: float) -> None:
            delays.append(d)

        async def on_connect() -> None:
            connected.set()

        sup = ConnectionSupervisor(t, on_connect=on_connect, sleep=record_sleep)
        task = asyncio.create_task(sup.run())
        await asyncio.wait_for(connected.wait(), 1)
        sup.stop()
        await asyncio.wait_for(task, 1)

        assert t.connect_calls == 3  # 2 failures + 1 success
        assert delays == [1.0, 2.0]

    asyncio.run(scenario())


def test_reconnects_and_alerts_on_cold_disconnect() -> None:
    async def scenario() -> None:
        t = FakeTransport()
        connects = 0
        cold = 0
        first = asyncio.Event()
        second = asyncio.Event()

        async def on_connect() -> None:
            nonlocal connects
            connects += 1
            (first if connects == 1 else second).set()

        async def on_cold() -> None:
            nonlocal cold
            cold += 1

        sup = ConnectionSupervisor(
            t, on_connect=on_connect, on_cold_disconnect=on_cold, sleep=_instant_sleep
        )
        task = asyncio.create_task(sup.run())
        await asyncio.wait_for(first.wait(), 1)
        t.disconnected.set()  # simulate a connection drop
        await asyncio.wait_for(second.wait(), 1)
        sup.stop()
        await asyncio.wait_for(task, 1)

        assert connects == 2  # reconnected
        assert cold == 1  # cold disconnect alerted once

    asyncio.run(scenario())


def test_on_connect_failure_retries_not_fatal() -> None:
    async def scenario() -> None:
        t = FakeTransport()
        attempts = 0
        connected = asyncio.Event()

        async def on_connect() -> None:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("resync blew up")  # first resync fails
            connected.set()

        sup = ConnectionSupervisor(t, on_connect=on_connect, sleep=_instant_sleep)
        task = asyncio.create_task(sup.run())
        await asyncio.wait_for(connected.wait(), 1)  # supervisor survived and retried
        sup.stop()
        await asyncio.wait_for(task, 1)

        assert attempts == 2  # retried after the resync failure
        assert t.connect_calls == 2  # reconnected to retry the resync
        assert t.disconnect_calls >= 1  # half-initialised connection was dropped

    asyncio.run(scenario())


def test_expected_restart_does_not_alert() -> None:
    async def scenario() -> None:
        t = FakeTransport()
        cold = 0
        first = asyncio.Event()
        second = asyncio.Event()
        connects = 0

        async def on_connect() -> None:
            nonlocal connects
            connects += 1
            (first if connects == 1 else second).set()

        async def on_cold() -> None:
            nonlocal cold
            cold += 1

        sup = ConnectionSupervisor(
            t,
            on_connect=on_connect,
            on_cold_disconnect=on_cold,
            is_expected_restart=lambda: True,
            sleep=_instant_sleep,
        )
        task = asyncio.create_task(sup.run())
        await asyncio.wait_for(first.wait(), 1)
        t.disconnected.set()  # expected daily-restart drop
        await asyncio.wait_for(second.wait(), 1)
        sup.stop()
        await asyncio.wait_for(task, 1)

        assert connects == 2  # still reconnects
        assert cold == 0  # but no cold alert

    asyncio.run(scenario())


# --- #677: the reconnect path must be able to recover state, feeds and its own orders ------------


def test_resync_keeps_broker_state_instead_of_counting_it() -> None:
    """The defect: `resync` fetched orders/positions and logged only `len()` of each.

    That is a log line, not a reconciliation. After a crash between placing an order and persisting
    it locally, the app has no record, re-derives the same setup (the engine is compute-on-read) and
    places a SECOND entry. Keeping the state is what lets an OMS adopt instead of re-deriving.
    """

    class _Order:
        def __init__(self, ref: str) -> None:
            self.order = type("O", (), {"orderRef": ref})()

    async def scenario() -> None:
        t = IBKRTransport(_settings())
        t._client_id = 7

        async def fake_orders() -> list[object]:
            return [_Order("2026-08-08:CANF|entry"), _Order("2026-08-08:CANF|stop")]

        async def fake_positions() -> list[object]:
            return ["POS"]

        t.ib.reqAllOpenOrdersAsync = fake_orders  # type: ignore[method-assign]
        t.ib.reqPositionsAsync = fake_positions  # type: ignore[method-assign]
        await t.resync()

        assert len(t.broker_state.open_orders) == 2
        assert t.broker_state.positions == ("POS",)
        assert t.broker_state.synced_at_client_id == 7
        # Adoption is by BROKER-side identity — a local dict does not survive the crash.
        assert t.broker_state.order_refs == (
            "2026-08-08:CANF|entry",
            "2026-08-08:CANF|stop",
        )

    asyncio.run(scenario())


def test_resync_replays_registered_subscriptions() -> None:
    """A reconnect that silently drops a position's feed is the worst failure in the path.

    `is_connected()` stays true, no prices arrive, and an app-side stop can never fire — which looks
    exactly like a quiet tape. Nothing registers today, so this pins the seam before Gate 5.
    """

    async def scenario() -> None:
        t = IBKRTransport(_settings())
        replayed: list[str] = []

        async def fake_orders() -> list[object]:
            return []

        async def fake_positions() -> list[object]:
            return []

        t.ib.reqAllOpenOrdersAsync = fake_orders  # type: ignore[method-assign]
        t.ib.reqPositionsAsync = fake_positions  # type: ignore[method-assign]

        async def sub_a() -> None:
            replayed.append("a")

        async def boom() -> None:
            raise RuntimeError("stream gone")

        async def sub_c() -> None:
            replayed.append("c")

        t.register_subscription("a", sub_a)
        t.register_subscription("b", boom)
        t.register_subscription("c", sub_c)
        await t.resync()
        # One dead stream must not block the others — restoring 2 of 3 beats restoring none.
        assert replayed == ["a", "c"]

        t.unregister_subscription("b")
        replayed.clear()
        await t.resync()
        assert replayed == ["a", "c"]

    asyncio.run(scenario())


def test_registering_the_same_subscription_twice_does_not_duplicate_it() -> None:
    async def scenario() -> None:
        t = IBKRTransport(_settings())
        calls: list[int] = []

        async def fake_empty() -> list[object]:
            return []

        t.ib.reqAllOpenOrdersAsync = fake_empty  # type: ignore[method-assign]
        t.ib.reqPositionsAsync = fake_empty  # type: ignore[method-assign]

        async def first() -> None:
            calls.append(1)

        async def second() -> None:
            calls.append(2)

        t.register_subscription("SPY", first)
        t.register_subscription("SPY", second)  # re-arming the same stream replaces it
        await t.resync()
        assert calls == [2]

    asyncio.run(scenario())


def test_pinned_client_id_never_rotates() -> None:
    """IB scopes order events and cancellation rights to the PLACING client id.

    Rotating while an order is working orphans it: no `orderStatus` for our own stop, and no way to
    cancel it. Rotation is only safe while the app places no orders, which is why the pin ships off
    and must be flipped before Gate 6.
    """

    async def scenario() -> None:
        s = _settings(ibkr_client_id=1, ibkr_client_id_pool=4, ibkr_pin_client_id=True)
        t = IBKRTransport(s)
        used: list[int] = []

        async def fake_connect(host: str, port: int, *, clientId: int, timeout: float) -> None:
            used.append(clientId)

        t.ib.connectAsync = fake_connect  # type: ignore[method-assign]
        await t.connect()
        await t.connect()
        await t.connect()
        assert used == [1, 1, 1]  # never rotates, however many unclean disconnects

    asyncio.run(scenario())


def test_the_client_id_pin_ships_off_so_phase_1_reconnects_are_unchanged() -> None:
    """It is a Gate 6 precondition, not a Phase-1 change: rotation still sidesteps error 326."""
    assert _settings().ibkr_pin_client_id is False
