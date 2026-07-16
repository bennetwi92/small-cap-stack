"""The virtual book's value types: candidates, trades, skips, cash flows, results.

Split out of the old single-file ``portfolio.py`` (#259) with no behaviour change.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from ..capture import Bar
from ..config import Settings
from .exit import ExitOutcome, simulate_exit


@dataclass(frozen=True)
class CandidateTrade:
    """A qualifying pre-market engine-v2 trade, extracted from a day's captured bars.

    Carries the bar window + entry index so the exit can be simulated for *any* (target, breakeven)
    without re-reading the store — that is what makes the adaptive optimiser cheap."""

    trading_date: date
    symbol: str
    seg_id: str
    run: int
    trigger_at: datetime  # the entry bar's start (ET-aware)
    entry_price: float  # realised fill (gap-through), what P&L is measured against
    entry_fill: float  # the +3-tick nominal fill (the price band is applied to this)
    stop: float
    risk: float  # entry_price - stop (> 0)
    entry_index: int
    bars: tuple[Bar, ...]

    def exit_under(self, s: Settings, target_r: float, breakeven_r: float) -> ExitOutcome:
        return simulate_exit(
            self.bars,
            self.entry_price,
            self.stop,
            self.entry_index,
            target_r=target_r,
            breakeven_r=breakeven_r,
            tick_size=s.tick_size,
            exit_slippage_ticks=s.portfolio_exit_slippage_ticks,
        )


@dataclass(frozen=True)
class PaperTrade:
    """A trade the virtual book actually took, with its sized outcome and equity bookkeeping."""

    trading_date: date
    symbol: str
    seg_id: str
    run: int
    trigger_at: datetime
    entry_price: float
    stop: float
    qty: int
    target_r: float
    breakeven_r: float
    realized_r: float
    reason: str
    exit_price: float
    gross_pnl_usd: float
    commission_usd: float  # IBKR's own line
    fees_usd: float  # exchange + clearing + TAF + SEC (tiered unbundles these) — #232 §1
    net_pnl_usd: float  # gross − commission − fees
    equity_before: float
    equity_after: float


@dataclass(frozen=True)
class SkippedTrade:
    """A qualifying setup the book did **not** take — issue #230 follow-up.

    Two reasons, kept apart by ``skip_reason``:

    - ``"cap"`` — the day's ``max_trades_per_day`` was already filled by earlier (lower
      trigger-time) trades. This is the population the "what did the 2/day cap cost me?" R-log
      answers, and the only one the headline ``skipped_total_r`` counts.
    - ``"unaffordable"`` — it was selected, but ``size_position`` returned ``qty < 1``, so the book
      couldn't buy a single share. These used to vanish into neither log (#251). Practically
      unreachable at the default book (it needs equity ~$40, a >90% drawdown), but a silently
      dropped setup is worse than a rare one.

    Carries what the trade *would* have returned at that day's (target, breakeven), simulated over
    the same bars with the same exit model as a taken trade. It is unsized on purpose: R is
    size-independent, and reporting a hypothetical dollar P&L would imply the position was actually
    affordable/compliant, which the settled-cash cap exists to prevent — without pretending we
    could have held a third concurrent position."""

    trading_date: date
    symbol: str
    seg_id: str
    run: int
    trigger_at: datetime
    entry_price: float
    stop: float
    target_r: float
    breakeven_r: float
    realized_r: float  # what it would have made/lost at the day's target (size-independent)
    reason: str  # exit reason: "target" | "stop" | "breakeven" | "close"
    exit_price: float
    skip_reason: str = "cap"  # why it wasn't taken: "cap" | "unaffordable"


@dataclass(frozen=True)
class CashFlow:
    """One dated money movement outside trading: a withdrawal (out to you), a CGT bill, or the VPS
    fee. ``usd`` is the amount debited from the book; ``gbp`` is the same amount in pounds."""

    date: date
    kind: str  # "withdrawal" | "tax" | "vps"
    usd: float
    gbp: float


@dataclass(frozen=True)
class PortfolioResult:
    start_equity: float
    end_equity: float
    trades: tuple[PaperTrade, ...]
    equity_curve: tuple[tuple[date, float], ...]  # end-of-day equity points
    n_trades: int
    wins: int
    losses: int
    win_rate: float | None
    total_r: float
    avg_r: float | None
    expectancy_usd: float | None
    return_pct: float
    max_drawdown_pct: float
    # Cost attribution (#232) — kept split so the page can show where the money actually went.
    commission_usd: float  # IBKR's own line, all trades
    fees_usd: float  # exchange + clearing + TAF + SEC, all trades
    data_fees_usd: float  # market-data subscription, charged monthly net of the waiver
    total_costs_usd: float  # broker costs only (commission + fees + data) — VPS/tax are separate
    # Getting-paid layer — withdrawals to you, UK CGT reserved, VPS running cost (all also in GBP).
    withdrawals_usd: float
    withdrawals_gbp: float
    tax_paid_usd: float
    tax_paid_gbp: float
    vps_costs_usd: float
    vps_costs_gbp: float
    net_take_home_gbp: float  # what actually reached your bank = sum of withdrawals in GBP
    cash_flows: tuple[CashFlow, ...]  # the dated withdrawal / tax / VPS schedule
    # Qualifying setups the book didn't take, each tagged with why (see SkippedTrade).
    skipped: tuple[SkippedTrade, ...]
    # Sum over the CAP-dropped ones only — what a wider max_trades_per_day would have let us take
    # (#230). Deliberately excludes "unaffordable" skips so this stays the answer to one question.
    skipped_total_r: float
