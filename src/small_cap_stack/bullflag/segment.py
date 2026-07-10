"""Stage 2 of the engine-v2 pipeline (issue #177): segment a bar series into a bull-flag shape.

See ``engine-v2.md §5`` and ``bull-flag.md §2.2``. Given the bars and their tokens
(:func:`.tokens.tokenize`), find the longest valid ``base → POLE → CONSOLIDATION`` ending at the
**last** bar (end-anchored — no trigger ``H`` yet), or ``None`` if no valid shape ends there.

Why the segmenter needs the bars, not just the tokens: the **peak** must be the *dominant* high of
the trailing window (the top the pullback descends from), not the nearest local up-tick — this is
the engine's #163 fix. Tokens drop magnitudes, so a mid-pullback up-tick would otherwise be
mistaken for the peak; reusing the legacy ``_find_pole_peak`` for the dominant-high search prevents
that (and keeps peak selection identical to the legacy detector). Tokens then drive the structural
checks.

Grammar (``E`` = equal high is allowed **only in the consolidation**, never in the pole):

- **Peak** = the dominant (highest) high among the trailing ``max_cons + 1`` bars. If it lands on
  the last bar the series is still extending → ``None``.
- **Consolidation** = the bars after the peak (``1..max_cons`` of them). Its tokens must contain
  **no ``H``** (any higher-high step means it ticked back up — not a clean pullback) and **>= 1
  strict ``L``** (an all-``E`` flat top is not a genuine pullback); ``E`` (a flat pullback candle)
  is fine here.
- **Pole** = the run of **strict higher highs (``H``)** ending at the peak, capped at ``max_pole``.
  An ``E`` is *not* allowed in the pole, so the walk stops at the first non-``H`` going back;
  ``pole_len`` counts the higher highs and must be ``>= 1``. Because every pole step strictly rises,
  the base sits strictly below the peak.

Index convention: token ``k`` compares ``bars[k]`` (from-side) to ``bars[k+1]`` (to-side).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ..capture import Bar
from .detect import _find_pole_peak
from .tokens import Token


@dataclass(frozen=True)
class Segment:
    """A pure structural match. All indices are into the bar list the tokens came from."""

    base_idx: int  # b0, the launch bar (the pole's first token's from-side)
    peak_idx: int  # the pole peak (dominant high of the trailing window)
    cons_end_idx: int  # last consolidation bar = detection fires here (the last bar)
    tokens: tuple[Token, ...]  # tokens for bars[base_idx+1 .. cons_end_idx]
    pole_len: int  # count of strict H in the pole (1..max_pole)
    cons_len: int  # count of consolidation bars after the peak (1..max_cons)


def segment_at_end(
    bars: Sequence[Bar], tokens: Sequence[Token], *, max_pole: int, max_cons: int
) -> Segment | None:
    """Longest valid base→POLE→CONSOLIDATION ending at the last bar, else ``None``.

    ``tokens`` must be :func:`.tokens.tokenize` of ``bars`` (``len(tokens) == len(bars) - 1``).
    """
    n = len(bars)
    if n < 3 or len(tokens) != n - 1:
        return None  # need a base + >=1 pole bar + >=1 flag bar, and matching tokens

    # Peak = dominant high of the trailing max_cons+1 bars (ties -> earliest); #163. Reuse the
    # legacy detector's _find_pole_peak so the dominant-high rule stays byte-identical across both
    # engines (this is what keeps the #179 parity test honest); when detect.py is retired the shared
    # primitive moves into the package core.
    peak = _find_pole_peak(list(bars), max_cons)
    if peak is None:
        return None  # a new high on the last bar -> still extending, no completed flag

    # Consolidation = tokens describing the steps peak->peak+1 .. (n-2)->(n-1).
    cons_tokens = tokens[peak:]
    cons_len = n - 1 - peak
    if "H" in cons_tokens:
        return None  # a higher-high step in the flag -> ticked back up, not a clean pullback
    if "L" not in cons_tokens:
        return None  # all-E flat top -> no net lower high, not a genuine pullback

    # Pole: the run of STRICT higher highs ending at the peak, capped at max_pole. An equal-high
    # (E) is NOT allowed in the pole — it only belongs to the consolidation (trader's rule) — so the
    # walk stops at the first non-H going back. This also keeps the base strictly below the peak
    # (base.high < peak.high), so pole_span > 0 always; the old E-tolerant walk could drift the base
    # across a flat run onto a bar at/above the peak, giving a zero/negative span (#181: ITRG/IVF).
    start = peak
    pole_len = 0
    while start - 1 >= 0 and tokens[start - 1] == "H" and pole_len < max_pole:
        start -= 1
        pole_len += 1
    if pole_len < 1:
        return None  # the step into the peak wasn't a strict higher high -> no pole

    return Segment(
        base_idx=start,
        peak_idx=peak,
        cons_end_idx=n - 1,
        tokens=tuple(tokens[start:]),
        pole_len=pole_len,
        cons_len=cons_len,
    )
