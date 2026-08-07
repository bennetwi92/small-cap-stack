"""Stage 4a of the engine-v2 pipeline (issue #179): the hard gates.

See ``research/engine-v2.md §7`` and ``research/bull-flag.md §5``. Each ``[gate input]``
feature becomes one
:class:`GateResult`; a setup is accepted iff every gate passes. Returning an *ordered* list of
results (not just a bool) lets the review page show **which** gate rejected a shape and by how much.

⚠️ There is deliberately **no trading-window gate here**. The window is a *selection* rule and
lives in the selection tier (``day.py``'s ``in_window`` / ``select_window_start``, #567); gating it
here too would double-gate it. An optional ``gate_window=`` flag used to exist for that, defaulting
off and passed ``True`` by exactly one test — deleted in #518 along with the end-anchored detector
that was its only other reader. ``trigger_in_window`` remains a *feature*, scored not gated.
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
    min_vol_ratio: float = 1.0,
) -> tuple[GateResult, ...]:
    """Ordered gate results for a feature vector. ``all(g.passed for g in ...)`` = accepted.

    The ``pole_len``/``cons_len`` gates are redundant when the caller already capped both with
    the same params during segmentation (they always pass there) — they exist for
    callers that gate against *tighter* caps than segmentation used, e.g. the #181 divergence spike
    segmenting at 8/6 but gating at 4/4 to measure what the cap change removes.
    """
    gates = [
        GateResult("pole_len", fv.pole_len <= max_pole, fv.pole_len),
        GateResult("cons_len", fv.cons_len <= max_cons, fv.cons_len),
        # A tolerance, not a boolean (#606). The locked #127 rule asks whether the thrust carried
        # more conviction than the pullback; testing `peak_vol > max(cons_vol)` made 0.9999 and 0.10
        # indistinguishable, so a 3.7% miss on a 5-minute volume bucket rejected identically to a
        # 90% one. `min_vol_ratio = 1.0` reproduces the strict rule exactly, so this is a strict
        # generalisation and the default keeps the gate's name honest.
        GateResult("vol_peak_gt_cons", fv.vol_ratio >= min_vol_ratio, fv.vol_ratio),
        GateResult("wick_peak", fv.peak_upper_wick <= max_peak_wick, fv.peak_upper_wick),
        # "No red candle in the pole" as an identify-and-reject gate rather than a detection skip
        # (#196). refine_pole keeps a red/flat-peaked pole so the trader sees the setup they'd read;
        # here it fails instead. Intermediate pole bars are green (the thrust walk), so the peak is
        # the only bar that can be non-green.
        GateResult("peak_green", fv.peak_is_green, fv.peak_is_green),
        GateResult("pole_height", fv.pole_height_pct >= min_pole_pct, fv.pole_height_pct),
        GateResult("cons_retracement", fv.retracement <= max_retracement, fv.retracement),
        GateResult("cons_holds_base", fv.holds_base, fv.holds_base),
    ]
    return tuple(gates)


def passed(gates: tuple[GateResult, ...]) -> bool:
    """A setup is accepted iff every gate passed."""
    return all(g.passed for g in gates)
