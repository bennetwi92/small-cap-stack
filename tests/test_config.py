"""Tests for settings loading."""

from __future__ import annotations

from datetime import time

import pytest

from tests.support import settings


def test_defaults() -> None:
    s = settings()
    assert s.ibkr_port == 4002
    assert s.ibkr_trading_mode == "paper"
    assert s.scan_start == time(4, 0)
    assert s.scan_end == time(11, 59)


def test_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    # 7496 is TWS *live*, paired with mode=live on purpose: since #663 the two are cross-checked,
    # so the old 7497 (TWS paper) + live combination is now a startup error rather than a valid
    # override. Same assertion, a pair that means something.
    monkeypatch.setenv("IBKR_PORT", "7496")
    monkeypatch.setenv("IBKR_TRADING_MODE", "live")
    monkeypatch.setenv("SCAN_START", "03:30")
    s = settings()
    assert s.ibkr_port == 7496
    assert s.ibkr_trading_mode == "live"
    assert s.scan_start == time(3, 30)


# --- paper-vs-live agreement (#663) -----------------------------------------------------


@pytest.mark.parametrize(
    ("port", "mode"),
    [
        (4002, "paper"),  # Gateway API
        (4001, "live"),
        (7497, "paper"),  # TWS API
        (7496, "live"),
        (4004, "paper"),  # socat, which is what the compose stack actually uses
        (4003, "live"),
    ],
)
def test_a_port_that_agrees_with_the_declared_mode_loads(port: int, mode: str) -> None:
    assert settings(ibkr_port=port, ibkr_trading_mode=mode).ibkr_port == port


@pytest.mark.parametrize(
    ("port", "mode"),
    [
        (4002, "live"),  # the dangerous direction: label says live, socket is paper
        (4001, "paper"),  # the *more* dangerous one: label says paper, socket is live
        (7497, "live"),
        (4003, "paper"),
    ],
)
def test_a_port_that_contradicts_the_declared_mode_refuses_to_start(port: int, mode: str) -> None:
    with pytest.raises(ValueError, match="contradicts"):
        settings(ibkr_port=port, ibkr_trading_mode=mode)


def test_an_unrecognised_port_is_left_alone() -> None:
    """A tunnel or a non-standard socat mapping must stay startable — guessing would break it.

    `test_settings_isolation` relies on this too: it asserts on `IBKR_PORT=9999`.
    """
    assert settings(ibkr_port=9999, ibkr_trading_mode="live").ibkr_port == 9999
    assert settings(ibkr_port=9999, ibkr_trading_mode="paper").ibkr_port == 9999


@pytest.mark.parametrize("mode", ["Paper", "LIVE", "paper ", "", "simulated"])
def test_a_trading_mode_outside_the_two_words_refuses_to_start(mode: str) -> None:
    """`ibkr_trading_mode` was a free string, so a typo read as neither and was never noticed."""
    with pytest.raises(ValueError, match="must be one of"):
        settings(ibkr_trading_mode=mode)
