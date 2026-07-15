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
- **Size** — capital-based: ``floor(opening_equity × fraction / entry)``. Both of the day's trades
  size off the *day's opening equity* (they're concurrent positions committed before either
  resolves), so 50% × 2 fully deploys the account.
- **Exit** — a fixed R target with an optional breakeven arm, simulated bar-by-bar with the same
  conservative stop-first / gap-through convention as :mod:`rmetrics`. Commission + exit slippage
  are netted out so the equity curve is honest at ~$250 notional.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime

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


def size_position(equity: float, entry_price: float, fraction: float) -> int:
    """Whole-share quantity for a capital-based position: ``floor(equity × fraction / entry)``."""
    if entry_price <= 0:
        return 0
    return int((equity * fraction) // entry_price)


def commission(qty: int, per_share: float, minimum: float) -> float:
    """IBKR-style per-order-side commission: ``max(minimum, qty × per_share)``."""
    return round(max(minimum, qty * per_share), 4)


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
    commission_usd: float
    net_pnl_usd: float
    equity_before: float
    equity_after: float


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
    out: list[CandidateTrade] = []
    for row in opps.iter_rows(named=True):
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


def simulate_portfolio(
    candidates_by_day: Sequence[tuple[date, Sequence[CandidateTrade]]],
    s: Settings,
    *,
    target_r: float | None = None,
    breakeven_r: float | None = None,
) -> PortfolioResult:
    """Walk days chronologically, taking ≤N trades/day sized off that day's opening equity.

    ``candidates_by_day`` need not be pre-sorted; days are ordered here and each day's trades are
    taken in trigger-time order up to ``portfolio_max_trades_per_day``. ``target_r`` /
    ``breakeven_r`` default to the configured values (so the caller can sweep them here)."""
    tr = s.portfolio_target_r if target_r is None else target_r
    be = s.portfolio_breakeven_r if breakeven_r is None else breakeven_r
    equity = s.portfolio_start_equity_usd
    trades: list[PaperTrade] = []
    curve: list[tuple[date, float]] = []
    peak = equity
    max_dd = 0.0

    for day, cands in sorted(candidates_by_day, key=lambda dc: dc[0]):
        opening_equity = equity  # both concurrent positions size off the day's open
        taken = sorted(cands, key=lambda c: c.trigger_at)[: s.portfolio_max_trades_per_day]
        for c in taken:
            qty = size_position(opening_equity, c.entry_price, s.portfolio_position_fraction)
            if qty < 1:  # position too small to afford a share — skip, still logged by count
                continue
            outcome = c.exit_under(s, tr, be)
            gross = round(qty * (outcome.exit_price - c.entry_price), 4)
            comm = round(
                2 * commission(qty, s.portfolio_commission_per_share, s.portfolio_commission_min), 4
            )
            net = round(gross - comm, 4)
            before = equity
            equity = round(equity + net, 4)
            peak = max(peak, equity)
            if peak > 0:
                max_dd = max(max_dd, (peak - equity) / peak)
            trades.append(
                PaperTrade(
                    trading_date=c.trading_date,
                    symbol=c.symbol,
                    seg_id=c.seg_id,
                    run=c.run,
                    trigger_at=c.trigger_at,
                    entry_price=c.entry_price,
                    stop=c.stop,
                    qty=qty,
                    target_r=tr,
                    breakeven_r=be,
                    realized_r=outcome.realized_r,
                    reason=outcome.reason,
                    exit_price=outcome.exit_price,
                    gross_pnl_usd=gross,
                    commission_usd=comm,
                    net_pnl_usd=net,
                    equity_before=before,
                    equity_after=equity,
                )
            )
        curve.append((day, equity))

    n = len(trades)
    wins = sum(1 for t in trades if t.net_pnl_usd > 0)
    losses = sum(1 for t in trades if t.net_pnl_usd < 0)
    total_r = round(sum(t.realized_r for t in trades), 4)
    start = s.portfolio_start_equity_usd
    return PortfolioResult(
        start_equity=start,
        end_equity=equity,
        trades=tuple(trades),
        equity_curve=tuple(curve),
        n_trades=n,
        wins=wins,
        losses=losses,
        win_rate=round(wins / n, 4) if n else None,
        total_r=total_r,
        avg_r=round(total_r / n, 4) if n else None,
        expectancy_usd=round(sum(t.net_pnl_usd for t in trades) / n, 4) if n else None,
        return_pct=round((equity - start) / start, 4) if start else 0.0,
        max_drawdown_pct=round(max_dd, 4),
    )


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
