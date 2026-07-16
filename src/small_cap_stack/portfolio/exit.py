"""Exit simulation — the tested core of the virtual book (#230).

Walks a filled trade to its fixed-R target / stop / breakeven / close. Split out of the old
single-file ``portfolio.py`` (#259) with no behaviour change.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ..capture import Bar


@dataclass(frozen=True)
class ExitOutcome:
    """The result of walking a filled trade to its exit under a fixed target + breakeven."""

    realized_r: float
    reason: str  # "target" | "stop" | "breakeven" | "close"
    exit_index: int
    exit_price: float


def simulate_exit(
    bars: Sequence[Bar],
    entry_price: float,
    stop: float,
    entry_index: int,
    *,
    target_r: float,
    breakeven_r: float = 0.0,
    tick_size: float = 0.01,
    exit_slippage_ticks: int = 0,
) -> ExitOutcome:
    """Walk a filled trade from its entry bar to a fixed-R target / stop / breakeven / close.

    Conventions mirror :func:`rmetrics._measure`: **stop-first** intrabar (a bar that breaches the
    active stop closes the trade before any favourable excursion is credited) and **gap-through** on
    the stop (a bar that opens below the stop fills at its open, not the stop). The target is a
    resting limit — filled at exactly ``target_price`` even on a gap-up over it (conservative:
    never credit the extra). Breakeven arms *after* the bar whose high reaches ``breakeven_r`` (no
    intrabar look-ahead) and moves the stop to ``entry_price``. ``exit_slippage_ticks`` widens
    stop/close fills (a limit target never slips). ``risk = entry_price - stop`` must be positive.
    """
    risk = entry_price - stop
    if risk <= 0:  # caller guarantees a valid setup; guard defensively
        raise ValueError("simulate_exit requires entry_price > stop (positive risk)")
    target_price = entry_price + target_r * risk
    be_arm_price = entry_price + breakeven_r * risk if breakeven_r > 0 else None
    slip = exit_slippage_ticks * tick_size
    active_stop = stop
    armed = False

    for k in range(entry_index, len(bars)):
        b = bars[k]
        if b.low <= active_stop:  # stop-first: breach closes the trade
            exit_price = (
                min(active_stop, b.open) - slip
            )  # gap-through: open-fill if it gapped below
            reason = "breakeven" if armed else "stop"
            return ExitOutcome(
                round((exit_price - entry_price) / risk, 4), reason, k, round(exit_price, 4)
            )
        if b.high >= target_price:  # resting limit fills at the target
            return ExitOutcome(target_r, "target", k, round(target_price, 4))
        if be_arm_price is not None and not armed and b.high >= be_arm_price:
            armed = True  # protect from the NEXT bar on (no same-bar look-ahead)
            active_stop = entry_price

    last = bars[-1]  # never resolved intraday -> mark to close at the final bar
    exit_price = last.close - slip
    return ExitOutcome(
        round((exit_price - entry_price) / risk, 4), "close", len(bars) - 1, round(exit_price, 4)
    )
