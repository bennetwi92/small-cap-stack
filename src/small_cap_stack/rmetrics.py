"""R-multiple measurement (issue #18) — engine-v2 (#180 cutover to detect_day).

Phase-1 places no orders — but to learn the strategy we measure, per opportunity, what *would*
have happened. The engine-v2 full-day detector (:func:`bullflag.detect_day`) picks the one
appearance-anchored setup a trader would take (greedy cycle walk, colour/thrust pole, gates,
exhaustion) and the bar its entry triggers; this module measures the trade from there:

- **entry for R** is the conservative 3-tick ``entry_fill`` (not the 1-tick mechanical trigger) —
  R is deliberately measured against a worse fill so Phase-1 never overstates the edge;
- **risk** R = entry - stop (stop = consolidation low), with a gap-through fill: if the trigger bar
  *opened* above the fill, the entry (and realised risk) widen to that open (#163);
- **Max R** (peak favourable excursion — also reported as ``max_gain_pct``, the same peak as a
  plain fraction of the entry price) and **MAE** (worst adverse), under a conservative
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

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from .bullflag import detect_day_with_settings
from .capture import Bar
from .config import Settings


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
    # The same peak expressed as a fraction of the entry price — R normalises by the stop distance,
    # so a 0.9R move on a wide stop and a 0.9R move on a tight one read identically while being very
    # different moves in the tape. This is the size-of-the-move view of Max R (#390).
    max_gain_pct: float | None = None
    mae_r: float | None = None  # worst adverse excursion after entry, in R
    stopped_out: bool = False
    stop_index: int | None = None
    # The realised fill landed above the entry bar's own high — a price that never printed on this
    # bar (#555, #581). Distinct from `same_bar_stop` below and easy to confuse with it: this one is
    # about the ENTRY being fictional, not about the ORDER of entry and stop. 31 of the 91 same-bar
    # cases in the recon record carry it, so the review page must be able to tell them apart.
    fill_above_entry_bar_high: bool = False
    # Data quality of the traded setup (#604), carried so a suspect trade is visible rather than
    # silently averaged into a win rate. `halted_consolidation` says a consolidation bar recorded
    # zero trades while a neighbour cleared the volume floor — the published entry/stop are then
    # prices the tape was halted through. See `DaySetup`.
    untraded_cons_bars: int = 0
    halted_consolidation: bool = False
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

    @property
    def same_bar_stop(self) -> bool:
        """Entry and stop landed on the SAME bar — so this trade's R is an assumption (#581).

        ``_measure`` cannot see intrabar order, so when a 5-min bar contains both the trigger and
        the stop it books the conservative reading: stopped on entry, zero favourable excursion,
        no later bar measured. That is the right *default*, but it is not a measurement, and a
        ``-1R`` that is really a coin-flip must be able to say so.

        Re-resolving all 91 such cases in the recon store against its 1-min bars (#583) found the
        conservative reading **wrong 38%** of the time (35 ran, 31 genuinely stopped inside the
        bar, 16 irreducibly ambiguous within a single minute, 9 without minute data). Publishing
        the flag is what lets the review page and the book mark those rows as unresolved rather
        than quietly averaging them into a win rate.
        """
        return (
            self.stopped_out and self.stop_index is not None and self.stop_index == self.entry_index
        )


def _measure(
    bars: Sequence[Bar], entry_level: float, stop: float, entry_j: int
) -> dict[str, object]:
    """Track a filled trade from its entry bar: Max R, MAE, stop-out (stop-first, gap-through)."""
    bar = bars[entry_j]
    # NOTE (#555): `bar.high` is deliberately NOT checked against `entry_level`. The trigger fires
    # on a 1-tick break while `entry_level` is the 3-tick fill, so when the bar's high lands between
    # the two this books a fill above the bar's whole range — a price that never printed. Kept: a
    # higher entry means wider risk, a smaller position and a worse R, so it can only understate the
    # edge, which is the point of the conservative fill. Documented in research/strategy.md.
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
        "max_gain_pct": round((max_high - entry) / entry, 5),
        "mae_r": round((entry - min_low) / risk, 3),
        "stopped_out": stopped_out,
        "stop_index": stop_index,
        "bars_to_max_r": bars_to_max_r,
        # See the NOTE above: `entry` is deliberately not clamped to the bar's range, so it can sit
        # above a high that never printed. Record when it did rather than leaving it inferable.
        "fill_above_entry_bar_high": entry > bar.high,
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
        "untraded_cons_bars": setup.untraded_cons_bars,
        "halted_consolidation": setup.halted_consolidation,
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
        # One definition of takeable, not two (#555). This branch already implies a trigger, so the
        # old inline `passed and not exhausted` agreed with `DaySetup.takeable` in every case that
        # reaches here — but it was a second copy of the system's most important boolean, and
        # `charts.py` published the other one. They diverged when `entry_fill - stop <= 0`.
        takeable=setup.takeable,
        **shape,  # type: ignore[arg-type]
        **m,  # type: ignore[arg-type]
    )


# --- #583: resolving a same-bar entry+stop against a finer grid ---------------------------------
#
# `_measure` above is pure over ONE bar grid and the 25 golden fixtures are written against it, so
# the finer-grid question lives here as a separate pass rather than inside it. A second grid is a
# second question.


@dataclass(frozen=True)
class EntryResolution:
    """What a finer bar grid says about a 5-min bar that held both the trigger and the stop.

    ``outcome`` is one of:

    - ``"ran"`` — the trigger printed and the stop was NOT touched again before the bar closed. The
      only outcome that changes anything: the conservative reading was wrong.
    - ``"confirmed_stop"`` — the trigger printed, then the stop was taken later inside the same bar.
      The conservative reading stands, now *evidenced* rather than assumed.
    - ``"ambiguous_same_minute"`` — the filling minute's own low breached the stop. Irreducible at
      this resolution; the conservative reading stands.
    - ``"unresolved"`` — no finer bars, or the trigger never printed in them. Nothing to say.

    **The trigger decides whether we are in, not the fill.** ``entry_fill`` is a deliberately
    pessimistic *price* (breakout + 3 ticks); a marketable limit at the 1-tick trigger fills at or
    better than it. Keying the search on the fill printing would call a real fire a non-event —
    BIYA 2026-05-22 triggers at 08:57 on a 1.14 high while the 1.15 fill does not print until 09:04.
    """

    outcome: str
    entry_at: datetime | None = None  # start of the minute that filled
    entry_price: float | None = None  # the pessimistic fill, or the minute's open if it gapped past
    stopped_at: datetime | None = None  # minute the stop was taken (``confirmed_stop`` only)
    synthetic_bar: Bar | None = None  # ``ran`` only: the entry bar re-cut from the filling minute

    @property
    def ran(self) -> bool:
        return self.outcome == "ran"


def resolve_entry_bar(
    minute_bars: Sequence[Bar],
    *,
    entry_trigger: float,
    entry_fill: float,
    stop: float,
    bar_start: datetime,
    bar_end: datetime,
) -> EntryResolution:
    """Resolve the order of entry and stop inside ``[bar_start, bar_end)`` using finer bars.

    Stop-first is preserved at the finer resolution — this narrows the ambiguous window, it does not
    remove it. A minute that spans both levels stays ``ambiguous_same_minute`` and keeps the
    conservative answer; only tick data could settle those.
    """
    mins = sorted((b for b in minute_bars if bar_start <= b.start < bar_end), key=lambda b: b.start)
    fill_i = next((i for i, b in enumerate(mins) if b.high >= entry_trigger), None)
    if fill_i is None:
        return EntryResolution("unresolved")

    filled = mins[fill_i]
    entry = max(entry_fill, filled.open)  # same gap-through rule `_measure` applies
    if filled.low <= stop:
        return EntryResolution("ambiguous_same_minute", entry_at=filled.start, entry_price=entry)

    rest = mins[fill_i:]
    hit = next((b for b in rest[1:] if b.low <= stop), None)
    if hit is not None:
        return EntryResolution(
            "confirmed_stop", entry_at=filled.start, entry_price=entry, stopped_at=hit.start
        )

    # Ran. Re-cut the 5-min entry bar to span only the part of it we were actually in, opening at
    # the realised fill so `_measure`'s `max(entry_level, bar.open)` reproduces that same entry.
    return EntryResolution(
        "ran",
        entry_at=filled.start,
        entry_price=entry,
        synthetic_bar=Bar(
            start=bar_start,
            open=entry,
            high=max(b.high for b in rest),
            low=min(b.low for b in rest),
            close=rest[-1].close,
            volume=sum(b.volume for b in rest),
        ),
    )


def measure_resolved_trade(
    bars: Sequence[Bar], *, entry_fill: float, stop: float, entry_index: int
) -> dict[str, object]:
    """Public seam onto the same measurement :func:`compute_r_metrics` runs internally.

    Exists so :mod:`portfolio.extract` can re-measure a trade whose entry bar was re-cut by
    :func:`resolve_entry_bar` without re-running detection (the setup is unchanged; only the bar we
    were actually in has moved) and without reaching for a private name.
    """
    return _measure(bars, entry_fill, stop, entry_index)
