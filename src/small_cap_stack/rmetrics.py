"""R-multiple measurement (issue #18) — engine-v2 (#180 cutover to detect_day).

Phase-1 places no orders — but to learn the strategy we measure, per opportunity, what *would*
have happened. The engine-v2 full-day detector (:func:`bullflag.detect_day`) picks the one
appearance-anchored setup a trader would take (greedy cycle walk, colour/thrust pole, gates,
exhaustion) and the bar its entry triggers; this module measures the trade from there:

- **entry for R** is the conservative 3-tick ``entry_fill`` (not the 1-tick mechanical trigger) —
  R is deliberately measured against a worse fill so Phase-1 never overstates the edge;
- **risk** R = entry - stop (stop = consolidation low), with a gap-through fill: if the trigger bar
  *opened* above the fill, the entry (and realised risk) widen to that open (#163);
- **Max R** (peak favourable excursion) and **MAE** (worst adverse), under a conservative
  **stop-first** intrabar convention: if a bar breaches the stop we treat the trade closed at the
  stop on that bar — its high is not credited and no later bar is measured.

R is measured for **every** setup that triggers, even one the engine rejects (a gate failure or an
exhausted cycle): ``triggered`` records the fire, ``takeable`` whether it also passed the gates and
wasn't exhausted, and ``failing_gates`` / ``exhausted`` / ``cycle_num`` the reason — so the review
page can show "rejected as exhausted, but would have been +2R" (a Phase-1 learning signal).

Appearance and staleness gating live inside ``detect_day`` (bar-*start* granularity: the entry bar
must open at/after ``first_hit``; a break more than ``entry_staleness_min`` after it reads as faded
and yields setup-found-but-not-triggered). Pure and replayable over the cached raw bars.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from .bullflag import detect_day_with_settings
from .capture import Bar
from .config import Settings


def bar_interval(bars: Sequence[Bar]) -> timedelta:
    """The modal spacing between consecutive bar starts — the bar duration (usually 5 min).

    Taken from the *most common* gap so a pre-market hole doesn't inflate it. Defaults to 5 minutes
    when there aren't two bars to measure. (Kept for callers that reason about bar close times.)"""
    if len(bars) < 2:
        return timedelta(minutes=5)
    gaps = [bars[i].start - bars[i - 1].start for i in range(1, len(bars))]
    return Counter(gaps).most_common(1)[0][0]


@dataclass(frozen=True)
class RMetrics:
    setup_found: bool
    triggered: bool = False  # a setup fired (reached the entry) — regardless of the reject verdict
    takeable: bool = False  # fired AND passed all gates AND not exhausted (the trade we'd take)
    entry_trigger: float | None = None  # the +1-tick mechanical trigger level
    entry_fill: float | None = None  # the +3-tick conservative fill (R is measured against this)
    stop: float | None = None
    initial_risk: float | None = None
    entry_price: float | None = None  # the realised fill (>= entry_fill on a gap-through)
    entry_index: int | None = None
    max_r: float | None = None  # peak favourable excursion, in R
    mae_r: float | None = None  # worst adverse excursion after entry, in R
    stopped_out: bool = False
    stop_index: int | None = None
    bars_to_max_r: int | None = None
    flag_len: int | None = None  # consolidation count of the traded setup
    retracement: float | None = None  # flag's retracement into the pole, fraction
    pole_len: int | None = None  # number of higher highs in the pole
    cons_vol_reducing: bool | None = None  # consolidation volume non-increasing (soft signal)
    pole_has_big_green: bool | None = None  # pole holds a strong-bodied green candle (soft)
    # engine-v2 (#180): the reject verdict and its reason, surfaced for Phase-1 learning.
    cycle_num: int | None = None  # 1 = fresh; N = Nth contiguous pump of the day
    exhausted: bool = False  # cycle_num over the exhaustion cap (a late entry into a worn move)
    passed: bool | None = None  # all gates passed (shape quality)
    failing_gates: tuple[str, ...] = ()  # names of the gates that rejected the shape
    score: float | None = None  # 0..1 quality score


def _measure(
    bars: Sequence[Bar], entry_level: float, stop: float, entry_j: int
) -> dict[str, object]:
    """Track a filled trade from its entry bar: Max R, MAE, stop-out (stop-first, gap-through)."""
    bar = bars[entry_j]
    entry = max(entry_level, bar.open)  # gap-through: fill no better than the open
    risk = round(entry - stop, 6)
    min_low = bar.low
    bars_to_max_r = 0
    stopped_out = False
    stop_index: int | None = None
    if bar.low <= stop:  # same-bar trigger+stop -> stop-first credits no favourable excursion
        max_high = entry
        stopped_out = True
        stop_index = entry_j
    else:
        max_high = bar.high
        for k in range(entry_j + 1, len(bars)):
            b = bars[k]
            if b.low <= stop:  # check the stop first (conservative intrabar ordering)
                min_low = min(min_low, b.low)
                stopped_out = True
                stop_index = k
                break
            if b.high > max_high:
                max_high = b.high
                bars_to_max_r = k - entry_j
            min_low = min(min_low, b.low)
    return {
        "entry_price": entry,
        "entry_index": entry_j,
        "initial_risk": risk,
        "max_r": round((max_high - entry) / risk, 3),
        "mae_r": round((entry - min_low) / risk, 3),
        "stopped_out": stopped_out,
        "stop_index": stop_index,
        "bars_to_max_r": bars_to_max_r,
    }


def compute_r_metrics(
    bars: Sequence[Bar], settings: Settings, *, first_hit: datetime | None = None
) -> RMetrics:
    """Measure the notional trade for a day's ``bars`` via ``detect_day`` (see the module doc).

    ``bars`` is the whole trading day (engine-v2 counts exhaustion across it); ``first_hit`` is the
    run's scanner appearance (gates the entry). Returns ``setup_found=False`` when no pole forms.
    """
    setup = detect_day_with_settings(list(bars), settings, first_hit)
    if setup is None:
        return RMetrics(setup_found=False)
    seg, fv = setup.segment, setup.features
    shape: dict[str, object] = {
        "entry_trigger": setup.entry_trigger,
        "entry_fill": setup.entry_fill,
        "stop": setup.stop,
        "flag_len": seg.cons_len,
        "retracement": round(fv.retracement, 4),
        "pole_len": seg.pole_len,
        "cons_vol_reducing": fv.cons_vol_reducing,
        "pole_has_big_green": fv.pole_has_big_green,
        "cycle_num": setup.cycle_num,
        "exhausted": setup.exhausted,
        "passed": setup.passed,
        "failing_gates": tuple(g.name for g in setup.gates if not g.passed),
        "score": round(setup.score, 4),
    }
    planned_risk = round(setup.entry_fill - setup.stop, 6)
    if setup.trigger_idx is None or planned_risk <= 0:
        # formed but never a takeable trigger (never fired, stale, or non-positive risk)
        return RMetrics(
            setup_found=True,
            triggered=False,
            initial_risk=planned_risk if planned_risk > 0 else None,
            **shape,  # type: ignore[arg-type]
        )
    m = _measure(bars, setup.entry_fill, setup.stop, setup.trigger_idx)
    return RMetrics(
        setup_found=True,
        triggered=True,
        takeable=setup.passed and not setup.exhausted,
        **shape,  # type: ignore[arg-type]
        **m,  # type: ignore[arg-type]
    )
