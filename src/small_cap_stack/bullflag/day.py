"""Engine-v2 full-day detector (#211 stage 3) — the compute-on-read setup the trader would take.

See ``engine-v2.md §13``. Unlike the end-anchored :func:`.setup.detect_setup` (does a flag end at
the LAST bar?), :func:`detect_day` scans a WHOLE day of bars at once and returns the one setup a
trader would have taken, given the scanner-appearance time — matching "store raw, compute derived on
read". It is the port of the visual-review prototype (``spikes/viz_engine.py::pick_setup`` + the
exhaustion wiring), validated against 25 reviewed opportunities (#194).

Pipeline: a greedy H/E/L **cycle walk** (:func:`.cycles.segment_cycles`) proposes each candidate
pole; :func:`.segment.refine_pole` refines it (colour/thrust, red peak allowed); the **entry** is
the first ≥1-tick break of the last consolidation candle, gated by **appearance** (the entry bar
must open at/after ``first_hit``) and **staleness**; the shape is featured/gated/scored (the
``peak_green`` gate rejects a red peak); and **exhaustion** (:mod:`.cycles`) flags the 3rd+ cycle.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, time, timedelta

from ..capture import Bar
from ..config import Settings
from .cycles import prior_cycle_count, segment_cycles, significant_cycles
from .features import FeatureVector, extract, trailing_atr
from .gates import GateResult, evaluate
from .gates import passed as gates_passed
from .score import DEFAULT_WEIGHTS, score
from .segment import Segment, refine_pole
from .tokens import token_eps, tokenize


@dataclass(frozen=True)
class DaySetup:
    """The full-day detection result: the setup, its gates/score, and its exhaustion standing."""

    segment: Segment
    features: FeatureVector
    entry_trigger: float  # last consolidation high + trigger_offset (1 tick) — the mechanical fire
    entry_fill: float  # last consolidation high + fill_offset (3 ticks) — conservative R fill
    breakout_level: float  # high of the last consolidation candle (rounded 4)
    stop: float  # consolidation (flag) low (rounded 4)
    gates: tuple[GateResult, ...]  # includes peak_green (#196)
    passed: bool  # all gates passed (a shape-valid, quality setup) — NOT yet the take decision
    score: float  # 0..1 quality
    contributions: Mapping[str, float]  # per-feature score contribution (explainability)
    trigger_idx: int | None  # bar index of the breakout; None if it never fired or is stale
    cycle_num: int  # 1-based: 1 = a fresh move; N = the Nth contiguous pump of the day
    total_significant_cycles: int  # significant cycles across the whole day (context, not a gate)
    exhausted: bool  # cycle_num exceeds the exhaustion cap -> a late entry into a worn-out move

    @property
    def takeable(self) -> bool:
        """The trade decision: gates pass, an entry actually triggered, and it isn't exhausted."""
        return self.passed and self.trigger_idx is not None and not self.exhausted


