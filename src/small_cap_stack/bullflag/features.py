"""Stage 3 of the engine-v2 pipeline (issue #178): extract the per-area feature vector.

See ``engine-v2.md §3,§6`` and ``bull-flag.md §3``. Given the bars and a :class:`.segment.Segment`,
compute a :class:`FeatureVector` covering the six areas (SHAPE / VOL / WICK / POLE / CONS / LOC).
Pure over ``bars[base_idx .. cons_end_idx]`` plus (for the ATR baseline) the bars before the base —
store-raw / compute-on-read, so features replay over history.

Anchors match the legacy detector — ``pole_base = bars[base_idx].low``,
``pole_high = bars[peak_idx].high``, ``cons_low = min(low over consolidation)`` — so retracement is
numerically identical for shapes both engines segment the same way. The pole is a run of strict
higher highs (no ``E``), so ``pole_span > 0`` always. Parity is scoped to poles whose steps clear
the ``eps`` (1-tick) tolerance: a near-1-tick step is ``E`` in v2 (an intended noise filter) but a
higher high to the legacy strict-``>`` walk. "Pole bars" span ``base_idx..peak_idx`` inclusive
(same slice legacy ``detect`` uses for ``pole_has_big_green``); "consolidation bars" span
``peak_idx+1..cons_end_idx``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import time

from ..capture import Bar, bar_interval
from ..clock import ET, within_window
from .primitives import classify, is_big_green, non_increasing, upper_wick_frac
from .segment import Segment

# A "doji"/small-bodied bar: body <= this fraction of its range. Used only by the
# WICK_cons_indecision score feature (ranking, not gating), so the threshold is refinable from data.
_DOJI_MAX_BODY_FRAC = 0.10
# Strategy window (ET); defaults mirror Settings.scan_start / scan_end. Overridable so extract stays
# self-contained and testable without a Settings.
_WINDOW_START = time(4, 0)
_WINDOW_END = time(11, 59)
_ATR_WINDOW = 14


@dataclass(frozen=True)
class FeatureVector:
    """The six feature areas of ``bull-flag.md §3`` for a segmented shape (engine-v2.md §3)."""

    # SHAPE
    pole_len: int
    cons_len: int
    cons_strictness: float  # frac of cons steps that are strict L (vs E); pole is all-H by rule
    token_string: str  # e.g. "HHLLL"
    # VOL
    peak_gt_cons: bool  # peak-bar vol > max(cons.vol)  [gate input] (the locked #127 rule)
    vol_ratio: float  # peak-bar vol / max(cons.vol)  (inf if cons has no volume)
    cons_vol_reducing: bool  # consolidation volume non-increasing
    pole_vol_concentration: float  # peak.vol / sum(thrust.vol)  (thrust = pole bars above launch)
    # WICK
    peak_upper_wick: float  # upper-wick frac of the peak bar  [gate input]
    peak_is_green: bool  # the peak bar closes green  [gate input] — the peak_green gate (#196)
    pole_has_big_green: bool  # a strong-bodied green candle in the pole
    pole_avg_body: float  # mean body fraction across pole bars
    cons_indecision: float  # frac of cons bars that are small-bodied / doji
    # POLE
    pole_height_pct: float  # (pole_high - pole_base) / pole_base  [gate input]
    pole_height_abs: float  # pole_high - pole_base (dollars)
    pole_velocity: float  # pole_height_pct / pole_len (per higher-high)
    pole_extension_atr: float | None  # pole_height_abs / trailing ATR (None if no baseline)
    # CONS
    retracement: float  # (pole_high - cons_low) / (pole_high - pole_base)  [gate input]
    holds_base: bool  # cons_low > pole_base  [gate input]
    cons_tightness: float  # (max cons high - min cons low) / pole_high
    cons_drift_slope: float  # per-step change in cons highs (<= 0 preferred)
    # LOC (recorded only this pass — scanner join lands in a later issue)
    trigger_in_window: bool  # detection time within the strategy window (ET)  [gate input]
    bars_before_scan: int | None  # None until the scanner_hits join lands


def _body_frac(bar: Bar) -> float:
    """Body as a fraction of the bar's range (0 = doji, 1 = marubozu). Zero-range bar -> 0."""
    rng = bar.high - bar.low
    if rng <= 0:
        return 0.0
    return abs(bar.close - bar.open) / rng


def _true_range(bar: Bar, prev_close: float | None) -> float:
    """Wilder true range: max(high-low, |high-prev_close|, |low-prev_close|)."""
    tr = bar.high - bar.low
    if prev_close is not None:
        tr = max(tr, abs(bar.high - prev_close), abs(bar.low - prev_close))
    return tr


