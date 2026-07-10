"""Stage 1 of the engine-v2 pipeline (issue #177): tokenise a bar series into an H/L/E string.

See ``engine-v2.md §4`` and ``bull-flag.md §2.1``. Each bar after the first emits one token by
comparing its high to the previous bar's high within a flatness tolerance ``eps``:

- ``H`` — higher high  (``high[i] > high[i-1] + eps``)
- ``L`` — lower high   (``high[i] < high[i-1] - eps``)
- ``E`` — equal high   (within ``eps``; a 1-tick wobble that shouldn't break a run)

Pure over the raw bars (store-raw / compute-on-read), so the tokenisation replays over history.
Length invariant: ``len(tokenize(bars)) == max(0, len(bars) - 1)``.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from ..capture import Bar

Token = Literal["H", "L", "E"]


def tokenize(bars: Sequence[Bar], *, eps: float) -> list[Token]:
    """One token per bar after the first, comparing ``high[i]`` to ``high[i-1]`` within ``eps``.

    ``eps`` is a flatness tolerance (typically one tick): highs within ``eps`` of each other are
    ``E`` (equal), so a sub-tick wobble neither advances a pole nor breaks a consolidation.
    """
    tokens: list[Token] = []
    for i in range(1, len(bars)):
        delta = bars[i].high - bars[i - 1].high
        if delta > eps:
            tokens.append("H")
        elif delta < -eps:
            tokens.append("L")
        else:
            tokens.append("E")
    return tokens
