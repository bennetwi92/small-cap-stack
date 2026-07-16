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
  risk target sizes smaller whenever the stop is tighter than ``risk_fraction / position_fraction``
  of the entry. In the *adaptive* book ``risk_fraction`` is itself throttled day-by-day by a
  kill-switch ladder (#239) — see :func:`simulate_portfolio_adaptive`.
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

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from .capture import Bar
from .clock import ET
from .config import Settings
from .report import day_chart_bars, day_opportunities, symbol_runs
from .rmetrics import compute_r_metrics
from .storage import Store

# --- --- Exit simulation (the tested core) --------------------------------------------


@dataclass(frozen=True)
class ExitOutcome:
    """The result of walking a filled trade to its exit under a fixed target + breakeven."""

    realized_r: float
    reason: str  # "target" | "stop" | "breakeven" | "close"
    exit_index: int
    exit_price: float


def simulate_exit(
    bars: Sequence[Bar],
    entry_price: float,
    stop: float,
    entry_index: int,
    *,
    target_r: float,
    breakeven_r: float = 0.0,
    tick_size: float = 0.01,
    exit_slippage_ticks: int = 0,
) -> ExitOutcome:
    """Walk a filled trade from its entry bar to a fixed-R target / stop / breakeven / close.

    Conventions mirror :func:`rmetrics._measure`: **stop-first** intrabar (a bar that breaches the
    active stop closes the trade before any favourable excursion is credited) and **gap-through** on
    the stop (a bar that opens below the stop fills at its open, not the stop). The target is a
    resting limit — filled at exactly ``target_price`` even on a gap-up over it (conservative:
    never credit the extra). Breakeven arms *after* the bar whose high reaches ``breakeven_r`` (no
    intrabar look-ahead) and moves the stop to ``entry_price``. ``exit_slippage_ticks`` widens
    stop/close fills (a limit target never slips). ``risk = entry_price - stop`` must be positive.
    """
    risk = entry_price - stop
    if risk <= 0:  # caller guarantees a valid setup; guard defensively
        raise ValueError("simulate_exit requires entry_price > stop (positive risk)")
    target_price = entry_price + target_r * risk
    be_arm_price = entry_price + breakeven_r * risk if breakeven_r > 0 else None
    slip = exit_slippage_ticks * tick_size
    active_stop = stop
    armed = False

    for k in range(entry_index, len(bars)):
        b = bars[k]
        if b.low <= active_stop:  # stop-first: breach closes the trade
            exit_price = (
                min(active_stop, b.open) - slip
            )  # gap-through: open-fill if it gapped below
            reason = "breakeven" if armed else "stop"
            return ExitOutcome(
                round((exit_price - entry_price) / risk, 4), reason, k, round(exit_price, 4)
            )
        if b.high >= target_price:  # resting limit fills at the target
            return ExitOutcome(target_r, "target", k, round(target_price, 4))
        if be_arm_price is not None and not armed and b.high >= be_arm_price:
            armed = True  # protect from the NEXT bar on (no same-bar look-ahead)
            active_stop = entry_price

    last = bars[-1]  # never resolved intraday -> mark to close at the final bar
    exit_price = last.close - slip
    return ExitOutcome(
        round((exit_price - entry_price) / risk, 4), "close", len(bars) - 1, round(exit_price, 4)
    )


# --- --- Sizing & costs ---------------------------------------------------------------


def size_position(
    equity: float,
    entry_price: float,
    stop: float,
    *,
    risk_fraction: float,
    max_position_fraction: float,
) -> int:
    """Risk-based whole-share quantity, capped at a max position notional (#237).

    Targets ``risk_fraction`` of equity at risk — ``qty × (entry − stop) ≈ equity × risk_fraction``,
    so ``risk_qty = floor(equity × risk_fraction / (entry − stop))`` — then caps the position at
    ``cap_qty = floor(equity × max_position_fraction / entry)`` so a tight stop can't size past the
    concentration / settled-cash limit. Returns ``min(risk_qty, cap_qty)``: the risk target binds
    on tight stops, the notional cap on wide ones. ``risk = entry − stop`` is guaranteed positive by
    the caller (candidates are pre-filtered on ``risk > 0``); a non-positive risk falls back to cap.
    """
    if entry_price <= 0:
        return 0
    cap_qty = int((equity * max_position_fraction) // entry_price)
    risk_per_share = entry_price - stop
    if risk_per_share <= 0:  # degenerate; caller guarantees risk > 0, cap-bound defensively
        return cap_qty
    risk_qty = int((equity * risk_fraction) // risk_per_share)
    return min(risk_qty, cap_qty)


def commission(qty: int, per_share: float, minimum: float) -> float:
    """IBKR-style per-order-side commission: ``max(minimum, qty × per_share)``.

    This is the IBKR line ONLY. Under tiered pricing the exchange/regulatory pass-throughs are
    unbundled and charged on top — see :func:`trade_costs` for the all-in figure."""
    return round(max(minimum, qty * per_share), 4)


@dataclass(frozen=True)
class TradeCosts:
    """All-in round-trip cost of one paper trade, split so the drag is visible, not buried.

    ``commission_usd`` is IBKR's own line; ``fees_usd`` is everything tiered pricing unbundles
    (exchange removal + clearing on both sides, FINRA TAF + SEC Section 31 on the sell)."""

    commission_usd: float
    fees_usd: float

    @property
    def total_usd(self) -> float:
        return round(self.commission_usd + self.fees_usd, 4)


def trade_costs(qty: int, entry_price: float, exit_price: float, s: Settings) -> TradeCosts:
    """Full IBKR tiered round-trip cost for ``qty`` shares (#232 §1).

    Both sides pay commission + exchange removal + clearing; only the sell pays TAF and SEC. The
    book is always liquidity-removing (stop-triggered entries, stop/market exits), so no
    add-liquidity rebate is ever credited."""
    if qty < 1:
        return TradeCosts(0.0, 0.0)
    comm = 2 * commission(qty, s.portfolio_commission_per_share, s.portfolio_commission_min)
    per_share_both = (
        2 * qty * (s.portfolio_exchange_fee_per_share + s.portfolio_clearing_fee_per_share)
    )
    taf = min(qty * s.portfolio_taf_per_share, s.portfolio_taf_max)
    sec = max(0.0, qty * exit_price) * s.portfolio_sec_fee_rate
    return TradeCosts(round(comm, 4), round(per_share_both + taf + sec, 4))


# --- --- Trade model ------------------------------------------------------------------


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


# --- --- Extraction (reads the store; reuses the report seams) ------------------------


def _qualify(
    rm_entry_index: int | None,
    rm_entry_price: float | None,
    rm_entry_fill: float | None,
    rm_stop: float | None,
    rm_risk: float | None,
    takeable: bool,
    day_bars: Sequence[Bar],
    s: Settings,
) -> bool:
    """Apply the #230 selection rules to one run's R-metrics. Pure for straightforward testing."""
    if not takeable:  # engine-v2 pass + triggered + not exhausted
        return False
    if rm_entry_index is None or rm_entry_price is None or rm_entry_fill is None:
        return False
    if rm_stop is None or rm_risk is None or rm_risk <= 0:
        return False
    if not (s.portfolio_entry_price_min <= rm_entry_fill <= s.portfolio_entry_price_max):
        return False
    trigger_bar = day_bars[rm_entry_index]
    return trigger_bar.start.astimezone(ET).time() < s.portfolio_premarket_cutoff


def extract_day_trades(store: Store, s: Settings, trading_date: date) -> list[CandidateTrade]:
    """Qualifying pre-market engine-v2 trades for one day, in trigger-time order.

    Reuses the EOD report's segmentation + R-metrics so the paper book never drifts from the
    review/results pages: same runs, same detector, same appearance/staleness/exhaustion gating."""
    opps = day_opportunities(store, trading_date)
    if opps.is_empty():
        return []
    bars_df = store.read("bars", dt=trading_date)
    scans = store.read("scanner_hits", dt=trading_date)
    excluded = {sym.upper() for sym in s.portfolio_exclude_symbols}
    out: list[CandidateTrade] = []
    for row in opps.iter_rows(named=True):
        if str(row["symbol"]).upper() in excluded:  # ETFs mis-captured pre-#226 — never a candidate
            continue
        oid = row["opportunity_id"]
        day_bars = day_chart_bars(bars_df, oid, s)
        if not day_bars:
            continue
        for run in symbol_runs(row, bars_df, scans, s):
            rm = compute_r_metrics(day_bars, s, first_hit=run.first_hit)
            if not _qualify(
                rm.entry_index,
                rm.entry_price,
                rm.entry_fill,
                rm.stop,
                rm.initial_risk,
                rm.takeable,
                day_bars,
                s,
            ):
                continue
            assert rm.entry_index is not None  # narrowed by _qualify
            assert rm.entry_price is not None and rm.entry_fill is not None
            assert rm.stop is not None and rm.initial_risk is not None
            out.append(
                CandidateTrade(
                    trading_date=trading_date,
                    symbol=row["symbol"],
                    seg_id=run.seg_id,
                    run=run.idx,
                    trigger_at=day_bars[rm.entry_index].start,
                    entry_price=rm.entry_price,
                    entry_fill=rm.entry_fill,
                    stop=rm.stop,
                    risk=rm.initial_risk,
                    entry_index=rm.entry_index,
                    bars=tuple(day_bars),
                )
            )
    out.sort(key=lambda c: c.trigger_at)
    return out


# --- --- Portfolio simulation ---------------------------------------------------------


def _select_day(cands: Sequence[CandidateTrade], s: Settings) -> list[CandidateTrade]:
    """The ≤N candidates a day actually takes, in trigger-time order.

    The single source of truth for *which* trades a day acts on — shared by sizing
    (:func:`_take_day`) and the risk-throttle signal (:func:`_day_signal_r`) so they never drift."""
    return sorted(cands, key=lambda c: c.trigger_at)[: s.portfolio_max_trades_per_day]


def _skipped(
    c: CandidateTrade, s: Settings, target_r: float, breakeven_r: float, skip_reason: str
) -> SkippedTrade:
    """A qualifying setup the book didn't take, with the outcome it would have had."""
    o = c.exit_under(s, target_r, breakeven_r)
    return SkippedTrade(
        trading_date=c.trading_date,
        symbol=c.symbol,
        seg_id=c.seg_id,
        run=c.run,
        trigger_at=c.trigger_at,
        entry_price=c.entry_price,
        stop=c.stop,
        target_r=target_r,
        breakeven_r=breakeven_r,
        realized_r=o.realized_r,
        reason=o.reason,
        exit_price=o.exit_price,
        skip_reason=skip_reason,
    )


def _take_day(
    day: date,
    cands: Sequence[CandidateTrade],
    equity: float,
    s: Settings,
    target_r: float,
    breakeven_r: float,
    *,
    risk_fraction: float | None = None,
) -> tuple[list[PaperTrade], list[SkippedTrade]]:
    """Take a single day's ≤N trades (trigger-time order), all sized off the day's opening equity.

    Both concurrent positions size off ``equity`` (the day's open) since they're committed before
    either resolves; equity accrues sequentially only for the running-balance bookkeeping.

    Returns the taken trades *and* the qualifying setups the book didn't take, in trigger order,
    each with the outcome it would have had at the same (target, breakeven) plus a ``skip_reason``
    (#251): ``"cap"`` for everything past the first N by trigger time — the "what did the cap cost
    me" log (#230) — and ``"unaffordable"`` for a selected setup that couldn't be sized to a single
    share at full risk. Callers wanting only the cap population must filter; ``_finalize`` and
    ``_book_json`` do.

    ``risk_fraction`` defaults to the configured value; the adaptive kill-switch passes the day's
    throttled fraction, and a 0 fraction sizes every position to 0 (the day takes no trades)."""
    rf = s.portfolio_risk_fraction if risk_fraction is None else risk_fraction
    opening_equity = equity
    # Ask _select_day rather than re-slicing here (#256). It is documented as the single source of
    # truth for which trades a day acts on, but only the throttle signal (_day_signal_r) actually
    # called it — this function re-implemented the same slice inline. They agreed, so nothing had
    # drifted yet; the point is that a future selection change (a tie-break, an affordability
    # pre-filter) applied in one place would silently desync the throttle from the real trades.
    taken = _select_day(cands, s)
    # The dropped set is the complement BY IDENTITY, not `ordered[len(taken):]`. The positional
    # form would quietly re-assume _select_day returns a trigger-time *prefix* — the very coupling
    # this change removes. Under a non-prefix selector (a tie-break, an affordability pre-filter:
    # the changes named above) it would log a taken trade as cap-dropped and lose the real one.
    taken_ids = {id(c) for c in taken}
    dropped = sorted((c for c in cands if id(c) not in taken_ids), key=lambda c: c.trigger_at)
    # On a rung-0 day nothing is taken at all, so the cap was never the binding constraint — the
    # throttle was. Logging these as "the cap cost me this" would inflate the page's headline with
    # kill-switch days.
    skipped = [_skipped(c, s, target_r, breakeven_r, "cap") for c in dropped] if rf > 0 else []
    out: list[PaperTrade] = []
    for c in taken:
        qty = size_position(
            opening_equity,
            c.entry_price,
            c.stop,
            risk_fraction=rf,
            max_position_fraction=s.portfolio_position_fraction,
        )
        if qty < 1:
            # Too small to afford a share — record it rather than dropping it on the floor (#251),
            # but ONLY when sizing at full configured risk. Any throttled rung can produce qty=0 on
            # a wide stop (rung 1's rf=0.025 is a $12.50 risk budget at $500 equity, so a
            # $15/share-risk setup sizes to 0 while the book is perfectly healthy). Calling that
            # "unaffordable" would tell the trader their equity was the constraint when the
            # kill-switch was. Throttled sizing — rung 0 included — is the ladder doing its job.
            if rf >= s.portfolio_risk_fraction:
                skipped.append(_skipped(c, s, target_r, breakeven_r, "unaffordable"))
            continue
        outcome = c.exit_under(s, target_r, breakeven_r)
        gross = round(qty * (outcome.exit_price - c.entry_price), 4)
        costs = trade_costs(qty, c.entry_price, outcome.exit_price, s)
        net = round(gross - costs.total_usd, 4)
        before = equity
        equity = round(equity + net, 4)
        out.append(
            PaperTrade(
                trading_date=c.trading_date,
                symbol=c.symbol,
                seg_id=c.seg_id,
                run=c.run,
                trigger_at=c.trigger_at,
                entry_price=c.entry_price,
                stop=c.stop,
                qty=qty,
                target_r=target_r,
                breakeven_r=breakeven_r,
                realized_r=outcome.realized_r,
                reason=outcome.reason,
                exit_price=outcome.exit_price,
                gross_pnl_usd=gross,
                commission_usd=costs.commission_usd,
                fees_usd=costs.fees_usd,
                net_pnl_usd=net,
                equity_before=before,
                equity_after=equity,
            )
        )
    # Cap-skips come from the day's LAST triggers and unaffordable ones are appended from among its
    # first, so the list would otherwise be out of trigger order — and the page reverses it for
    # "newest first". Sort so that promise holds.
    skipped.sort(key=lambda sk: sk.trigger_at)
    return out, skipped


def _next_month(m: tuple[int, int]) -> tuple[int, int]:
    """The calendar month after ``(year, month)``."""
    y, mo = m
    return (y + 1, 1) if mo == 12 else (y, mo + 1)


class _DataFeeLedger:
    """Accrues the monthly market-data subscription, waived above a commission threshold (#232 §4).

    Charged at month rollover and applied *inline* by the callers (not as a post-pass) so it lands
    in ``equity`` before the next day is sized — sizing is capital-based, so a fee that didn't
    compound would flatter the book. Months present in the data but with no trades are still
    charged: the subscription bills whether or not you trade, which is the entire point of #232."""

    def __init__(self, s: Settings) -> None:
        self._s = s
        self._month: tuple[int, int] | None = None
        self._commission = 0.0
        self.total_charged = 0.0

    def roll(self, day: date) -> float:
        """Fee due *before* trading ``day``: one per calendar month since the anchor.

        Walks month by month rather than settling once per *observed* rollover (#249). A month with
        zero collected dates — a full data outage — never produces a rollover of its own, so the
        old single-settle silently skipped it: June data, then September data, charged June and
        anchored September, dropping July and August entirely. The subscription bills whether or not
        you trade *and whether or not we collected*, which is the whole point of #232; the docstring
        claim that "months with no trades are still charged" only held for months with some day in
        them."""
        m = (day.year, day.month)
        if self._month is None:
            self._month = m
            return 0.0
        if m <= self._month:  # same month, or an out-of-order day (days arrive ascending)
            return 0.0
        total = 0.0
        while self._month != m:
            total += self._settle()  # intervening months carry no commission -> no waiver
            self._month = _next_month(self._month)
        return round(total, 4)

    def observe(self, trades: Sequence[PaperTrade]) -> None:
        self._commission += sum(t.commission_usd for t in trades)

    def close(self) -> float:
        """Fee for the final (possibly partial) month."""
        return self._settle() if self._month is not None else 0.0

    def _settle(self) -> float:
        waived = self._commission >= self._s.portfolio_market_data_waiver_usd
        fee = 0.0 if waived else self._s.portfolio_market_data_usd_per_month
        self._commission = 0.0
        self.total_charged = round(self.total_charged + fee, 4)
        return fee


# --- --- Getting-paid ledgers (withdrawals, tax, VPS) ---------------------------------
#
# All three follow the :class:`_DataFeeLedger` shape: settle at a period boundary and return a USD
# debit the caller folds into ``equity`` *before* the next day is sized (sizing is capital-based, so
# a charge that didn't compound would flatter the book). Each records a dated :class:`CashFlow` too.
# FX: ``portfolio_gbpusd_rate`` is GBP/USD (1 GBP = rate USD) — USD→GBP divides, GBP→USD multiplies.


class _VpsLedger:
    """The VPS running cost (~£10/mo), charged at month rollover like the market-data fee.

    Kept separate from :class:`_DataFeeLedger` — it's a different real-world expense (host infra,
    not IBKR data), has no waiver, and is denominated in GBP. Every month present in the data is
    charged whether or not it traded: the box bills regardless."""

    def __init__(self, s: Settings) -> None:
        self._s = s
        self._month: tuple[int, int] | None = None
        self.total_usd = 0.0
        self.total_gbp = 0.0
        self.events: list[CashFlow] = []

    def roll(self, day: date) -> float:
        """One charge per calendar month since the anchor — including months with no data (#249).

        The box bills regardless of whether we collected anything, so a data outage must not be a
        free month. Each gap month's CashFlow is dated at the start of the month it rolls into; the
        final transition keeps ``day`` itself, so a gapless run bills exactly as it did before."""
        m = (day.year, day.month)
        if self._month is None:
            self._month = m
            return 0.0
        if m <= self._month:
            return 0.0
        total = 0.0
        while self._month != m:
            nxt = _next_month(self._month)
            total += self._settle(day if nxt == m else date(nxt[0], nxt[1], 1))
            self._month = nxt
        return round(total, 4)

    def close(self, day: date) -> float:
        return self._settle(day) if self._month is not None else 0.0

    def _settle(self, day: date) -> float:
        gbp = round(self._s.portfolio_vps_gbp_per_month, 4)
        usd = round(gbp * self._s.portfolio_gbpusd_rate, 4)
        if usd <= 0:
            return 0.0
        self.total_gbp = round(self.total_gbp + gbp, 4)
        self.total_usd = round(self.total_usd + usd, 4)
        self.events.append(CashFlow(day, "vps", usd, gbp))
        return usd


class _TaxLedger:
    """UK CGT reserve on net realised gains, per tax year (6 Apr–5 Apr).

    ``observe`` accrues each day's net P&L into a running GBP gain for the current tax year;
    ``reserve_usd`` is the outstanding CGT accrued so far (used to hold cash back from withdrawals
    so the book keeps enough to pay HMRC). ``roll`` settles the prior year's bill at the 6-Apr
    boundary (debits it, records the CashFlow, resets the running gain + allowance); ``close``
    settles the final, possibly-partial year. Losses reduce the year's gain, floored at £0 within
    the year (cross-year loss carry-forward is deliberately not modelled — a documented
    simplification, and conservative since it never *lowers* the reserve). Real CGT is due the
    following 31 Jan; settling at year-end reserves earlier, the safe direction for take-home."""

    def __init__(self, s: Settings) -> None:
        self._s = s
        self._year_start: date | None = None  # 6-Apr start of the tax year currently accruing
        self._ytd_gain_gbp = 0.0
        self.total_usd = 0.0
        self.total_gbp = 0.0
        self.events: list[CashFlow] = []

    @staticmethod
    def _tax_year_start(day: date) -> date:
        """The 6-April start of the UK tax year containing ``day``."""
        boundary = date(day.year, 4, 6)
        return boundary if day >= boundary else date(day.year - 1, 4, 6)

    def observe(self, trades: Sequence[PaperTrade]) -> None:
        usd = sum(t.net_pnl_usd for t in trades)
        self._ytd_gain_gbp += usd / self._s.portfolio_gbpusd_rate

    def _cgt_gbp(self) -> float:
        taxable = max(0.0, self._ytd_gain_gbp - self._s.portfolio_cgt_annual_exempt_gbp)
        return taxable * self._s.portfolio_cgt_rate

    def reserve_usd(self) -> float:
        """Outstanding CGT accrued this year, in USD — cash to keep back from withdrawals."""
        return round(self._cgt_gbp() * self._s.portfolio_gbpusd_rate, 4)

    def roll(self, day: date) -> float:
        ys = self._tax_year_start(day)
        if self._year_start is None:
            self._year_start = ys
            return 0.0
        if ys == self._year_start:
            return 0.0
        fee = self._settle(day)
        self._year_start = ys
        self._ytd_gain_gbp = 0.0
        return fee

    def close(self, day: date) -> float:
        return self._settle(day) if self._year_start is not None else 0.0

    def _settle(self, day: date) -> float:
        gbp = round(self._cgt_gbp(), 4)
        usd = round(gbp * self._s.portfolio_gbpusd_rate, 4)
        if usd <= 0:
            return 0.0
        self.total_gbp = round(self.total_gbp + gbp, 4)
        self.total_usd = round(self.total_usd + usd, 4)
        self.events.append(CashFlow(day, "tax", usd, gbp))
        return usd


class _WithdrawalLedger:
    """Quarterly profit withdrawal above a high-water mark — how the strategy actually pays you.

    Every ``withdraw_cadence_months`` it pays out ``withdraw_fraction`` of the profit above the HWM,
    but never dips below ``withdraw_floor_usd`` (base capital / account viability) and never
    distributes cash reserved for tax. The HWM then ratchets to the post-withdrawal balance, so the
    next period only pays on genuinely new profit. Pays nothing while at/under the floor or
    underwater — which is why the whole layer is a no-op at the $500 start until the account grows
    past the floor."""

    def __init__(self, s: Settings) -> None:
        self._s = s
        self._hwm = s.portfolio_start_equity_usd
        self._anchor: tuple[int, int] | None = None  # (year, month) the cadence last reset at
        self.total_usd = 0.0
        self.total_gbp = 0.0
        self.events: list[CashFlow] = []

    def roll(self, day: date, equity: float, tax_reserve_usd: float) -> float:
        ym = (day.year, day.month)
        if self._anchor is None:
            self._anchor = ym
            return 0.0
        elapsed = (ym[0] - self._anchor[0]) * 12 + (ym[1] - self._anchor[1])
        if elapsed < self._s.portfolio_withdraw_cadence_months:
            return 0.0
        self._anchor = ym
        return self._withdraw(day, equity, tax_reserve_usd)

    def _withdraw(self, day: date, equity: float, tax_reserve_usd: float) -> float:
        profit = equity - self._hwm
        if profit <= 0 or equity <= self._s.portfolio_withdraw_floor_usd:
            return 0.0
        available = equity - self._s.portfolio_withdraw_floor_usd - tax_reserve_usd
        gross = round(min(self._s.portfolio_withdraw_fraction * profit, available), 4)
        if gross <= 0:
            return 0.0
        gbp = round(gross / self._s.portfolio_gbpusd_rate, 4)
        self._hwm = round(equity - gross, 4)
        self.total_usd = round(self.total_usd + gross, 4)
        self.total_gbp = round(self.total_gbp + gbp, 4)
        self.events.append(CashFlow(day, "withdrawal", gross, gbp))
        return gross


_CASH_FLOW_ORDER = {"withdrawal": 0, "tax": 1, "vps": 2}


def _finalize(
    trades: list[PaperTrade],
    skipped: list[SkippedTrade],
    curve: list[tuple[date, float]],
    s: Settings,
    end_equity: float,
    data_fees_usd: float,
    vps: _VpsLedger,
    tax: _TaxLedger,
    wd: _WithdrawalLedger,
) -> PortfolioResult:
    start = s.portfolio_start_equity_usd
    equity = end_equity
    # Drawdown walks the pure trading-P&L path (start + cumulative net trade P&L), so scheduled
    # cash-outs — withdrawals, the CGT bill, VPS/data fees — never masquerade as a strategy
    # drawdown. It measures the edge, not the cadence at which you take money off the table.
    trading = start
    peak, max_dd = start, 0.0
    for t in trades:
        trading = round(trading + t.net_pnl_usd, 4)
        peak = max(peak, trading)
        if peak > 0:
            max_dd = max(max_dd, (peak - trading) / peak)
    n = len(trades)
    total_r = round(sum(t.realized_r for t in trades), 4)
    withdrawals_usd = round(wd.total_usd, 4)
    cash_flows = tuple(
        sorted(
            [*wd.events, *tax.events, *vps.events],
            key=lambda cf: (cf.date, _CASH_FLOW_ORDER[cf.kind]),
        )
    )
    return PortfolioResult(
        start_equity=start,
        end_equity=equity,
        trades=tuple(trades),
        equity_curve=tuple(curve),
        n_trades=n,
        wins=sum(1 for t in trades if t.net_pnl_usd > 0),
        losses=sum(1 for t in trades if t.net_pnl_usd < 0),
        win_rate=round(sum(1 for t in trades if t.net_pnl_usd > 0) / n, 4) if n else None,
        total_r=total_r,
        avg_r=round(total_r / n, 4) if n else None,
        expectancy_usd=round(sum(t.net_pnl_usd for t in trades) / n, 4) if n else None,
        # Total-value return: add withdrawn cash back so paying yourself doesn't read as a loss,
        # while tax + VPS + broker costs (all already out of `end_equity`) legitimately reduce it.
        return_pct=round((equity + withdrawals_usd - start) / start, 4) if start else 0.0,
        max_drawdown_pct=round(max_dd, 4),
        commission_usd=round(sum(t.commission_usd for t in trades), 4),
        fees_usd=round(sum(t.fees_usd for t in trades), 4),
        data_fees_usd=round(data_fees_usd, 4),
        total_costs_usd=round(
            sum(t.commission_usd + t.fees_usd for t in trades) + data_fees_usd, 4
        ),
        withdrawals_usd=withdrawals_usd,
        withdrawals_gbp=round(wd.total_gbp, 4),
        tax_paid_usd=round(tax.total_usd, 4),
        tax_paid_gbp=round(tax.total_gbp, 4),
        vps_costs_usd=round(vps.total_usd, 4),
        vps_costs_gbp=round(vps.total_gbp, 4),
        net_take_home_gbp=round(wd.total_gbp, 4),
        cash_flows=cash_flows,
        skipped=tuple(skipped),
        # Cap-dropped only — see PortfolioResult.skipped_total_r.
        skipped_total_r=round(sum(sk.realized_r for sk in skipped if sk.skip_reason == "cap"), 4),
    )


def _run_book(
    days: list[tuple[date, Sequence[CandidateTrade]]],
    s: Settings,
    target_for_day: Callable[[int, date], float],
    breakeven_r: float,
    risk_for_day: Callable[[int, date, Sequence[CandidateTrade], float], float] | None = None,
) -> PortfolioResult:
    """The shared day-walk both books ride on — the differences are how the R target is chosen and,
    optionally, how the per-trade risk fraction is throttled.

    Each day, the four boundary ledgers settle *before* the day is sized (so every charge / payout
    compounds into the capital that sizes the next trades): the VPS bill and market-data fee at
    month rollover, the CGT bill at the 6-Apr tax-year boundary, then the quarterly withdrawal
    (which sees the post-tax equity and holds back the outstanding CGT reserve). Trades are then
    taken and their realised P&L accrued into the tax ledger. Final charges settle at close.

    ``risk_for_day`` (adaptive book only) receives the day's candidates + chosen target and returns
    the throttled risk fraction to size that day with; ``None`` sizes at the configured fraction."""
    equity = s.portfolio_start_equity_usd
    trades: list[PaperTrade] = []
    skipped: list[SkippedTrade] = []
    curve: list[tuple[date, float]] = []
    data_fees = _DataFeeLedger(s)
    vps = _VpsLedger(s)
    tax = _TaxLedger(s)
    wd = _WithdrawalLedger(s)
    for i, (day, cands) in enumerate(days):
        equity = round(equity - vps.roll(day), 4)
        equity = round(equity - data_fees.roll(day), 4)
        equity = round(equity - tax.roll(day), 4)  # settle CGT before deciding the withdrawal
        equity = round(equity - wd.roll(day, equity, tax.reserve_usd()), 4)
        target = target_for_day(i, day)
        risk_fraction = None if risk_for_day is None else risk_for_day(i, day, cands, target)
        day_trades, day_skipped = _take_day(
            day, cands, equity, s, target, breakeven_r, risk_fraction=risk_fraction
        )
        data_fees.observe(day_trades)
        tax.observe(day_trades)
        trades.extend(day_trades)
        skipped.extend(day_skipped)
        equity = day_trades[-1].equity_after if day_trades else equity
        curve.append((day, equity))
    equity = round(equity - data_fees.close(), 4)
    if days:  # settle the final, possibly-partial VPS month and CGT year on the last day
        last = days[-1][0]
        equity = round(equity - vps.close(last), 4)
        equity = round(equity - tax.close(last), 4)
    if curve:  # the final boundary charges land on the last day
        curve[-1] = (curve[-1][0], equity)
    return _finalize(trades, skipped, curve, s, equity, data_fees.total_charged, vps, tax, wd)


def simulate_portfolio(
    candidates_by_day: Sequence[tuple[date, Sequence[CandidateTrade]]],
    s: Settings,
    *,
    target_r: float | None = None,
    breakeven_r: float | None = None,
) -> PortfolioResult:
    """Walk days chronologically at a FIXED target, taking ≤N trades/day off the day's open equity.

    ``target_r`` / ``breakeven_r`` default to the configured values (so the caller can sweep them —
    the manual-slider path). For the daily re-fit path see :func:`simulate_portfolio_adaptive`."""
    tr = s.portfolio_target_r if target_r is None else target_r
    be = s.portfolio_breakeven_r if breakeven_r is None else breakeven_r
    days = sorted(candidates_by_day, key=lambda dc: dc[0])
    return _run_book(days, s, lambda i, day: tr, be)


def simulate_portfolio_adaptive(
    candidates_by_day: Sequence[tuple[date, Sequence[CandidateTrade]]],
    s: Settings,
    *,
    breakeven_r: float | None = None,
) -> tuple[PortfolioResult, list[tuple[date, float]], list[tuple[date, float]]]:
    """Walk days chronologically, re-fitting BOTH the R target and the risk fraction each day.

    **Target** — each day's target = the highest-expectancy grid target over the candidates from the
    prior ``portfolio_adaptive_window_days`` days (strictly before today — no look-ahead). Until at
    least ``portfolio_adaptive_min_samples`` trailing candidates exist the target falls back to the
    configured ``portfolio_target_r``. Overfit is real at low N — the window + a plateau-preferring
    :func:`best_target` are the guards, not a cure.

    **Risk (kill-switch)** — the per-trade risk fraction walks the :func:`risk_ladder` (0 →
    ``portfolio_risk_fraction`` over ``portfolio_risk_rungs`` rungs). It starts at full risk (top
    rung) and steps ONE rung only after ``portfolio_risk_step_days`` net-positive days in a row (up)
    or the same run of net-negative days (down) — see :func:`step_risk_rung`; the day's result is
    its aggregate realised R over its qualifying setups, and a flat/no-setup day holds the streak.
    The signal is *size-independent* — the day's would-be setups, not its sized P&L — so a
    book throttled to the 0 rung (which takes no trades) still re-arms once setups start working.
    Applying today's rung, then stepping from today's result, keeps it causal (no look-ahead).

    Returns the book plus the per-day ``(date, chosen_target)`` and ``(date, risk_fraction)`` lists
    so the page can show how each knob drifted."""
    be = s.portfolio_breakeven_r if breakeven_r is None else breakeven_r
    grid = list(s.portfolio_target_grid)
    ladder = risk_ladder(s)
    rung = len(ladder) - 1  # start at full risk — the kill-switch cuts DOWN from the top
    streak = 0  # signed run of consecutive decisive days (see step_risk_rung)
    days = sorted(candidates_by_day, key=lambda dc: dc[0])
    chosen: list[tuple[date, float]] = []
    daily_risk: list[tuple[date, float]] = []
    rung_state = [rung]  # mutable so the risk closure can step it each day
    streak_state = [streak]  # signed run of decisive days, carried across the closure

    def target_for_day(i: int, day: date) -> float:
        window_start = day - timedelta(days=s.portfolio_adaptive_window_days)
        trailing = [
            c
            for d, cs in days[:i]
            if d >= window_start
            for c in cs  # strictly-prior days only
        ]
        target = s.portfolio_target_r
        if len(trailing) >= s.portfolio_adaptive_min_samples:
            pick = best_target(expectancy_curve(trailing, s, target_grid=grid, breakeven_r=be))
            if pick is not None:
                target = pick.target_r
        chosen.append((day, target))
        return target

    def risk_for_day(i: int, day: date, cands: Sequence[CandidateTrade], target: float) -> float:
        # Apply today's rung, then step the ladder for TOMORROW from today's setups
        # (size-independent → the book re-arms even when throttled to the 0 rung).
        risk_fraction = ladder[rung_state[0]]
        daily_risk.append((day, risk_fraction))
        signal = _day_signal_r(_select_day(cands, s), s, target, be)
        rung_state[0], streak_state[0] = step_risk_rung(
            rung_state[0], streak_state[0], signal, len(ladder), s.portfolio_risk_step_days
        )
        return risk_fraction

    res = _run_book(days, s, target_for_day, be, risk_for_day)
    return res, chosen, daily_risk


# --- --- Adaptive target optimiser ----------------------------------------------------


@dataclass(frozen=True)
class TargetStat:
    target_r: float
    breakeven_r: float
    n: int
    hit_rate: float | None  # fraction that reached the target
    expectancy_r: float | None  # mean realised R (the objective)


def expectancy_curve(
    candidates: Sequence[CandidateTrade],
    s: Settings,
    *,
    target_grid: Sequence[float],
    breakeven_r: float = 0.0,
) -> list[TargetStat]:
    """Per-target mean realised R over a set of trades — the input to the adaptive target choice.

    Expectancy is the *actual* mean realised R under the full exit model (breakeven, mark-to-close,
    costs excluded here — this is the pre-cost strategy edge), not the idealised ``p·T − (1−p)``, so
    breakeven and partial mark-to-close outcomes are captured exactly. Feed a trailing window of
    candidates to re-fit the target as the regime drifts."""
    stats: list[TargetStat] = []
    n = len(candidates)
    for t in target_grid:
        outcomes = [c.exit_under(s, t, breakeven_r) for c in candidates]
        if not outcomes:
            stats.append(TargetStat(t, breakeven_r, 0, None, None))
            continue
        hits = sum(1 for o in outcomes if o.reason == "target")
        exp = sum(o.realized_r for o in outcomes) / n
        stats.append(TargetStat(t, breakeven_r, n, round(hits / n, 4), round(exp, 4)))
    return stats


def best_target(stats: Sequence[TargetStat]) -> TargetStat | None:
    """Pick the highest-expectancy target with a defined expectancy (ties → the smaller target).

    Smaller-on-tie is the robust choice: a lower target with equal expectancy reaches it more often
    (higher hit rate → less variance), which matters on a tiny, all-in account."""
    scored = [st for st in stats if st.expectancy_r is not None]
    if not scored:
        return None
    return max(scored, key=lambda st: (st.expectancy_r or 0.0, -st.target_r))


# --- --- Adaptive risk throttle (kill-switch, #239) -----------------------------------


def risk_ladder(s: Settings) -> tuple[float, ...]:
    """The risk-fraction rungs the kill-switch walks: 0 up to ``portfolio_risk_fraction``, evenly.

    ``portfolio_risk_rungs`` rungs *including* the 0 floor, so 3 → ``(0.0, 0.025, 0.05)`` at the 5%
    default. A single rung disables the throttle (always full risk). Fewer rungs ⇒ a faster wind-up
    back to full risk after a knock-down, which is the point of keeping the ladder coarse."""
    n = max(1, s.portfolio_risk_rungs)
    top = s.portfolio_risk_fraction
    if n == 1:
        return (top,)
    return tuple(round(top * i / (n - 1), 6) for i in range(n))


def step_risk_rung(
    rung: int, streak: int, day_signal: float, n_rungs: int, step_days: int
) -> tuple[int, int]:
    """Advance the ``(rung, streak)`` kill-switch state by one day — one rung per ``step_days`` run.

    ``streak`` is a signed count of consecutive *decisive* days in the current direction (positive =
    net-positive days, negative = net-negative days). A net-positive day extends or flips it up, a
    net-negative day extends or flips it down, and a **flat / no-setup day holds both rung and
    streak** — an information-less day carries no momentum, so "in a row" counts decisive days
    across flat gaps. Once the streak reaches ``±step_days`` the rung steps one notch that way
    (clamped to ``[0, n_rungs - 1]``) and the streak resets to 0, so each further move needs a fresh
    run. ``step_days=1`` steps on every decisive day (eager). ``day_signal`` is size-independent
    (see :func:`_day_signal_r`), so a book parked at rung 0 still climbs once setups work again."""
    if day_signal > 0:
        streak = streak + 1 if streak > 0 else 1  # extend or flip to a winning run
    elif day_signal < 0:
        streak = streak - 1 if streak < 0 else -1  # extend or flip to a losing run
    else:
        return rung, streak  # flat day: hold the rung AND the streak
    if streak >= step_days:
        return min(n_rungs - 1, rung + 1), 0
    if streak <= -step_days:
        return max(0, rung - 1), 0
    return rung, streak


def _day_signal_r(
    taken: Sequence[CandidateTrade], s: Settings, target_r: float, breakeven_r: float
) -> float:
    """Aggregate realised R of a day's taken setups under ``(target_r, breakeven_r)``.

    The throttle's day result: size-independent (pure R), so it is defined even on days the book
    took no positions (the 0 rung), which is exactly what lets the kill-switch re-arm."""
    return round(sum(c.exit_under(s, target_r, breakeven_r).realized_r for c in taken), 4)


# --- --- JSON payload for the web page ------------------------------------------------


def collected_dates(store: Store) -> list[date]:
    """Every trading date with a captured opportunity, ascending (compute-on-read)."""
    import polars as pl

    opps = store.read("opportunities")
    if opps.is_empty() or "trading_date" not in opps.columns:
        return []
    vals = opps.select(pl.col("trading_date")).unique().to_series().to_list()
    return sorted(d for d in vals if d is not None)


def _trade_json(t: PaperTrade) -> dict[str, object]:
    return {
        "date": t.trading_date.isoformat(),
        "symbol": t.symbol,
        "seg_id": t.seg_id,
        "run": t.run,
        "trigger_at": t.trigger_at.astimezone(ET).isoformat(),
        "entry": t.entry_price,
        "stop": t.stop,
        "qty": t.qty,
        "target_r": t.target_r,
        "realized_r": t.realized_r,
        "reason": t.reason,
        "exit_price": t.exit_price,
        "gross_pnl": t.gross_pnl_usd,
        "costs": round(t.commission_usd + t.fees_usd, 4),
        "net_pnl": t.net_pnl_usd,
        "equity_after": t.equity_after,
    }


def _skipped_json(sk: SkippedTrade) -> dict[str, object]:
    return {
        "date": sk.trading_date.isoformat(),
        "symbol": sk.symbol,
        "seg_id": sk.seg_id,
        "run": sk.run,
        "trigger_at": sk.trigger_at.astimezone(ET).isoformat(),
        "entry": sk.entry_price,
        "stop": sk.stop,
        "target_r": sk.target_r,
        "realized_r": sk.realized_r,
        "reason": sk.reason,
        "exit_price": sk.exit_price,
        "skip_reason": sk.skip_reason,
    }


def _book_json(
    res: PortfolioResult,
    daily_targets: list[tuple[date, float]] | None,
    daily_risk: list[tuple[date, float]] | None = None,
) -> dict[str, object]:
    book: dict[str, object] = {
        "stats": {
            "n_trades": res.n_trades,
            "wins": res.wins,
            "losses": res.losses,
            "win_rate": res.win_rate,
            "total_r": res.total_r,
            "avg_r": res.avg_r,
            "expectancy_usd": res.expectancy_usd,
            "end_equity": res.end_equity,
            "return_pct": res.return_pct,
            "max_drawdown_pct": res.max_drawdown_pct,
            "commission_usd": res.commission_usd,
            "fees_usd": res.fees_usd,
            "data_fees_usd": res.data_fees_usd,
            "total_costs_usd": res.total_costs_usd,
            # Getting-paid layer.
            "withdrawals_usd": res.withdrawals_usd,
            "withdrawals_gbp": res.withdrawals_gbp,
            "tax_paid_usd": res.tax_paid_usd,
            "tax_paid_gbp": res.tax_paid_gbp,
            "vps_costs_usd": res.vps_costs_usd,
            "vps_costs_gbp": res.vps_costs_gbp,
            "net_take_home_gbp": res.net_take_home_gbp,
            # Cap-only: the page's note asks "what did the N/day cap cost me?", so mixing the
            # unaffordable population into these would make it misattribute (#251).
            "skipped_count": sum(1 for sk in res.skipped if sk.skip_reason == "cap"),
            "skipped_total_r": res.skipped_total_r,
            "unaffordable_count": sum(1 for sk in res.skipped if sk.skip_reason == "unaffordable"),
        },
        "equity_curve": [{"date": d.isoformat(), "equity": e} for d, e in res.equity_curve],
        "trades": [_trade_json(t) for t in res.trades],
        "skipped": [_skipped_json(sk) for sk in res.skipped],
        "cash_flows": [
            {"date": cf.date.isoformat(), "kind": cf.kind, "usd": cf.usd, "gbp": cf.gbp}
            for cf in res.cash_flows
        ],
    }
    if daily_targets is not None:
        book["daily_targets"] = [{"date": d.isoformat(), "target": t} for d, t in daily_targets]
    if daily_risk is not None:
        book["daily_risk"] = [{"date": d.isoformat(), "risk": r} for d, r in daily_risk]
    return book


# --- --- Per-day candidate cache (issue: backfill-dashboard-perf) ---------------------
#
# The portfolio book is *cross-day*, so :func:`build_portfolio_payload` needs every collected day's
# qualifying trades. Extracting one day (segment + R-metrics per opportunity) costs about as much
# as one EOD report, so rebuilding the whole book from scratch on *every single-date dashboard
# backfill* silently did full-archive-scale work — the per-date backfill that should take seconds
# took minutes as history grew (the very ``--all`` workload CLAUDE.md warns off the box). A day's
# candidates are a pure function of that day's raw partitions + the settings that drive extraction,
# and the raw store is append-only immutable, so we cache each day's extracted candidates on disk
# keyed by a fingerprint of (those partition files, the whole settings model). A single-date
# backfill then re-extracts only the day that changed and reads the rest back from cache; any
# settings change or late-arriving/backfilled partition shifts the fingerprint and forces a correct
# re-extract, so compute-on-read is preserved. The cache lives under ``<data_dir>/cache`` (NOT
# ``dashboard/``, which publish-dashboard force-pushes wholesale to a public branch) and is fully
# regenerable.
_CANDIDATE_CACHE_SUBDIR = ("cache", "portfolio_candidates")
_EXTRACT_DATASETS = ("opportunities", "bars", "scanner_hits")


def portfolio_candidate_cache_dir(s: Settings) -> Path:
    """Directory holding the per-day extracted-candidate cache — off the published dashboard dir."""
    return s.data_dir.joinpath(*_CANDIDATE_CACHE_SUBDIR)


def _settings_fingerprint(s: Settings) -> str:
    """Hash the whole settings model: any change (price band, cutoff, excludes, tick size, or an
    engine param feeding ``symbol_runs`` / ``compute_r_metrics``) may alter extraction, and hashing
    everything can't miss one — a change just triggers one correct re-extract across all days."""
    body = json.dumps(s.model_dump(mode="json"), sort_keys=True, default=str)
    return hashlib.sha256(body.encode()).hexdigest()


def _day_fingerprint(store: Store, s: Settings, trading_date: date, settings_fp: str) -> str:
    """Fingerprint the day's extraction inputs: the raw partition files (name/size/mtime) that
    ``extract_day_trades`` reads, plus the settings hash. Append-only immutable parts mean a stable
    fingerprint until a new part lands for the date (a late backfill), which correctly busts it."""
    parts: dict[str, list[tuple[str, int, int]]] = {}
    for dataset in _EXTRACT_DATASETS:
        root = store.data_dir / dataset / f"dt={trading_date.isoformat()}"
        files = sorted(root.glob("**/*.parquet"))
        parts[dataset] = [(p.name, (st := p.stat()).st_size, st.st_mtime_ns) for p in files]
    body = json.dumps({"settings": settings_fp, "partitions": parts}, sort_keys=True)
    return hashlib.sha256(body.encode()).hexdigest()


def _bar_to_json(b: Bar) -> list[object]:
    return [b.start.isoformat(), b.open, b.high, b.low, b.close, b.volume]


def _bar_from_json(r: list[Any]) -> Bar:
    return Bar(
        start=datetime.fromisoformat(str(r[0])),
        open=float(r[1]),
        high=float(r[2]),
        low=float(r[3]),
        close=float(r[4]),
        volume=float(r[5]),
    )


def _candidate_to_json(c: CandidateTrade) -> dict[str, Any]:
    return {
        "trading_date": c.trading_date.isoformat(),
        "symbol": c.symbol,
        "seg_id": c.seg_id,
        "run": c.run,
        "trigger_at": c.trigger_at.isoformat(),
        "entry_price": c.entry_price,
        "entry_fill": c.entry_fill,
        "stop": c.stop,
        "risk": c.risk,
        "entry_index": c.entry_index,
        "bars": [_bar_to_json(b) for b in c.bars],
    }


def _candidate_from_json(d: dict[str, Any]) -> CandidateTrade:
    return CandidateTrade(
        trading_date=date.fromisoformat(str(d["trading_date"])),
        symbol=str(d["symbol"]),
        seg_id=str(d["seg_id"]),
        run=int(d["run"]),
        trigger_at=datetime.fromisoformat(str(d["trigger_at"])),
        entry_price=float(d["entry_price"]),
        entry_fill=float(d["entry_fill"]),
        stop=float(d["stop"]),
        risk=float(d["risk"]),
        entry_index=int(d["entry_index"]),
        bars=tuple(_bar_from_json(b) for b in d["bars"]),
    )


def _read_candidate_cache(path: Path, fingerprint: str) -> list[CandidateTrade] | None:
    """Return cached candidates iff the file parses and its fingerprint matches; else None."""
    if not path.exists():
        return None
    try:
        loaded = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(loaded, dict) or loaded.get("fingerprint") != fingerprint:
        return None
    cands = loaded.get("candidates")
    if not isinstance(cands, list):
        return None
    try:
        return [_candidate_from_json(c) for c in cands]
    except (KeyError, ValueError, TypeError):  # a schema change in the cached shape → re-extract
        return None


def _write_candidate_cache(path: Path, fingerprint: str, cands: Sequence[CandidateTrade]) -> None:
    """Atomically persist a day's candidates + fingerprint (tmp + os.replace, like write_json)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"fingerprint": fingerprint, "candidates": [_candidate_to_json(c) for c in cands]}
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload))
    os.replace(tmp, path)


def _extract_day_trades_cached(
    store: Store,
    s: Settings,
    trading_date: date,
    cache_dir: Path | None,
    settings_fp: str,
    *,
    force: bool,
) -> list[CandidateTrade]:
    """:func:`extract_day_trades` with a fingerprinted on-disk cache (``cache_dir=None`` disables).

    On a cache hit the day is not re-read/re-computed at all; ``force`` skips the read so a date the
    caller knows just changed is always re-extracted (and its fingerprint refreshed)."""
    if cache_dir is None:
        return extract_day_trades(store, s, trading_date)
    fingerprint = _day_fingerprint(store, s, trading_date, settings_fp)
    path = cache_dir / f"{trading_date.isoformat()}.json"
    if not force:
        cached = _read_candidate_cache(path, fingerprint)
        if cached is not None:
            return cached
    cands = extract_day_trades(store, s, trading_date)
    _write_candidate_cache(path, fingerprint, cands)
    return cands


def build_portfolio_payload(
    store: Store,
    s: Settings,
    generated_utc: datetime,
    *,
    cache_dir: Path | None = None,
    force_dates: Iterable[date] | None = None,
) -> dict[str, object]:
    """Build the ``portfolio.json`` the web page reads: the adaptive book plus a fixed-target sweep.

    Extracts every day's qualifying trades once, then simulates the adaptive (daily re-fit) book
    and one fixed-target book per selectable target — all server-side so the page needs no bars and
    no duplicated logic. Written to ``/data/dashboard`` at EOD and shipped by publish-dashboard.

    ``cache_dir`` enables the per-day candidate cache (see :func:`portfolio_candidate_cache_dir`) so
    a single-date backfill re-extracts only the day(s) in ``force_dates`` and reads the rest from
    cache instead of re-doing the whole archive; leave it None to always extract fresh."""
    settings_fp = _settings_fingerprint(s)
    force = set(force_dates or ())
    by_day = [
        (
            d,
            _extract_day_trades_cached(store, s, d, cache_dir, settings_fp, force=d in force),
        )
        for d in collected_dates(store)
    ]
    adaptive_res, daily_targets, daily_risk = simulate_portfolio_adaptive(by_day, s)
    # Selectable fixed targets: the adaptive grid widened with a couple of extremes for exploration.
    targets = sorted(set(s.portfolio_target_grid) | {1.0, 4.0, 5.0})
    books: dict[str, object] = {"adaptive": _book_json(adaptive_res, daily_targets, daily_risk)}
    for t in targets:
        books[f"{t:g}"] = _book_json(simulate_portfolio(by_day, s, target_r=t), None)
    return {
        "generated_utc": generated_utc.isoformat(),
        "start_equity": s.portfolio_start_equity_usd,
        "gbpusd_rate": s.portfolio_gbpusd_rate,
        "config": {
            "risk_fraction": s.portfolio_risk_fraction,
            "position_fraction": s.portfolio_position_fraction,
            "max_trades_per_day": s.portfolio_max_trades_per_day,
            "premarket_cutoff_et": s.portfolio_premarket_cutoff.isoformat(),
            "entry_price_min": s.portfolio_entry_price_min,
            "entry_price_max": s.portfolio_entry_price_max,
            "breakeven_r": s.portfolio_breakeven_r,
            "commission_per_share": s.portfolio_commission_per_share,
            "commission_min": s.portfolio_commission_min,
            "exchange_fee_per_share": s.portfolio_exchange_fee_per_share,
            "clearing_fee_per_share": s.portfolio_clearing_fee_per_share,
            "market_data_usd_per_month": s.portfolio_market_data_usd_per_month,
            "market_data_waiver_usd": s.portfolio_market_data_waiver_usd,
            "exit_slippage_ticks": s.portfolio_exit_slippage_ticks,
            "adaptive_window_days": s.portfolio_adaptive_window_days,
            "adaptive_min_samples": s.portfolio_adaptive_min_samples,
            # Getting-paid layer.
            "gbpusd_rate": s.portfolio_gbpusd_rate,
            "withdraw_fraction": s.portfolio_withdraw_fraction,
            "withdraw_cadence_months": s.portfolio_withdraw_cadence_months,
            "withdraw_floor_usd": s.portfolio_withdraw_floor_usd,
            "cgt_rate": s.portfolio_cgt_rate,
            "cgt_annual_exempt_gbp": s.portfolio_cgt_annual_exempt_gbp,
            "vps_gbp_per_month": s.portfolio_vps_gbp_per_month,
            # Adaptive risk throttle / kill-switch.
            "risk_rungs": s.portfolio_risk_rungs,
            "risk_ladder": list(risk_ladder(s)),
            "risk_step_days": s.portfolio_risk_step_days,
        },
        "targets": [f"{t:g}" for t in targets],
        "books": books,
    }
