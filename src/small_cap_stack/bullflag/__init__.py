"""Bull-flag detection package (issue #16; engine-v2 redefinition #176).

Public surface. The **legacy** anchored detector (``detect`` / ``detect_with_settings`` /
``BullFlag`` / ``classify``) is unchanged and still drives ``rmetrics`` and the review workbench.
The engine-v2 pipeline lands stage by stage behind it (``engine-v2.md``): stage 1 :mod:`.tokens`,
stage 2 :mod:`.segment` (this issue #177); stages 3–4 (features/gates/score) follow in #178–#179.
"""

from __future__ import annotations

from .cycles import (
    Cycle,
    contiguous_prior_cycles,
    prior_cycle_count,
    segment_cycles,
    significant_cycles,
)
from .detect import BullFlag, classify, detect, detect_with_settings
from .features import FeatureVector, extract, trailing_atr
from .gates import GateResult, evaluate
from .score import DEFAULT_WEIGHTS, score
from .segment import Segment, refine_pole, segment_at_end
from .setup import Setup, detect_setup, detect_setup_with_settings
from .tokens import Token, token_eps, tokenize

__all__ = [
    "DEFAULT_WEIGHTS",
    "BullFlag",
    "Cycle",
    "FeatureVector",
    "GateResult",
    "Segment",
    "Setup",
    "Token",
    "classify",
    "contiguous_prior_cycles",
    "detect",
    "detect_setup",
    "detect_setup_with_settings",
    "detect_with_settings",
    "evaluate",
    "extract",
    "prior_cycle_count",
    "refine_pole",
    "score",
    "segment_at_end",
    "segment_cycles",
    "significant_cycles",
    "token_eps",
    "tokenize",
    "trailing_atr",
]
