"""Engine-v2 exhaustion primitives (#102 / #176): count the prior pump/fade cycles before a pole.

See ``engine-v2.md`` (full-day detector) and the visual-review record (#194). A "move" that has
already pumped and faded several times is *exhausted* — a late entry into a worn-out name. These
pure functions count how many real prior cycles lead into a target pole, so the day detector can
reject the 3rd+ cycle.

Two layers, validated across 25 reviewed opportunities:

- :func:`segment_cycles` — a deliberately LOOSE pass over the whole day's tokens: a pole is any run
  of ``H``, a consolidation any run of ``L``/``E`` after it (the first ``H`` after a consolidation
  both ends that cycle and starts the next). No colour/thrust rule, no gates — it just finds every
  pump/fade the grammar admits, including noise.
- :func:`significant_cycles` — keep only cycles that are a REAL pump: the pole span must carry a
  green *thrust* bar (``_is_big_green``) AND some bar must clear ``min_volume``. Structure drops
  high-volume flat/doji churn (SNDQ); the (deliberately low, ``scan_min_5m_volume // 2``) volume
  floor drops tiny green blips (ARCT/FCEL/SDOT) while keeping genuine low-volume pumps (WULF's 84k).
  Volume alone is the wrong axis — it was simultaneously too high (dropped WULF) and too low (kept
  SNDQ's churn); structure + a lower floor separates them (#102).
- :func:`contiguous_prior_cycles` — of the significant cycles, count only a CONTIGUOUS run abutting
  the pole (walking back, ``<= 1`` bar gap; the first gap ends the run). Drops multi-hour drift
  (CONL) and disconnected earlier blips (MARA's 08:00 pump). No ascending requirement — exhaustion
  is about repetition regardless of direction (a fading sequence of ever-lower pumps still counts;
  OPEN). Structural significance already removes the flat churn that motivated an earlier ascending
  rule, which wrongly zeroed OPEN's descending exhaustion.

Index convention matches :mod:`.tokens`: token ``k`` compares ``bars[k]`` to ``bars[k+1]``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ..capture import Bar
from .detect import _is_big_green
from .tokens import Token


@dataclass(frozen=True)
class Cycle:
    """One pump/fade cycle from the loose H/E/L walk. Indices are into the bar list."""

    pole_start: int  # base bar (the bar before the first H of this cycle's pole)
    peak: int  # last H bar of this cycle's pole
    cons_start: int | None  # first consolidation bar, or None if still mid-pole at day's end
    cons_end: int  # last consolidation bar so far (== peak if no consolidation started)
    breakout: int | None  # the H bar that ends this cycle and starts the next; None if open


def segment_cycles(tokens: Sequence[Token]) -> list[Cycle]:
    """Segment the whole day's tokens into consecutive pump/fade cycles, left to right.

    The last cycle may be "open" (``breakout is None``) if the day ends mid-pole or mid-flag.
    """
    cycles: list[Cycle] = []
    state = "searching"  # searching -> pole -> cons -> pole (next cycle) -> ...
    pole_start: int | None = None
    peak: int | None = None
    cons_start: int | None = None
    for i, t in enumerate(tokens):
        if state == "searching":
            if t == "H":
                pole_start, peak, state = i, i + 1, "pole"
        elif state == "pole":
            if t == "H":
                peak = i + 1
            else:
                cons_start, state = i + 1, "cons"
        elif t == "H":  # state == "cons": the first H ends this cycle and starts the next pole
            assert pole_start is not None and peak is not None
            cycles.append(Cycle(pole_start, peak, cons_start, i, i + 1))
            pole_start, peak, cons_start, state = i, i + 1, None, "pole"
    if pole_start is not None and peak is not None and state != "searching":
        cycles.append(
            Cycle(pole_start, peak, cons_start, cons_start - 1 if cons_start else peak, None)
        )
    return cycles


def significant_cycles(
    bars: Sequence[Bar], cycles: Sequence[Cycle], min_volume: float
) -> list[Cycle]:
    """Keep only cycles that are a REAL pump: a green thrust bar in the pole span AND a bar clearing
    ``min_volume``. Structure kills flat/doji churn (even at high volume); the low volume floor
    kills tiny green blips while keeping genuine low-volume pumps. See the module docstring."""
    out: list[Cycle] = []
    for c in cycles:
        span = bars[c.pole_start + 1 : c.peak + 1]
        if not span:
            continue
        if any(_is_big_green(b) for b in span) and max(b.volume for b in span) >= min_volume:
            out.append(c)
    return out


def contiguous_prior_cycles(
    bars: Sequence[Bar], sig_cycles: Sequence[Cycle], pole_base_idx: int
) -> list[Cycle]:
    """The prior cycles that count toward exhaustion, in chronological order: the contiguous run of
    significant cycles abutting the pole (walking back, ``<= 1`` bar gap; the first gap ends it).

    ``bars`` is accepted for symmetry with the rest of the module (and so callers need not special-
    case an empty list); the walk itself only needs the cycle indices.
    """
    priors = [c for c in sig_cycles if c.peak < pole_base_idx]
    counted: list[Cycle] = []
    nxt_start = pole_base_idx
    for c in reversed(priors):
        if nxt_start - c.cons_end <= 1:
            counted.append(c)
            nxt_start = c.pole_start
        else:
            break  # a gap ends the contiguous run
    return list(reversed(counted))


def prior_cycle_count(bars: Sequence[Bar], sig_cycles: Sequence[Cycle], pole_base_idx: int) -> int:
    """How many prior cycles count toward exhaustion (see :func:`contiguous_prior_cycles`)."""
    return len(contiguous_prior_cycles(bars, sig_cycles, pole_base_idx))
