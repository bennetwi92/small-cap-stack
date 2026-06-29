"""Tests for the IBKR connection layer (pure logic + supervisor via a fake transport)."""

from __future__ import annotations

import asyncio

import pytest

from small_cap_stack.config import Settings
from small_cap_stack.ibkr import (
    ConnAction,
    ConnectionSupervisor,
    RetryPolicy,
    SubscriptionRegistry,
    classify_connection_error,
)
from small_cap_stack.ibkr.transport import build_supervisor

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


# --- SubscriptionRegistry ---------------------------------------------------------------


def test_subscription_registry() -> None:
    r = SubscriptionRegistry()
    r.register("AAPL", {"conId": 1})
    r.register("MSFT", {"conId": 2})
    assert len(r) == 2
    assert "AAPL" in r
    r.unregister("AAPL")
    assert "AAPL" not in r
    assert r.all() == [("MSFT", {"conId": 2})]


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


def test_build_supervisor_wires_real_transport() -> None:
    # Constructs the ib_async-backed transport + supervisor offline (no connect).
    sup = build_supervisor(Settings(_env_file=None))  # type: ignore[call-arg]
    assert isinstance(sup, ConnectionSupervisor)


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