def trailing_atr(bars: Sequence[Bar], base_idx: int, *, window: int = _ATR_WINDOW) -> float | None:
    """Mean true range over the ``window`` bars immediately before ``base_idx`` (the pole base).

    Returns ``None`` when there aren't ``window`` bars before the base — then ``pole_extension_atr``
    is ``None`` and simply doesn't contribute to the score (``bull-flag.md §3.4``).
    """
    if window <= 0 or base_idx < window:
        return None
    trs = [
        _true_range(bars[i], bars[i - 1].close if i - 1 >= 0 else None)
        for i in range(base_idx - window, base_idx)
    ]
    return sum(trs) / len(trs)


def extract(
    bars: Sequence[Bar],
    seg: Segment,
    *,
    atr: float | None = None,
    window_start: time = _WINDOW_START,
    window_end: time = _WINDOW_END,
) -> FeatureVector:
    """Compute the feature vector for a segmented shape.

    ``atr`` is the trailing baseline for ``pole_extension_atr`` (see :func:`trailing_atr`); pass
    ``None`` when unavailable. ``window_start``/``window_end`` are the strategy-window bounds (ET)
    for ``trigger_in_window``; defaults mirror ``Settings.scan_start``/``scan_end``.
    """
    base_idx, peak_idx, cons_end_idx = seg.base_idx, seg.peak_idx, seg.cons_end_idx
    pole = bars[base_idx : peak_idx + 1]  # base .. peak inclusive (matches legacy `pole`)
    thrust = bars[
        base_idx + 1 : peak_idx + 1
    ]  # the pole bars above the launch (may include E bars)
    cons = bars[peak_idx + 1 : cons_end_idx + 1]  # the consolidation (excludes the peak)
    n_pole_steps = peak_idx - base_idx  # number of pole tokens (>= pole_len >= 1)
    cons_tokens = seg.tokens[n_pole_steps:]

    pole_base = bars[base_idx].low
    pole_high = bars[peak_idx].high
    cons_low = min(b.low for b in cons)
    peak_vol = bars[peak_idx].volume
    cons_vmax = max(b.volume for b in cons)
    thrust_vsum = sum(
        b.volume for b in thrust
    )  # concentration denominator (thrust always non-empty)
    pole_span = pole_high - pole_base  # > 0: peak.high >= base.high > base.low
    pole_height_pct = pole_span / pole_base if pole_base > 0 else 0.0

    # VOL: the pole PEAK bar's volume vs the consolidation's max (the locked #127 rule, identical to
    # legacy detect.py:157). Deliberately the peak bar, not max-over-pole — a low-volume thrust must
    # not be rescued by a high-volume launch or earlier pole bar.
    vol_ratio = peak_vol / cons_vmax if cons_vmax > 0 else float("inf")

    cons_highs = [b.high for b in cons]
    cons_drift_slope = (
        (cons_highs[-1] - cons_highs[0]) / (len(cons_highs) - 1) if len(cons_highs) > 1 else 0.0
    )

    # trigger_in_window: the trigger (first H after the consolidation, bull-flag.md §4) lands on the
    # bar AFTER cons_end, so the earliest it can fire is the consolidation's close = the next bar's
    # open. Anchor there, not on cons_end's OPEN, else a flag completing at 11:55 reads in-window
    # when its 12:00 breakout is past the 11:59 close. Use the MODAL bar spacing (not the last gap),
    # so a missing/gapped bar before cons_end doesn't inflate the interval (#179 review).
    trigger_time = bars[cons_end_idx].start + bar_interval(bars)

    return FeatureVector(
        # SHAPE
        pole_len=seg.pole_len,
        cons_len=seg.cons_len,
        cons_strictness=cons_tokens.count("L") / seg.cons_len,
        token_string="".join(seg.tokens),
        # VOL
        peak_gt_cons=peak_vol > cons_vmax,
        vol_ratio=vol_ratio,
        cons_vol_reducing=non_increasing([b.volume for b in cons]),
        pole_vol_concentration=peak_vol / thrust_vsum if thrust_vsum > 0 else 0.0,
        # WICK
        peak_upper_wick=upper_wick_frac(bars[peak_idx]),
        peak_is_green=classify(bars[peak_idx]) == "green",
        pole_has_big_green=any(is_big_green(b) for b in pole),
        pole_avg_body=sum(_body_frac(b) for b in pole) / len(pole),
        cons_indecision=sum(_body_frac(b) <= _DOJI_MAX_BODY_FRAC for b in cons) / len(cons),
        # POLE
        pole_height_pct=pole_height_pct,
        pole_height_abs=pole_span,
        pole_velocity=pole_height_pct / seg.pole_len,
        pole_extension_atr=pole_span / atr if atr is not None and atr > 0 else None,
        # CONS
        retracement=(pole_high - cons_low) / pole_span,
        holds_base=cons_low > pole_base,
        cons_tightness=(max(b.high for b in cons) - cons_low) / pole_high,
        cons_drift_slope=cons_drift_slope,
        # LOC
        trigger_in_window=within_window(trigger_time.astimezone(ET), window_start, window_end),
        bars_before_scan=None,
    )
