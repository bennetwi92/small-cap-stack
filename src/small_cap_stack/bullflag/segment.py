"""Stage 2 of the engine-v2 pipeline (issue #177): the bull-flag shape and its pole walk.

See ``research/engine-v2.md §5`` and ``research/bull-flag.md §2.2``.

:class:`Segment` is the pure structural match — indices into a bar list, no prices.
:func:`refine_pole` is the **pole extension walk**: given a peak, how far back the thrust runs.

⚠️ There used to be a second, *end-anchored* segmenter here (``segment_at_end``) — a dominant-high
search that found the longest shape ending at the last bar, for the ``detect_setup`` detector.
Both were deleted in #518: ``detect_setup`` had no caller in ``src/`` and existed only to be
tested, and it was the sole caller of ``segment_at_end``. The live path is ``day.py::detect_day``,
whose greedy cycle walk picks the peak and then calls :func:`refine_pole` for the pole — so the
extension rule the two shared is what survived, and it now has exactly one caller.

Index convention: token ``k`` compares ``bars[k]`` (from-side) to ``bars[k+1]`` (to-side).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ..capture import Bar
from .primitives import is_green_bodied
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


def refine_pole(
    bars: Sequence[Bar],
    tokens: Sequence[Token],
    peak: int,
    *,
    max_pole: int,
    min_step_share: float = 0.0,
    min_body_frac: float = 0.5,
) -> tuple[int, int] | None:
    """``(base_idx, pole_len)`` for the pole ending at a GIVEN ``peak``, or ``None`` if none forms.

    The full-day detector (``detect_day``, research/engine-v2.md §13) anchors the pole to
    whatever peak its greedy cycle walk found, then calls this to walk the pole back from it.

    Walk backward from the peak through strict higher-high **thrust** bars (green, body >= half its
    range, :func:`.is_big_green`), capped at ``max_pole``; a doji-like/red bar stops the walk and
    becomes the base (#182/#190: MUZ/CRCG/CONL). The peak itself is NOT colour-checked here — a
    red/flat peak still forms a pole and is rejected downstream by the ``peak_green`` gate
    (identify-and-reject, #196: OPEN/IRE), rather than being skipped so the greedy walk wanders to a
    later junk pole. Returns ``None`` only when the pole is disabled (``max_pole < 1``) or there is
    no higher-high step into the peak (``tokens[peak-1] != "H"``).

    ``min_step_share`` (#585) additionally requires each *extension* to carry that fraction of the
    pole's advance, measured against the base it would create: a bar that ticks higher but
    contributes almost nothing to the move is a quiet pause, not thrust, and stops the walk. It has
    to be a **within-pole share** rather than any absolute or trailing-relative measure — on a
    frozen pre-market tape every trailing measure is inflated, and seven alternatives (step %, ATR
    multiple, step vs the bar's own range, absolute body %, a tighter ``is_big_green``, and two
    volume rules) all rank AKAN 2026-05-22's 08:00 bar *above* the WULF 09:25 extension that a
    reviewed fixture keeps. Only this axis orders them correctly (0.068 vs 0.106). ``0.0`` disables
    it, which is what a shape-only caller and the pre-#585 behaviour want.

    The admissible window here is narrow — (0.0677, 0.1058) — and set by one observation at each
    end. Treat the default as provisional; a reviewed case with a genuine sub-0.08 extension would
    close it entirely.

    ``min_body_frac`` (#607) is the thrust-body threshold for the same walk, split out from the
    locked 0.50 in :func:`.is_big_green` because a hard cut with no tolerance truncates poles on
    near-misses and inflates the reported retracement. BNAI 2026-06-09's 06:20 bar ran +7.5% on
    163k shares and carried **72% of the pole's advance**, and was called a quiet pause on a body of
    0.4861 — a 1.4-percentage-point miss. Only this walk reads it: ``significant_cycles`` and
    ``pole_has_big_green`` keep 0.50, or exhaustion counts move with it. Its admissible window is
    (0.4526, 0.4861] — CIFR 2026-07-06's 11:35 bar must stay excluded, BNAI's must come in — which
    is **0.033 wide on two observations**, and just as provisional as ``min_step_share``'s."""
    if max_pole < 1 or peak - 1 < 0 or tokens[peak - 1] != "H":
        return None
    base, pole_len = peak - 1, 1
    while (
        pole_len < max_pole
        and base - 1 >= 0
        and tokens[base - 1] == "H"
        and is_green_bodied(bars[base], min_body_frac)
        and _step_share(bars, base, peak) >= min_step_share
    ):
        base -= 1
        pole_len += 1
    return base, pole_len


def _step_share(bars: Sequence[Bar], base: int, peak: int) -> float:
    """What fraction of the pole's advance the step onto ``bars[base]`` contributes.

    Denominated by the advance the *extended* pole would span (``peak.high - bars[base-1].high``),
    so the question is "is this bar a real part of the move it is being counted into". A
    non-positive span cannot be judged and reads as 0.0, which blocks the extension.
    """
    span = bars[peak].high - bars[base - 1].high
    if span <= 0:
        return 0.0
    return (bars[base].high - bars[base - 1].high) / span
