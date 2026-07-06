"""IBKR connection layer (issue #11).

A thin reconnect-and-resync supervisor over the Dockerised IB Gateway. IBC + the Gateway
container own login, the daily restart, and 2FA policy; this package owns the in-process
concerns: reconnect/backoff, on-connect resync, error-code routing, and cold-restart alerting.

`ib_async.Watchdog` is deliberately not used — it can only manage a Gateway it launches itself,
not a separate container, and never resyncs orders.
"""

from __future__ import annotations

from .errors import ConnAction, classify_connection_error
from .retry import RetryPolicy
from .supervisor import ConnectionSupervisor, Transport

__all__ = [
    "ConnAction",
    "ConnectionSupervisor",
    "RetryPolicy",
    "Transport",
    "classify_connection_error",
]
