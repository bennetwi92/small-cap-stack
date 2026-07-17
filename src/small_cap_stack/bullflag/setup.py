"""Stage 4 assembly (issue #179): the full engine-v2 detection result.

See ``engine-v2.md §7,§8``. :func:`detect_setup` runs the whole pipeline
(tokenise → segment → extract → gate → score) and returns a :class:`Setup` carrying the segment,
feature vector, entry/stop levels, gate results, and quality score.

A shape that segments but fails a gate is returned with ``passed=False`` (so the review page can
explain the rejection); ``detect_setup`` returns ``None`` only when no valid *shape* exists.

Consumers take a trade only when ``passed``: ``s = detect_setup(...); if s and s.passed: ...``,
not a bare ``if s:``. R is measured against ``entry_fill`` (the conservative 3-tick fill), not
``entry_trigger`` (the 1-tick mechanical trigger) — the trigger decides *when* a setup fires
(#182/#190).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import time

from ..capture import Bar
from ..config import Settings
from .features import FeatureVector, extract, trailing_atr
from .gates import GateResult, evaluate, passed
from .score import DEFAULT_WEIGHTS, score
from .segment import Segment, segment_at_end
from .tokens import tokenize


@dataclass(frozen=True)
class Setup:
    segment: Segment
    features: FeatureVector
    entry_trigger: float  # breakout_level + entry_offset (rounded 4) — validated 1 tick, #182/#190
    entry_fill: float  # breakout_level + fill_offset (rounded 4) — conservative R fill, 3 ticks,
    # #182/#190: the trigger decides WHEN a setup fires; R is measured against this worse, more
    # conservative fill ("often I fill at the trigger price anyway, 3 ticks is being conservative").
    breakout_level: float  # high of the last consolidation candle (rounded 4)
    stop: float  # consolidation (flag) low (rounded 4)
    gates: tuple[GateResult, ...]
    passed: bool  # all gates passed -> a takeable setup
    score: float  # 0..1 quality; ranks passing setups (also populated for rejects, for review)
    contributions: Mapping[str, float]  # per-feature score contribution (explainability)


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
    entry_offset: float = 0.01,
    fill_offset: float = 0.03,
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
        entry_fill=round(breakout + fill_offset, 4),
        stop=round(stop, 4),
        gates=gates,
        passed=passed(gates),
        score=sc,
        contributions=contributions,
    )


def detect_setup_with_settings(bars: Sequence[Bar], settings: Settings) -> Setup | None:
    """Settings-driven :func:`detect_setup` — the END-anchored detector ("does a flag end at the
    last bar?").

    ⚠️ **Not the live path, and not aligned with it.** Production reads the full-day detector
    (:func:`.day.detect_day_with_settings`, consumed by ``rmetrics`` / ``charts``). This function
    has no production caller; it survives for tests and ad-hoc replay.

    It also does **not** run the locked v2 params. It reads the stale legacy caps
    (``bull_flag_max_pole`` 8 / ``bull_flag_max_flag`` 6) and its ``min_pole_pct`` /
    ``atr_window`` ``getattr`` fallbacks name ``Settings`` fields that **do not exist** — so the
    2% pole floor silently evaluates at ``0.0`` (off). Those fallbacks were placed for the #180
    settings flip, which never landed (**#302**). Until #302 resolves, this and the live path
    disagree about the caps.

    The entry TRIGGER uses ``bull_flag_trigger_offset_ticks`` (1 tick, validated via visual review,
    #182/#190); the FILL used for R uses ``bull_flag_fill_offset_ticks`` (3 ticks, conservative
    slippage, confirmed by the trader). Both are v2-only concepts.
    """
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
        entry_offset=settings.bull_flag_trigger_offset_ticks * tick,  # v2-only, no legacy fallback
        fill_offset=settings.bull_flag_fill_offset_ticks * tick,  # v2-only, no legacy fallback
        eps=getattr(settings, "bull_flag_eps_ticks", 1) * tick,
        window_start=settings.scan_start,
        window_end=settings.scan_end,
    )
