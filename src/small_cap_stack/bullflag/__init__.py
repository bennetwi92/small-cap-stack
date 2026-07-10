"""Bull-flag detection package (issue #16; engine-v2 redefinition #176).

Public surface. The **legacy** anchored detector (``detect`` / ``detect_with_settings`` /
``BullFlag`` / ``classify``) is unchanged and still drives ``rmetrics`` and the review workbench.
The engine-v2 pipeline lands stage by stage behind it (``engine-v2.md``): stage 1 :mod:`.tokens`,
stage 2 :mod:`.segment` (this issue #177); stages 3–4 (features/gates/score) follow in #178–#179.
"""

from __future__ import annotations

from .detect import BullFlag, classify, detect, detect_with_settings
from .segment import Segment, segment_at_end
from .tokens import Token, tokenize

__all__ = [
    "BullFlag",
    "Segment",
    "Token",
    "classify",
    "detect",
    "detect_with_settings",
    "segment_at_end",
    "tokenize",
]