def detect_day(
    bars: Sequence[Bar],
    *,
    first_hit: datetime | None = None,
    tick: float = 0.01,
    eps: float = 0.005,
    max_pole: int = 4,
    max_cons: int = 4,
    max_retracement: float = 0.50,
    max_peak_wick: float = 0.50,
    min_pole_pct: float = 0.02,
    trigger_offset: float = 0.01,
    fill_offset: float = 0.03,
    staleness_min: int = 30,
    cycle_min_volume: float = 50_000.0,
    exhaustion_cap: int = 2,
    atr_window: int = 14,
    weights: Mapping[str, float] = DEFAULT_WEIGHTS,
    window_start: time = time(4, 0),
    window_end: time = time(11, 59),
) -> DaySetup | None:
    """The setup the trader would take over ``bars`` (a whole day), or ``None`` if no pole forms.

    ``first_hit`` is the scanner-appearance datetime (``None`` disables the appearance/staleness
    gates). Defaults are the validated v2 values (caps 4/4, ``min_pole_pct`` 0.02, exhaust cap 2);
    :func:`detect_day_with_settings` maps the shared ones from ``Settings``. A shape that forms but
    fails a gate is still returned (``passed=False``) so callers can explain the rejection.
    """
    tokens = tokenize(bars, eps=eps)
    all_cycles = segment_cycles(tokens)

    # Greedy walk: each cycle proposes its peak; the entry is the first >= 1-tick break of the prior
    # bar's high after the peak (a price break, NOT the token breakout — an exactly-1-tick break is
    # an E within eps, #182: FWDI). The first cycle whose entry bar opens at/after first_hit wins
    # (appearance gates the ENTRY bar, not the peak — the pole can form before we saw the symbol;
    # #182: MSTZ). When an entry isn't takeable we fall through to the next cycle's later pole.
    chosen: tuple[int, int, int, int] | None = None  # (base, peak, cons_end, trigger)
    pole_len = 0
    for c in all_cycles:
        refined = refine_pole(bars, tokens, c.peak, max_pole=max_pole)
        if refined is None:
            continue  # no higher-high step into this peak
        trig = next(
            (j for j in range(c.peak + 1, len(bars)) if bars[j].high >= bars[j - 1].high + tick),
            None,
        )
        if trig is None or trig <= c.peak + 1:
            continue  # need >= 1 consolidation bar between the peak and the entry
        if first_hit is not None and bars[trig].start < first_hit:
            continue  # entry bar opened before/contains the appearance -> couldn't have taken it
        base, pole_len = refined
        chosen = (base, c.peak, trig - 1, trig)
        break
    if chosen is None:
        return None
    base, peak, cons_end, trigger_idx_opt = chosen
    trigger_idx: int | None = trigger_idx_opt

    seg = Segment(
        base_idx=base,
        peak_idx=peak,
        cons_end_idx=cons_end,
        tokens=tuple(tokens[base:cons_end]),
        pole_len=pole_len,
        cons_len=cons_end - peak,
    )
    fv = extract(
        bars,
        seg,
        atr=trailing_atr(bars, base, window=atr_window),
        window_start=window_start,
        window_end=window_end,
    )
    gates = evaluate(
        fv,
        max_pole=max_pole,
        max_cons=max_cons,
        max_peak_wick=max_peak_wick,
        min_pole_pct=min_pole_pct,
        max_retracement=max_retracement,
    )
    sc, contributions = score(
        fv, weights=weights, max_pole=max_pole, max_retracement=max_retracement
    )

    breakout = bars[cons_end].high
    stop = min(b.low for b in bars[peak + 1 : cons_end + 1])

    # Staleness (#130): a break too long after the appearance reads as faded (the "closed before
    # appearance" half is already covered — every bar after a visible peak is itself visible).
    if (
        trigger_idx is not None
        and first_hit is not None
        and bars[trigger_idx].start >= first_hit + timedelta(minutes=staleness_min)
    ):
        trigger_idx = None

    # Exhaustion (#102): contiguous prior significant cycles into this pole; reject the cap+1'th.
    sig = significant_cycles(bars, all_cycles, min_volume=cycle_min_volume)
    cycle_num = prior_cycle_count(bars, sig, base) + 1

    return DaySetup(
        segment=seg,
        features=fv,
        entry_trigger=round(breakout + trigger_offset, 4),
        entry_fill=round(breakout + fill_offset, 4),
        breakout_level=round(breakout, 4),
        stop=round(stop, 4),
        gates=gates,
        passed=gates_passed(gates),
        score=sc,
        contributions=contributions,
        trigger_idx=trigger_idx,
        cycle_num=cycle_num,
        total_significant_cycles=len(sig),
        exhausted=cycle_num > exhaustion_cap,
    )


def detect_day_with_settings(
    bars: Sequence[Bar], settings: Settings, first_hit: datetime | None
) -> DaySetup | None:
    """Settings-driven :func:`detect_day`. The v2 caps (``max_pole``/``max_cons`` 4, ``min_pole``
    0.02, exhaustion cap 2 via ``bull_flag_exhaustion_cap``) come from ``detect_day`` defaults
    — the legacy ``config`` values (8/6) and the legacy detector are untouched until the #180 flip;
    only the *shared* thresholds are read from ``settings``."""
    tick = settings.tick_size
    return detect_day(
        bars,
        first_hit=first_hit,
        tick=tick,
        eps=token_eps(settings),
        max_retracement=settings.bull_flag_max_retracement,
        max_peak_wick=settings.bull_flag_max_peak_wick,
        trigger_offset=settings.bull_flag_trigger_offset_ticks * tick,
        fill_offset=settings.bull_flag_fill_offset_ticks * tick,
        staleness_min=settings.entry_staleness_min,
        cycle_min_volume=settings.scan_min_5m_volume // 2,
        exhaustion_cap=settings.bull_flag_exhaustion_cap,
        window_start=settings.scan_start,
        window_end=settings.scan_end,
    )
