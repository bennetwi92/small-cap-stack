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
- **Size** — full buying power, not risk-based (#694 follow-up, full-buying-power sizing): the
  book's one trade a day (``portfolio_max_trades_per_day``, already 1 since #694/D-45) sizes to
  ``floor(opening_equity / entry_price)`` — as many shares as the day's opening equity can buy. No
  risk-fraction target, no notional cap; this matches how the trader (and Ross Cameron's "Warrior"
  small-account-challenge style) actually sizes. :class:`SizedPosition` still reports the realised
  ``risk_usd``/``risk_pct`` post-hoc, for the dashboard, but they no longer bound the size. In the
  *adaptive* book the day's activity — take the setup, or sit out entirely — is itself throttled
  day-by-day by a kill-switch ladder (#239) — see :func:`simulate_portfolio_adaptive`.
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
The book is compliant *by construction* rather than by simulating settlement: every position sizes
to ``floor(opening_equity / entry) ≤ opening_equity / entry``, so buy notional never exceeds
``opening_equity`` for a single trade — and under full-buying-power sizing (#694 follow-up) there is
no per-trade cap to stack a second concurrent trade *under*, so this only holds for
``portfolio_max_trades_per_day ≤ 1`` (already the case since #694/D-45: a wider cap would size a
second position off the SAME full opening equity a first position already spent). Every trade closes
same-day, so no unsettled position is carried and T+1 opens each day settled. See
``test_settled_cash_invariant_holds_by_construction``, which fails loudly if the config is ever
changed such that ``portfolio_max_trades_per_day > 1``.
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
from .metrics import daily_returns, sharpe, sortino, ulcer_index
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
    _read_candidate_cache,
    build_portfolio_payload,
    collected_dates,
    open_recon_store,
    portfolio_candidate_cache_dir,
    recon_store_dir,
)
from .projection import (
    DaySample,
    build_projection,
    capital_for_income,
    day_rate_net_annual_gbp,
    day_samples,
    future_sessions,
    income_from_capital,
    income_ramp,
    years_to_capital,
)
from .sim import (
    AdaptiveBook,
    AdaptiveState,
    TargetFit,
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
    "DaySample",
    "ExitOutcome",
    "PaperTrade",
    "PortfolioResult",
    "SizedPosition",
    "SkippedTrade",
    "TargetFit",
    "TargetStat",
    "TradeCosts",
    "best_target",
    "build_portfolio_payload",
    "build_projection",
    "capital_for_income",
    "collected_dates",
    "commission",
    "daily_returns",
    "day_rate_net_annual_gbp",
    "day_samples",
    "expectancy_curve",
    "extract_day_trades",
    "future_sessions",
    "income_from_capital",
    "income_ramp",
    "open_recon_store",
    "portfolio_candidate_cache_dir",
    "recon_store_dir",
    "risk_ladder",
    "sharpe",
    "simulate_exit",
    "simulate_portfolio",
    "simulate_portfolio_adaptive",
    "size_position",
    "sortino",
    "step_risk_rung",
    "trade_costs",
    "ulcer_index",
    "years_to_capital",
]
