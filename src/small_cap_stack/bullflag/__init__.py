"""Bull-flag detection package (issue #16; engine-v2 redefinition #176).

Public surface. The **engine-v2** pipeline (``research/engine-v2.md``) is the live detector:
raw bars are
tokenised (:mod:`.tokens`), segmented into a pole + consolidation (:mod:`.segment`), reduced to a
feature vector (:mod:`.features`), then gated and scored (:mod:`.gates`, :mod:`.score`).
:mod:`.setup` assembles the end-anchored result; :mod:`.day` runs the full-day detector that
``rmetrics`` and ``charts`` consume. :mod:`.primitives` holds the shared bar vocabulary.

The superseded anchored detector (``detect`` / ``BullFlag``) and its golden-parity test were
removed in #296 once the #180 cut-over had landed and left them without a caller.
"""

from __future__ import annotations

from .cycles import (
    Cycle,
    contiguous_prior_cycles,
    prior_cycle_count,
    segment_cycles,
    significant_cycles,
)
from .day import DaySetup, detect_day, detect_day_with_settings
from .features import FeatureVector, extract, trailing_atr
from .gates import GateResult, evaluate
from .primitives import classify, find_pole_peak, is_big_green, non_increasing, upper_wick_frac
from .score import DEFAULT_WEIGHTS, score
from .segment import Segment, refine_pole, segment_at_end
from .setup import Setup, detect_setup, detect_setup_with_settings
from .tokens import Token, token_eps, tokenize

__all__ = [
    "DEFAULT_WEIGHTS",
    "Cycle",
    "DaySetup",
    "FeatureVector",
    "GateResult",
    "Segment",
    "Setup",
    "Token",
    "classify",
    "contiguous_prior_cycles",
    "detect_day",
    "detect_day_with_settings",
    "detect_setup",
    "detect_setup_with_settings",
    "evaluate",
    "extract",
    "find_pole_peak",
    "is_big_green",
    "non_increasing",
    "prior_cycle_count",
    "refine_pole",
    "score",
    "segment_at_end",
    "segment_cycles",
    "significant_cycles",
    "token_eps",
    "tokenize",
    "trailing_atr",
    "upper_wick_frac",
]
