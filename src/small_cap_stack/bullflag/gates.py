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
    max_cons: int,
    min_pole_pct: float,
    max_retracement: float,
    min_vol_ratio: float = 1.0,
) -> tuple[GateResult, ...]:
    """Ordered gate results for a feature vector. ``all(g.passed for g in ...)`` = accepted.

    ⚠️ **Five gates, down from eight (#690).** Three were measured against the 197-session
    live+recon record and removed; the evidence and the two that were *kept despite* looking bad on
    a first pass are in ``research/decisions.md §D-44``. Do not re-add one without measuring it the
    same way — two of the three read as obviously-good rules and were not.

    **``pole_len`` — never fired.** 0 rejections in 3,639 pre-market setups: segmentation already
    caps pole length with the same parameter, so the gate re-asked a question already answered. It
    existed for callers gating against a *tighter* cap than segmentation used (the #181 divergence
    spike, long gone).

    **``cons_holds_base`` — changed no verdict.** It rejects 1,452 setups on its own, but every one
    is already rejected by another gate, so removing it flips ``passed`` on exactly 0 rows. Where it
    did discriminate it pointed the wrong way in 2 of the 3 periods.

    **``wick_peak`` — pointed the wrong way, consistently.** The setups it rejected outperformed the
    ones it kept in **all three** periods (dev -0.321 vs -0.344, val -0.011 vs -0.111, holdout
    -0.175 vs -0.278 R/trade), across 1,433 rejections. A wicky peak bar is not the warning it looks
    like.
    """
    gates = [
        GateResult("cons_len", fv.cons_len <= max_cons, fv.cons_len),
        # A tolerance, not a boolean (#606). The locked #127 rule asks whether the thrust carried
        # more conviction than the pullback; testing `peak_vol > max(cons_vol)` made 0.9999 and 0.10
        # indistinguishable, so a 3.7% miss on a 5-minute volume bucket rejected identically to a
        # 90% one. `min_vol_ratio = 1.0` reproduces the strict rule exactly, so this is a strict
        # generalisation and the default keeps the gate's name honest.
        GateResult("vol_peak_gt_cons", fv.vol_ratio >= min_vol_ratio, fv.vol_ratio),
        # "No red candle in the pole" as an identify-and-reject gate rather than a detection skip
        # (#196). refine_pole keeps a red/flat-peaked pole so the trader sees the setup they'd read;
        # here it fails instead. Intermediate pole bars are green (the thrust walk), so the peak is
        # the only bar that can be non-green.
        GateResult("peak_green", fv.peak_is_green, fv.peak_is_green),
        GateResult("pole_height", fv.pole_height_pct >= min_pole_pct, fv.pole_height_pct),
        # ⚠️ **Kept, and it is not what it looks like.** On its own this gate barely discriminates
        # (-0.264 kept vs -0.245 rejected R/trade) while rejecting **87%** of everything seen, which
        # reads as a gate to delete. It is not: under the 2-a-day capacity cap it is the binding
        # constraint on how many trades reach the book, and removing it takes the book from 103
        # trades at +0.9R to 236 at **-48.0R**, negative in all three periods. It rations rather
        # than selects, and on a negative-expectancy pool rationing is worth real money. If the
        # population's base rate ever turns positive, re-measure it — the sign of its value flips
        # with the sign of the pool's.
        GateResult("cons_retracement", fv.retracement <= max_retracement, fv.retracement),
    ]
    return tuple(gates)


def passed(gates: tuple[GateResult, ...]) -> bool:
    """A setup is accepted iff every gate passed."""
    return all(g.passed for g in gates)
