"""Stage 4a of the engine-v2 pipeline (issue #179): the hard gates.

See ``engine-v2.md §7`` and ``bull-flag.md §5``. Each ``[gate input]`` feature becomes one
:class:`GateResult`; a setup is accepted iff every gate passes. Returning an *ordered* list of
results (not just a bool) lets the review page show **which** gate rejected a shape and by how much.

``loc_in_window`` is optional (``gate_window``): the legacy detector never gated on the trading
window — that lives in the pipeline's appearance gate (``gates.py`` at the package root, #122) — so
it is off by default to keep v2 behaviourally identical to legacy until the #180 cut-over decides.
"""

from __future__ import annotations

from dataclasses import dataclass

from .features import FeatureVector


@dataclass(frozen=True)
class GateResult:
    name: str
    passed: bool
    value: float | bool  # the measured feature, for the review page's explanation


def evaluate(
    fv: FeatureVector,
    *,
    max_pole: int,
    max_cons: int,
    max_peak_wick: float,
    min_pole_pct: float,
    max_retracement: float,
    gate_window: bool = False,
) -> tuple[GateResult, ...]:
    """Ordered gate results for a feature vector. ``all(g.passed for g in ...)`` = accepted.

    The ``pole_len``/``cons_len`` gates are redundant when called from ``detect_setup`` (the
    segmenter already caps both with the same params, so they always pass there) — they exist for
    callers that gate against *tighter* caps than segmentation used, e.g. the #181 divergence spike
    segmenting at 8/6 but gating at 4/4 to measure what the cap change removes.
    """
    gates = [
        GateResult("pole_len", fv.pole_len <= max_pole, fv.pole_len),
        GateResult("cons_len", fv.cons_len <= max_cons, fv.cons_len),
        GateResult("vol_peak_gt_cons", fv.peak_gt_cons, fv.vol_ratio),
        GateResult("wick_peak", fv.peak_upper_wick <= max_peak_wick, fv.peak_upper_wick),
        # "No red candle in the pole" as an identify-and-reject gate rather than a detection skip
        # (#196). refine_pole keeps a red/flat-peaked pole so the trader sees the setup they'd read;
        # here it fails instead. Intermediate pole bars are green (the thrust walk), so the peak is
        # the only bar that can be non-green. For the end-anchored segment_at_end, which already
        # requires a green peak, this gate always passes.
        GateResult("peak_green", fv.peak_is_green, fv.peak_is_green),
        GateResult("pole_height", fv.pole_height_pct >= min_pole_pct, fv.pole_height_pct),
        GateResult("cons_retracement", fv.retracement <= max_retracement, fv.retracement),
        GateResult("cons_holds_base", fv.holds_base, fv.holds_base),
    ]
    if gate_window:
        gates.append(GateResult("loc_in_window", fv.trigger_in_window, fv.trigger_in_window))
    return tuple(gates)


def passed(gates: tuple[GateResult, ...]) -> bool:
    """A setup is accepted iff every gate passed."""
    return all(g.passed for g in gates)
