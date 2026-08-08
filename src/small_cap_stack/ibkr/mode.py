"""Paper-vs-live agreement checks (#663).

`IBKR_TRADING_MODE` gates nothing on its own. Its only readers are a log line and `status.json`,
while the mode that *actually* applies is decided by **`IBKR_PORT`** and, independently, by the
Gateway container's own **`TRADING_MODE`**. Three settings have to agree and nothing reconciled
them, so `status.json` could report ``"paper"`` while the app was connected to a live account —
or the reverse, which is the direction that looks safe and is not.

Two checks close the gap, and they are deliberately different in kind:

* :func:`mode_for_port` is **static** and runs at settings load, so a contradiction is a startup
  failure rather than a surprise at 04:00. It only judges ports we actually publish; a tunnel or a
  non-standard socat mapping returns ``None`` and is left alone.
* :func:`mode_for_accounts` asks **the broker**, which is the only authoritative answer. IBKR paper
  logins return ``D``-prefixed account ids (``DU`` individual, ``DF`` advisor); live accounts do
  not. This is what catches the case the static check cannot see — the right port, pointed at a
  Gateway container whose own ``TRADING_MODE`` disagrees.

Neither raises from the connection path on purpose: a mode mismatch is a permanent
misconfiguration, and `ConnectionSupervisor` retries `on_connect` failures forever with backoff, so
raising would turn a config error into an unbounded warning loop. The startup check is the
enforcement point today. When the disarm flag lands (#674) a mismatch should also refuse to arm,
which is the enforcement point that actually matters once orders exist.
"""

from __future__ import annotations

from collections.abc import Sequence

PAPER = "paper"
LIVE = "live"
MODES = (PAPER, LIVE)

# Every port this project publishes a meaning for. Ports are the real switch — `IBKR_PORT` is what
# decides which Gateway socket we land on — so a disagreement here is a genuine contradiction and
# not a preference. In the docker-compose stack the app talks to **socat**, because the raw API
# binds localhost-only with `TrustedIPs=127.0.0.1` and drops a cross-container client (CLAUDE.md).
KNOWN_PORTS: dict[int, tuple[str, str]] = {
    4002: (PAPER, "IB Gateway API"),
    4001: (LIVE, "IB Gateway API"),
    7497: (PAPER, "TWS API"),
    7496: (LIVE, "TWS API"),
    4004: (PAPER, "socat -> IB Gateway"),
    4003: (LIVE, "socat -> IB Gateway"),
}


def mode_for_port(port: int) -> str | None:
    """The trading mode ``port`` implies, or ``None`` when the port carries no published meaning.

    ``None`` is not a failure: a tunnel, a test double or a non-standard socat mapping is allowed
    to be whatever it says it is. Guessing would make a legitimate setup unstartable.
    """
    known = KNOWN_PORTS.get(port)
    return known[0] if known else None


def port_description(port: int) -> str:
    """Label for ``port`` used in error text; ``"unrecognised"`` when we publish no meaning."""
    known = KNOWN_PORTS.get(port)
    return f"{known[1]} {known[0]}" if known else "unrecognised"


def mode_for_accounts(accounts: Sequence[str]) -> str | None:
    """The mode implied by IBKR's own account ids, or ``None`` when they cannot settle it.

    IBKR paper logins return ``D``-prefixed ids (``DU`` individual, ``DF`` advisor); live ones do
    not. ``None`` covers the two cases where there is nothing to conclude rather than something to
    complain about: **no accounts yet** (the handshake can report them late, and an empty list must
    never read as "live"), and a **mixed** set, which no single login should produce — flagging that
    as a mode would pick a side on evidence that contradicts itself.
    """
    ids = [a.strip().upper() for a in accounts if a and a.strip()]
    if not ids:
        return None
    paper = [a for a in ids if a.startswith("D")]
    if len(paper) == len(ids):
        return PAPER
    if not paper:
        return LIVE
    return None


def account_mismatch(declared: str, accounts: Sequence[str]) -> str | None:
    """Message describing a declared-vs-broker disagreement, or ``None`` when they agree.

    Returns a message rather than raising so the caller decides the consequence — the connection
    path logs it, and a future arming gate can refuse on it (#674).
    """
    actual = mode_for_accounts(accounts)
    if actual is None or actual == declared:
        return None
    return (
        f"IBKR_TRADING_MODE says {declared!r} but the connected account is {actual!r} "
        f"(accounts={sorted(a.strip().upper() for a in accounts if a and a.strip())}). "
        "The Gateway container's TRADING_MODE and IBKR_PORT decide the real mode; "
        "IBKR_TRADING_MODE only labels it."
    )
