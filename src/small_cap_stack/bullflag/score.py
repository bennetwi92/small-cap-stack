"""Stage 4b of the engine-v2 pipeline (issue #179): the 0–1 quality score.

See ``research/engine-v2.md §7`` and ``research/bull-flag.md §5``. A straw-man weighted sum
of the ``score`` features,
each normalised to ``[0, 1]``, used to **rank** setups that pass the gates (not to reject). The
weights are hand-set intuition, to be **fit later** against the review workbench's corrected-outcome
Max R. :func:`score` returns the total plus a ``contributions`` map (``weight * normalised`` per
feature, which sum to the score) so a ranking is auditable on the review page, not a black box.

Higher is better for every normalised term: shallower retracement, a meaningful-but-not-insane pole,
a decisive volume edge, a strong tight thrust, an orderly drying-up pullback.
"""

from __future__ import annotations

from collections.abc import Mapping

from .features import FeatureVector

# Normalisation caps (the value at which a feature earns full marks). Hand-set; refinable from data.
_POLE_HEIGHT_CAP = 0.20  # a 20% pole run is "as good as it gets" for the height term
_VOL_RATIO_CAP = 5.0  # pole peak volume 5x the consolidation's saturates the edge (inf -> 1.0)
_TIGHT_CAP = 0.10  # a consolidation range >= 10% of pole_high scores 0 for tightness
_ATR_ABNORMAL = 2.0  # >= 2x trailing ATR is "abnormal" (full marks); None -> neutral 0.5

# Feature weights (sum to 1.0). Documented rationale: retracement depth and pole meaningfulness
# dominate; volume edge next; shape/wick/tightness are supporting signals.
DEFAULT_WEIGHTS: Mapping[str, float] = {
    "retracement_shallow": 0.24,
    "pole_height": 0.16,
    "vol_ratio": 0.13,
    "cons_vol_reducing": 0.09,
    "pole_short": 0.08,
    "cons_strictness": 0.06,
    "pole_big_green": 0.05,
    "pole_vol_conc": 0.05,
    "cons_tightness": 0.05,
    "pole_ext_atr": 0.05,
    "pole_avg_body": 0.04,
}


def _clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else x


def _normalise(fv: FeatureVector, *, max_pole: int, max_retracement: float) -> dict[str, float]:
    """Each score feature mapped to [0, 1], higher = better."""
    ext = 0.5 if fv.pole_extension_atr is None else _clamp01(fv.pole_extension_atr / _ATR_ABNORMAL)
    pole_short = 1.0 if max_pole <= 1 else _clamp01(1.0 - (fv.pole_len - 1) / (max_pole - 1))
    return {
        "retracement_shallow": _clamp01(1.0 - fv.retracement / max_retracement),
        "pole_height": _clamp01(fv.pole_height_pct / _POLE_HEIGHT_CAP),
        "vol_ratio": _clamp01((fv.vol_ratio - 1.0) / (_VOL_RATIO_CAP - 1.0)),  # inf -> 1.0
        "cons_vol_reducing": 1.0 if fv.cons_vol_reducing else 0.0,
        "pole_short": pole_short,
        "cons_strictness": _clamp01(fv.cons_strictness),
        "pole_big_green": 1.0 if fv.pole_has_big_green else 0.0,
        "pole_vol_conc": _clamp01(fv.pole_vol_concentration),
        "cons_tightness": _clamp01(1.0 - fv.cons_tightness / _TIGHT_CAP),
        "pole_ext_atr": ext,
        "pole_avg_body": _clamp01(fv.pole_avg_body),
    }


def score(
    fv: FeatureVector,
    *,
    weights: Mapping[str, float] = DEFAULT_WEIGHTS,
    max_pole: int = 4,
    max_retracement: float = 0.50,
) -> tuple[float, dict[str, float]]:
    """0–1 quality score + per-feature contributions (which sum to the score).

    ``max_pole`` must match the cap the shape was detected under (``detect_setup`` threads it
    through): the ``pole_short`` term normalises against it, so a mismatched cap would mis-rank
    poles longer than ``max_pole`` (they clamp to the worst score).
    """
    normalised = _normalise(fv, max_pole=max_pole, max_retracement=max_retracement)
    contributions = {k: weights.get(k, 0.0) * v for k, v in normalised.items()}
    return sum(contributions.values()), contributions
