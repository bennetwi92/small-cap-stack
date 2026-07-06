"""Routing for IBKR connectivity error codes (1100/1101/1102)."""

from __future__ import annotations

from enum import Enum, auto


class ConnAction(Enum):
    """What the supervisor should do in response to a connectivity error code."""

    CONNECTIVITY_LOST = auto()  # 1100: TWS/Gateway <-> IBKR link down
    RESUBSCRIBE = auto()  # 1101: link restored, subscriptions were lost (Phase-1: none to replay)
    DATA_OK = auto()  # 1102: link restored, subscriptions maintained
    IGNORE = auto()  # anything else


_MAP = {
    1100: ConnAction.CONNECTIVITY_LOST,
    1101: ConnAction.RESUBSCRIBE,
    1102: ConnAction.DATA_OK,
}


def classify_connection_error(code: int) -> ConnAction:
    """Map an IBKR error code to a connectivity action."""
    return _MAP.get(code, ConnAction.IGNORE)
