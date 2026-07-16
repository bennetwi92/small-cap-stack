"""The adaptive layer: R-target optimiser and the risk throttle / kill-switch (#239).

Pure, replayable functions over a day's candidates. Split out of the old single-file
``portfolio.py`` (#259) with no behaviour change.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ..config import Settings
from .models import CandidateTrade


@dataclass(frozen=True)
class TargetStat:
    target_r: float
    breakeven_r: float
    n: int
    hit_rate: float | None  # fraction that reached the target
    expectancy_r: float | None  # mean realised R (the objective)


def expectancy_curve(
    candidates: Sequence[CandidateTrade],
    s: Settings,
    *,
    target_grid: Sequence[float],
    breakeven_r: float = 0.0,
) -> list[TargetStat]:
    """Per-target mean realised R over a set of trades — the input to the adaptive target choice.

    Expectancy is the *actual* mean realised R under the full exit model (breakeven, mark-to-close,
    costs excluded here — this is the pre-cost strategy edge), not the idealised ``p·T − (1−p)``, so
    breakeven and partial mark-to-close outcomes are captured exactly. Feed a trailing window of
    candidates to re-fit the target as the regime drifts."""
    stats: list[TargetStat] = []
    n = len(candidates)
    for t in target_grid:
        outcomes = [c.exit_under(s, t, breakeven_r) for c in candidates]
        if not outcomes:
            stats.append(TargetStat(t, breakeven_r, 0, None, None))
            continue
        hits = sum(1 for o in outcomes if o.reason == "target")
        exp = sum(o.realized_r for o in outcomes) / n
        stats.append(TargetStat(t, breakeven_r, n, round(hits / n, 4), round(exp, 4)))
    return stats


def best_target(stats: Sequence[TargetStat]) -> TargetStat | None:
    """Pick the highest-expectancy target with a defined expectancy (ties → the smaller target).

    Smaller-on-tie is the robust choice: a lower target with equal expectancy reaches it more often
    (higher hit rate → less variance), which matters on a tiny, all-in account."""
    scored = [st for st in stats if st.expectancy_r is not None]
    if not scored:
        return None
    return max(scored, key=lambda st: (st.expectancy_r or 0.0, -st.target_r))


def risk_ladder(s: Settings) -> tuple[float, ...]:
    """The risk-fraction rungs the kill-switch walks: 0 up to ``portfolio_risk_fraction``, evenly.

    ``portfolio_risk_rungs`` rungs *including* the 0 floor, so 3 → ``(0.0, 0.025, 0.05)`` at the 5%
    default. A single rung disables the throttle (always full risk). Fewer rungs ⇒ a faster wind-up
    back to full risk after a knock-down, which is the point of keeping the ladder coarse."""
    n = max(1, s.portfolio_risk_rungs)
    top = s.portfolio_risk_fraction
    if n == 1:
        return (top,)
    return tuple(round(top * i / (n - 1), 6) for i in range(n))


def step_risk_rung(
    rung: int, streak: int, day_signal: float, n_rungs: int, step_days: int
) -> tuple[int, int]:
    """Advance the ``(rung, streak)`` kill-switch state by one day — one rung per ``step_days`` run.

    ``streak`` is a signed count of consecutive *decisive* days in the current direction (positive =
    net-positive days, negative = net-negative days). A net-positive day extends or flips it up, a
    net-negative day extends or flips it down, and a **flat / no-setup day holds both rung and
    streak** — an information-less day carries no momentum, so "in a row" counts decisive days
    across flat gaps. Once the streak reaches ``±step_days`` the rung steps one notch that way
    (clamped to ``[0, n_rungs - 1]``) and the streak resets to 0, so each further move needs a fresh
    run. ``step_days=1`` steps on every decisive day (eager). ``day_signal`` is size-independent
    (see :func:`_day_signal_r`), so a book parked at rung 0 still climbs once setups work again."""
    if day_signal > 0:
        streak = streak + 1 if streak > 0 else 1  # extend or flip to a winning run
    elif day_signal < 0:
        streak = streak - 1 if streak < 0 else -1  # extend or flip to a losing run
    else:
        return rung, streak  # flat day: hold the rung AND the streak
    if streak >= step_days:
        return min(n_rungs - 1, rung + 1), 0
    if streak <= -step_days:
        return max(0, rung - 1), 0
    return rung, streak


def _day_signal_r(
    taken: Sequence[CandidateTrade], s: Settings, target_r: float, breakeven_r: float
) -> float:
    """Aggregate realised R of a day's taken setups under ``(target_r, breakeven_r)``.

    The throttle's day result: size-independent (pure R), so it is defined even on days the book
    took no positions (the 0 rung), which is exactly what lets the kill-switch re-arm."""
    return round(sum(c.exit_under(s, target_r, breakeven_r).realized_r for c in taken), 4)
