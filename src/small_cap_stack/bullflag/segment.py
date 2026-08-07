"""Stage 2 of the engine-v2 pipeline (issue #177): segment a bar series into a bull-flag shape.

See ``research/engine-v2.md §5`` and ``research/bull-flag.md §2.2``. Given the bars and their tokens
(:func:`.tokens.tokenize`), find the longest valid ``base → POLE → CONSOLIDATION`` ending at the
**last** bar (end-anchored — no trigger ``H`` yet), or ``None`` if no valid shape ends there.

Why the segmenter needs the bars, not just the tokens: the **peak** must be the *dominant* high of
the trailing window (the top the pullback descends from), not the nearest local up-tick — this is
the engine's #163 fix. Tokens drop magnitudes, so a mid-pullback up-tick would otherwise be
mistaken for the peak; using the shared ``find_pole_peak`` primitive for the dominant-high search
prevents that. Tokens then drive the structural checks.

Grammar (``E`` = equal high is allowed **only in the consolidation**, never in the pole):

- **Peak** = the dominant (highest) high among the trailing ``max_cons + 1`` bars. If it lands on
  the last bar the series is still extending → ``None``. The peak bar itself must be **green**
  (``close > open``) — a red "peak" (a new high that reverses and closes weak within the same bar,
  e.g. a shooting-star top) isn't a genuine thrust, so it's disqualified and the caller keeps
  searching later prefixes for a green peak instead (validated via visual review, #182/#190: IRE).
- **Consolidation** = the bars after the peak (``1..max_cons`` of them). Its tokens must contain
  **no ``H``** (any higher-high step means it ticked back up — not a clean pullback) and **>= 1
  strict ``L``** (an all-``E`` flat top is not a genuine pullback); ``E`` (a flat pullback candle)
  is fine here.
- **Pole** = the run of **strict higher highs (``H``)** ending at the peak, capped at ``max_pole``,
  where every bar is a genuine **thrust candle** (green, body >= half its range) — a technically
  higher-high bar that's doji-like or red is a quiet pause or reversal, not real continuation of
  momentum, so the walk stops there and that bar becomes the base instead of an intermediate pole
  bar (validated via visual review, #182/#190: MUZ/CRCG/CONL). ``E`` is *not* allowed in the pole
  either way; ``pole_len`` counts the higher highs and must be ``>= 1``. Because every pole step
  strictly rises, the base sits strictly below the peak.

Index convention: token ``k`` compares ``bars[k]`` (from-side) to ``bars[k+1]`` (to-side).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ..capture import Bar
from .primitives import classify, find_pole_peak, is_green_bodied
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

    # Peak = dominant high of the trailing max_cons+1 bars (ties -> earliest); #163.
    peak = find_pole_peak(list(bars), max_cons)
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
    #
    # The peak itself must be green (any body size — matches the single-bar-pole tolerance below);
    # a red peak disqualifies this candidate entirely (#182/#190: IRE's shooting-star top). To
    # extend the pole PAST the peak's immediate predecessor, each additional bar must be a genuine
    # thrust (green, body >= half its range) — a doji-like or red bar breaks the walk and becomes
    # the base instead of an intermediate pole bar (#182/#190: MUZ/CRCG/CONL).
    #
    # The extension walk itself is :func:`refine_pole` — the only thing this path adds is the
    # green-peak requirement, which refine_pole deliberately omits (the day detector wants a red
    # peak identified-and-rejected downstream, not skipped). refine_pole also covers the rest of
    # the old condition: `max_pole < 1` disables the pole, and `tokens[peak - 1] != "H"` means no
    # strict higher-high step into the peak. Its extra `peak - 1 < 0` guard is dead here
    # (find_pole_peak's window floor `lo = max(1, n-1-max_cons)` guarantees peak >= 1).
    if classify(bars[peak]) != "green":
        return None  # a red/doji peak disqualifies this candidate (#182/#190: IRE's shooting star)
    refined = refine_pole(bars, tokens, peak, max_pole=max_pole)
    if refined is None:
        return None  # pole disabled, or no strict higher high into the peak
    base, pole_len = refined

    return Segment(
        base_idx=base,
        peak_idx=peak,
        cons_end_idx=n - 1,
        tokens=tuple(tokens[base:]),
        pole_len=pole_len,
        cons_len=cons_len,
    )


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
    whatever peak its
    greedy cycle walk found — NOT the dominant-high search :func:`segment_at_end` uses — so this
    shares the colour/thrust extension rule without the end-anchoring or dominant-peak selection.

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
