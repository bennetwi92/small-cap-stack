"""Stage 1 of the engine-v2 pipeline (issue #177): tokenise a bar series into an H/L/E string.

See ``research/engine-v2.md §4`` and ``research/bull-flag.md §2.1``. Each bar after the first
emits one token by
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
from ..config import Settings

Token = Literal["H", "L", "E"]


def token_eps(settings: Settings) -> float:
    """The engine-v2 tokenisation flatness tolerance: HALF a tick.

    A full one-tick higher high IS a higher high (directional) and must extend a pole, so it can't
    be swallowed as ``E``; only a truly-flat top (Δhigh = 0) is ``E`` (#196/SNDQ: a +0.01 higher
    high was mislabeled ``E`` at eps=1 tick, truncating the pole). Half a tick keeps every real
    >= 1-tick move directional while still absorbing sub-tick float noise (``tokenize`` rounds the
    delta first). ``detect_day_with_settings`` passes this and always did. A second, end-anchored
    wrapper resolved a ``getattr`` on a ``Settings`` field that never existed and silently ran at a
    full tick until #513; #518 deleted that detector, so there is one tokenisation path and the
    divergence cannot recur. ``eps`` stays an *argument* so a test can vary it."""
    return settings.tick_size / 2


def tokenize(bars: Sequence[Bar], *, eps: float) -> list[Token]:
    """One token per bar after the first, comparing ``high[i]`` to ``high[i-1]`` within ``eps``.

    ``eps`` is a flatness tolerance — **half** a tick on both engine paths (:func:`token_eps`), so
    highs within a sub-tick wobble of each other are ``E`` (equal) and neither advance a pole nor
    break a consolidation, while a genuine one-tick step stays directional (#196).
    """
    tokens: list[Token] = []
    for i in range(1, len(bars)):
        # Round the delta before the eps test: float error otherwise makes an exactly-1-tick move
        # register as H/L instead of E (e.g. 1.26 - 1.25 == 0.010000000000000009 > 0.01). Prices
        # carry <= 4 decimals, so rounding to 6 kills the noise without touching real moves.
        delta = round(bars[i].high - bars[i - 1].high, 6)
        if delta > eps:
            tokens.append("H")
        elif delta < -eps:
            tokens.append("L")
        else:
            tokens.append("E")
    return tokens
