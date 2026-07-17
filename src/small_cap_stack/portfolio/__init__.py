"""Virtual-portfolio tracker (issue #230) — a *pre-shadow* paper book.

Phase-1 places no orders; this module answers "what would my account have done if I'd taken the
trades I intend to take?" over the data the tracker already captures. It is deliberately the same
decision code real shadow/paper mode will use — *select → size → simulate-exit* — with only the
final step (simulate an exit from cached bars) later swapped for placing a bracket + capturing a
fill. All logic is pure and replayable over the raw store (store-raw / compute-on-read).

Rules locked in ``research/decisions.md`` (#230, 2026-07-15):

- **Select** — an engine-v2 *takeable* setup (formed, all gates passed, not exhausted) that
  *triggered*, whose **trigger bar opens strictly pre-market** (before 09:30 ET — stricter than the
  ``first_hit``-based results-page "premarket" label), with an ``entry_fill`` price in the
  configured band ($1–20). At most ``portfolio_max_trades_per_day`` per day, in trigger-time order.
- **Size** — risk-based, capped by notional (#237): each position targets ``risk_fraction`` (5%)
  of opening equity at risk — ``floor(opening_equity × risk_fraction / (entry − stop))`` — but is
  capped at ``position_fraction`` (50%) of opening equity in notional, i.e. ``min(risk_qty,
  cap_qty)``. Both of the day's trades size off the *day's opening equity* (they're concurrent
  positions committed before either resolves), so at the cap 50% × 2 fully deploys the account; the
  risk target sizes smaller whenever the stop is **wider** than ``risk_fraction /
  position_fraction`` (10%) of the entry, and the **cap** sizes smaller on anything tighter — which
  at bull-flag stop distances is most setups, so a taken trade routinely risks well under the
  configured 5%. That asymmetry was stated backwards here until #286 and is why the page could
  advertise "5% risk / trade" over trades that risked 0.8%; :class:`SizedPosition` now reports the
  binding constraint and the realised risk. In the *adaptive* book ``risk_fraction`` is itself
  throttled day-by-day by a kill-switch ladder (#239) — see :func:`simulate_portfolio_adaptive`.
- **Exit** — a fixed R target with an optional breakeven arm, simulated bar-by-bar with the same
  conservative stop-first / gap-through convention as :mod:`rmetrics`. Costs + exit slippage are
  netted out so the equity curve is honest at ~$250 notional.

**Cost model** (``research/broker-costs.md``, #232) — IBKR **tiered**, which is the cheapest plan
available to a UK client (IBKR Lite is US-residents-only) across essentially this whole price band.
Tiered *unbundles* the exchange/regulatory pass-throughs, and at these share counts they roughly
equal the commission itself, so charging commission alone understates a round trip by 20–50%. See
:func:`trade_costs`. The monthly market-data subscription is charged too (:class:`_DataFeeLedger`):
it is ~2%/month of a $500 book, and #232's central finding is that fixed costs do **not** scale down
with capital.

**Settled-cash invariant** — this is a UK *cash* account, so a purchase needs settled funds, and
buying with unsettled proceeds then selling before they settle is a good-faith violation (#232 §6).
The book is compliant *by construction* rather than by simulating settlement: the notional **cap**
bounds every position at 50% of ``opening_equity`` (the risk target only ever sizes *smaller*), so
with a 2/day cap max daily buy notional
``= 2 × floor(0.50 × opening_equity / entry) × entry ≤ opening_equity``; and since every trade
closes same-day, no unsettled position is carried and T+1 opens each day settled. The *cap* is
the constraint — see ``test_settled_cash_invariant``, which fails loudly if the config is ever
changed such that ``position_fraction × max_trades_per_day > 1``.
"""

# Split into focused modules (#259) — this file is the package's public face. `portfolio.py` had
# grown to ~1400 lines bundling the exit simulator, sizing/costs, four period ledgers, the adaptive
# optimiser + kill-switch, and the on-disk cache + JSON codec. Everything below is re-exported so
# `from small_cap_stack.portfolio import X` keeps working for every caller and test — the split is
# behaviour-preserving.
#
# How that was verified at split time (one-off, not a standing guard — the suite is that): every
# top-level node was moved by an AST extraction and diffed against the pre-split module (49/49
# present, zero modified bodies), and a fingerprint of the old module's output — exit sims, sizing
# and cost grids, the ledgers, three fixed-target books, the adaptive book — was re-run against the
# package and matched exactly.
#
# Private names (_take_day, _DataFeeLedger, ...) are re-exported too — but only the ones something
# outside the package actually imports. The suite reaches for them by design (trading logic must be
# exhaustively unit-tested, per CLAUDE.md), so those are part of this package's surface in practice;
# re-exporting the rest would freeze internal helpers into an apparent contract nothing depends on.
#
# ⚠️ These are BINDINGS, not windows: `from .sim import _select_day` copies the reference, so
# `monkeypatch.setattr(portfolio, "_select_day", ...)` is a SILENT NO-OP — sim.py resolves its own
# global and never sees the patch, and a test written that way passes while asserting nothing.
# Patch where the name is looked up (`portfolio.sim._select_day`, `portfolio.payload.
# extract_day_trades`). This bit the suite during the split: the tests that did it failed loudly,
# which is the only reason it was caught.

# ruff: noqa: F401 — every import below is a deliberate re-export, not dead code.

from __future__ import annotations

from .adaptive import (
    TargetStat,
    _day_signal_r,
    best_target,
    expectancy_curve,
    risk_ladder,
    step_risk_rung,
)
from .costs import SizedPosition, TradeCosts, commission, size_position, trade_costs
from .exit import ExitOutcome, simulate_exit
from .extract import _qualify, extract_day_trades
from .ledgers import (
    _DataFeeLedger,
    _TaxLedger,
    _VpsLedger,
    _WithdrawalLedger,
)
from .models import (
    CandidateTrade,
    CashFlow,
    PaperTrade,
    PortfolioResult,
    SkippedTrade,
)
from .payload import (
    _candidate_from_json,
    _candidate_to_json,
    build_portfolio_payload,
    collected_dates,
    portfolio_candidate_cache_dir,
)
from .sim import (
    AdaptiveBook,
    AdaptiveState,
    _select_day,
    _take_day,
    simulate_portfolio,
    simulate_portfolio_adaptive,
)

__all__ = [
    "AdaptiveBook",
    "AdaptiveState",
    "CandidateTrade",
    "CashFlow",
    "ExitOutcome",
    "PaperTrade",
    "PortfolioResult",
    "SizedPosition",
    "SkippedTrade",
    "TargetStat",
    "TradeCosts",
    "best_target",
    "build_portfolio_payload",
    "collected_dates",
    "commission",
    "expectancy_curve",
    "extract_day_trades",
    "portfolio_candidate_cache_dir",
    "risk_ladder",
    "simulate_exit",
    "simulate_portfolio",
    "simulate_portfolio_adaptive",
    "size_position",
    "step_risk_rung",
    "trade_costs",
]
