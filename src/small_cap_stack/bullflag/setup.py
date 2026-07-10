"""Stage 4 assembly + backward-compat shim (issue #179): the full engine-v2 detection result.

See ``engine-v2.md §7,§8``. :func:`detect_setup` runs the whole pipeline
(tokenise → segment → extract → gate → score) and returns a :class:`Setup` carrying the segment,
feature vector, entry/stop levels, gate results, and quality score. :meth:`Setup.as_bullflag`
projects it back to the legacy :class:`~.detect.BullFlag` so ``rmetrics`` / the review workbench can
consume v2 unchanged.

**Not yet wired in.** #179 builds this alongside the legacy detector and pins their equivalence with
a golden-parity test, but does **not** repoint ``detect_with_settings`` / ``rmetrics`` — the legacy
path stays active, so reported metrics don't move. The atomic switch (repoint + settings flip 8/6→
4/4, entry 5→3 ticks, ``min_pole_pct`` 2%) lands in #180, quantified by the #181 divergence spike.

A shape that segments but fails a gate is returned with ``passed=False`` (so the review page can
explain the rejection); ``detect_setup`` returns ``None`` only when no valid *shape* exists.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import time

from ..capture import Bar
from ..config import Settings
from .detect import BullFlag
from .features import FeatureVector, extract, trailing_atr
from .gates import GateResult, evaluate, passed
from .score import DEFAULT_WEIGHTS, score
from .segment import Segment, segment_at_end
from .tokens import tokenize


@dataclass(frozen=True)
class Setup:
    segment: Segment
    features: FeatureVector
    entry_trigger: float  # breakout_level + entry_offset (rounded 4)
    breakout_level: float  # high of the last consolidation candle (rounded 4)
    stop: float  # consolidation (flag) low (rounded 4)
    gates: tuple[GateResult, ...]
    passed: bool  # all gates passed -> a takeable setup
    score: float  # 0..1 quality; ranks passing setups (also populated for rejects, for review)
    contributions: Mapping[str, float]  # per-feature score contribution (explainability)

    def as_bullflag(self) -> BullFlag:
        """Project to the legacy BullFlag (field-for-field, same rounding) for rmetrics/review.

        Projects the levels regardless of ``passed`` (so the review page can show a rejected
        shape's would-be entry/stop). **Only emit it as a trade when ``self.passed``** — legacy
        ``detect()`` returns ``None`` on a gate failure, so the #180 wiring must use
        ``s = detect_setup(...); if s and s.passed: ... s.as_bullflag()``, not a bare ``if s:``.
        """
        return BullFlag(
            pole_len=self.segment.pole_len,
            flag_len=self.segment.cons_len,
            breakout_level=self.breakout_level,
            entry_trigger=self.entry_trigger,
            stop=self.stop,
            retracement=round(self.features.retracement, 4),
            cons_vol_reducing=self.features.cons_vol_reducing,
            pole_has_big_green=self.features.pole_has_big_green,
        )


def detect_setup(
    bars: Sequence[Bar],
    *,
    min_pole: int = 1,
    max_pole: int = 4,
    max_cons: int = 4,
    max_retracement: float = 0.50,
    max_peak_wick: float = 0.50,
    min_pole_pct: float = 0.02,
    atr_window: int = 14,
    entry_offset: float = 0.03,
    eps: float = 0.01,
    gate_window: bool = False,
    weights: Mapping[str, float] = DEFAULT_WEIGHTS,
    window_start: time = time(4, 0),
    window_end: time = time(11, 59),
) -> Setup | None:
    """Detect a bull flag at the END of the series -> the full Setup (or None if no valid shape)."""
    seg = segment_at_end(bars, tokenize(bars, eps=eps), max_pole=max_pole, max_cons=max_cons)
    if seg is None or seg.pole_len < min_pole:
        return None

    atr = trailing_atr(bars, seg.base_idx, window=atr_window)
    fv = extract(bars, seg, atr=atr, window_start=window_start, window_end=window_end)
    gates = evaluate(
        fv,
        max_pole=max_pole,
        max_cons=max_cons,
        max_peak_wick=max_peak_wick,
        min_pole_pct=min_pole_pct,
        max_retracement=max_retracement,
        gate_window=gate_window,
    )
    sc, contributions = score(
        fv, weights=weights, max_pole=max_pole, max_retracement=max_retracement
    )

    breakout = bars[seg.cons_end_idx].high
    stop = min(b.low for b in bars[seg.peak_idx + 1 : seg.cons_end_idx + 1])
    return Setup(
        segment=seg,
        features=fv,
        breakout_level=round(breakout, 4),
        entry_trigger=round(breakout + entry_offset, 4),
        stop=round(stop, 4),
        gates=gates,
        passed=passed(gates),
        score=sc,
        contributions=contributions,
    )


def detect_setup_with_settings(bars: Sequence[Bar], settings: Settings) -> Setup | None:
    """Settings-driven detect_setup. Reads the CURRENT (legacy) settings — new fields
    (``bull_flag_min_pole_pct`` / ``bull_flag_eps_ticks``) fall back to legacy-equivalent values
    until #180 adds them, so this reproduces the legacy detector **for shapes whose highs are
    clearly separated (steps > eps)**. The ``eps`` flatness tolerance (1 tick) is an intended v2
    refinement: a move within 1 tick is ``E`` (flat), so a 1-tick-only pole/flag differs from
    legacy's strict ``>`` — a deliberate noise filter (such poles fail ``min_pole_pct`` anyway),
    not a bug."""
    tick = settings.tick_size
    return detect_setup(
        bars,
        min_pole=settings.bull_flag_min_pole,
        max_pole=settings.bull_flag_max_pole,
        max_cons=settings.bull_flag_max_flag,
        max_retracement=settings.bull_flag_max_retracement,
        max_peak_wick=settings.bull_flag_max_peak_wick,
        min_pole_pct=getattr(settings, "bull_flag_min_pole_pct", 0.0),
        atr_window=getattr(settings, "bull_flag_atr_window", 14),
        entry_offset=settings.entry_offset_ticks * tick,
        eps=getattr(settings, "bull_flag_eps_ticks", 1) * tick,
        window_start=settings.scan_start,
        window_end=settings.scan_end,
    )
