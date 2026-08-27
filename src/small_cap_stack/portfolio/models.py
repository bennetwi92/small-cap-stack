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
    # Context + what the setup *offered*, carried from the same R-metrics the review/results pages
    # render so the book can't quote a different Max R than the chart (#390). All three are
    # properties of the candidate, not of a (target, breakeven) choice, so they survive the sweep.
    float_shares: int | None = None  # merged across sources by `report._funds_for` (fmp first)
    max_r: float | None = None  # peak favourable excursion in R — the ceiling `realized_r` chased
    max_gain_pct: float | None = None  # that same peak as a fraction of the entry price
    # Measurement caveats on `max_r` (#581), carried so the book can mark a trade unresolved rather
    # than reporting an assumption as a result. `same_bar_stop`: entry and stop fell on one bar, so
    # the R above is the conservative guess at intrabar order (wrong 38% of the time where 1-min
    # bars can check it, #583). `fill_above_entry_bar_high`: the fill sat above that bar's high, a
    # price that never printed (#555). Different defects — do not merge them into one boolean.
    same_bar_stop: bool = False
    fill_above_entry_bar_high: bool = False
    # What a finer bar grid said about that same-bar case (#583), when one was consulted:
    # "ran" | "confirmed_stop" | "ambiguous_same_minute" | "unresolved". None = never asked, which
    # is the live store's permanent answer today — it collects no 1-min bars. `same_bar_stop` stays
    # True either way: it is a fact about the 5-min grid, and this records what a finer one added.
    entry_resolution: str | None = None
    # Provenance (#430). ``"live"`` = the tracker watched this day happen and captured the bars from
    # the scanner in real time. ``"recon"`` = the day was rebuilt after the fact from purchased
    # vendor minute bars, with the scanner appearance *reconstructed* rather than observed. The two
    # are not interchangeable evidence — #428 measured the reconstruction's appearance timing at a
    # median −0.34 min but found the IBKR 50-row rank cap (SNDQ) unreproducible per-symbol — #460
    # since measured that cap as never binding at all (11 of 50 in pre-market), so a
    # reconstructed day can surface setups the live scanner would never have shown. Carrying the
    # label on the trade is what lets the book report the two populations apart instead of quietly
    # averaging them into one win rate.
    source: str = "live"  # "live" | "recon"

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
    # Sized full-buying-power (#694 follow-up): `qty = floor(opening_equity / entry_price)` — no
    # risk-fraction ceiling, no notional cap, so there is no longer a "which constraint bound" to
    # report. `risk_usd`/`risk_pct` are reported post-hoc — what this size actually put at risk
    # given the stop — for the dashboard; see costs.SizedPosition.
    risk_usd: float
    risk_pct: float
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
    # Carried from the candidate (#390) — see CandidateTrade. `max_r` is measured against the
    # *initial* stop over the rest of the day and knows nothing of this book's target or breakeven
    # stop, so it is the honest ceiling: `max_r - realized_r` is what this exit left on the table.
    float_shares: int | None = None
    max_r: float | None = None
    max_gain_pct: float | None = None
    same_bar_stop: bool = False  # see CandidateTrade — this trade's R is an assumption (#581)
    fill_above_entry_bar_high: bool = False  # see CandidateTrade (#555)
    entry_resolution: str | None = None  # see CandidateTrade (#583)
    source: str = "live"  # "live" | "recon" — carried from the candidate, see CandidateTrade


@dataclass(frozen=True)
class SkippedTrade:
    """A qualifying setup the book did **not** take — issue #230 follow-up.

    Four reasons, kept apart by ``skip_reason``:

    - ``"cap"`` — the day's ``max_trades_per_day`` was already filled by earlier (lower
      trigger-time) trades. This is the population the "what did the 2/day cap cost me?" R-log
      answers, and the only one the headline ``skipped_total_r`` counts.
    - ``"unaffordable"`` — it was selected, but full-buying-power ``size_position`` returned
      ``qty < 1``: the day's opening equity couldn't buy even one share at the entry price. These
      used to vanish into neither log (#251). Practically unreachable at the default book (it needs
      equity below the entry price, a near-total drawdown), but a silently dropped setup is worse
      than a rare one.
    - ``"throttled"`` — the adaptive kill-switch, not the cap and not the equity, is why it wasn't
      taken: the day sat at activity rung 0, so nothing was taken at all. Keeping these out of the
      two populations above is deliberate and predates this reason — attributing a kill-switch day
      to the cap would inflate "what the
      cap cost me", and calling a throttled rung "unaffordable" would blame the trader's equity for
      the ladder's decision. What was wrong was leaving them out of the log *entirely* (#465): a
      qualifying setup then appeared nowhere on the page, and the combined book silently lost three
      live setups the live-only book trades.
    - ``"day_stopped"`` — ``portfolio_daily_loss_limit_r`` (#650, ships disabled at 0.0) is why it
      wasn't taken: the realised R of trades already known to have closed before this one's trigger
      had already reached the limit. No-lookahead: a concurrent trade still open at this trigger
      contributes nothing to that total, so this reason can only fire once an earlier trade's exit
      is knowable in real time.

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
    # why it wasn't taken: "cap" | "unaffordable" | "throttled" | "day_stopped"
    skip_reason: str = "cap"
    # Carried from the candidate (#390) — same meaning as on PaperTrade.
    float_shares: int | None = None
    max_r: float | None = None
    max_gain_pct: float | None = None
    same_bar_stop: bool = False  # see CandidateTrade — this trade's R is an assumption (#581)
    fill_above_entry_bar_high: bool = False  # see CandidateTrade (#555)
    entry_resolution: str | None = None  # see CandidateTrade (#583)
    source: str = "live"  # "live" | "recon" — carried from the candidate, see CandidateTrade


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
