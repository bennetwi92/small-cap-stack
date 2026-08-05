"""Overnight pre-market harvest (#431) — the producer for #430's reconstructed-history store.

Rebuilds pre-market sessions the tracker never saw from purchased vendor minute bars and lands them
in ``data/recon``, where ``build_portfolio_payload(recon_store=…)`` publishes them as ``books_all``
beside the untouched live ``books``. Run it with ``python -m small_cap_stack.harvest``.

Read :mod:`.runner` for the shape of the job, :mod:`.guard` for why it is allowed nowhere near the
box's own day, and :mod:`.reconstruct` for what a "scanner appearance" means when nobody sells the
scanner's history.
"""

from __future__ import annotations

from .checkpoint import Checkpoint
from .guard import HostGuard, RunWindow, peak_rss_mb
from .prefilter import DailyRow, candidates, sweep_floors, universe_rows
from .reconstruct import (
    PREMARKET,
    GateTrace,
    Reconstruction,
    aggregate,
    reconstruct_hit,
    rolling_window_volume,
    to_bars,
    trim_session,
)
from .runner import (
    HARVEST_DATASETS,
    DailyResult,
    HarvestConfigError,
    HarvestRun,
    SessionResult,
    checkpoint_path,
    discard_partial,
    harvest_daily,
    harvest_session,
    harvest_store,
    plan_sessions,
    run_harvest,
    stored_universe,
    trading_sessions,
)
from .source import HarvestEntitlementError, HarvestError, HarvestSource, MassiveSource

__all__ = [
    "HARVEST_DATASETS",
    "PREMARKET",
    "Checkpoint",
    "DailyResult",
    "DailyRow",
    "GateTrace",
    "HarvestConfigError",
    "HarvestEntitlementError",
    "HarvestError",
    "HarvestRun",
    "HarvestSource",
    "HostGuard",
    "MassiveSource",
    "Reconstruction",
    "RunWindow",
    "SessionResult",
    "aggregate",
    "candidates",
    "checkpoint_path",
    "discard_partial",
    "harvest_daily",
    "harvest_session",
    "harvest_store",
    "peak_rss_mb",
    "plan_sessions",
    "reconstruct_hit",
    "rolling_window_volume",
    "run_harvest",
    "stored_universe",
    "sweep_floors",
    "to_bars",
    "trading_sessions",
    "trim_session",
    "universe_rows",
]
