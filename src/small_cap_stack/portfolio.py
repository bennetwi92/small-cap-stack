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
The book is compliant *by construction* rather than by simulating settlement: both trades size off
``opening_equity`` at 50% with a 2/day cap, so max daily buy notional
``= 2 × floor(0.50 × opening_equity / entry) × entry ≤ opening_equity``; and since every trade
closes same-day, no unsettled position is carried and T+1 opens each day settled. The cap *is*
the constraint — see ``test_settled_cash_invariant``, which fails loudly if the config is ever
changed such that ``position_fraction × max_trades_per_day > 1``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta

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
    total_costs_usd: float


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


def _take_day(
    day: date,
    cands: Sequence[CandidateTrade],
    equity: float,
    s: Settings,
    target_r: float,
    breakeven_r: float,
) -> list[PaperTrade]:
    """Take a single day's ≤N trades (trigger-time order), all sized off the day's opening equity.

    Both concurrent positions size off ``equity`` (the day's open) since they're committed before
    either resolves; equity accrues sequentially only for the running-balance bookkeeping."""
    opening_equity = equity
    taken = sorted(cands, key=lambda c: c.trigger_at)[: s.portfolio_max_trades_per_day]
    out: list[PaperTrade] = []
    for c in taken:
        qty = size_position(opening_equity, c.entry_price, s.portfolio_position_fraction)
        if qty < 1:  # position too small to afford a share — skip
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
    return out


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
        """Fee due *before* trading ``day``, i.e. charged when ``day`` opens a new month."""
        m = (day.year, day.month)
        if self._month is None:
            self._month = m
            return 0.0
        if m == self._month:
            return 0.0
        fee = self._settle()
        self._month = m
        return fee

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


def _finalize(
    trades: list[PaperTrade],
    curve: list[tuple[date, float]],
    s: Settings,
    end_equity: float,
    data_fees_usd: float,
) -> PortfolioResult:
    start = s.portfolio_start_equity_usd
    equity = end_equity
    # Drawdown walks trade-resolution equity. Month-rollover data fees are already folded into
    # `equity_before`/`equity_after` by the caller, so only the final month's fee (charged after the
    # last trade) sits outside this walk; `end_equity` carries it.
    peak, max_dd = start, 0.0
    for t in trades:
        peak = max(peak, t.equity_after)
        if peak > 0:
            max_dd = max(max_dd, (peak - t.equity_after) / peak)
    n = len(trades)
    total_r = round(sum(t.realized_r for t in trades), 4)
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
        return_pct=round((equity - start) / start, 4) if start else 0.0,
        max_drawdown_pct=round(max_dd, 4),
        commission_usd=round(sum(t.commission_usd for t in trades), 4),
        fees_usd=round(sum(t.fees_usd for t in trades), 4),
        data_fees_usd=round(data_fees_usd, 4),
        total_costs_usd=round(
            sum(t.commission_usd + t.fees_usd for t in trades) + data_fees_usd, 4
        ),
    )


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
    equity = s.portfolio_start_equity_usd
    trades: list[PaperTrade] = []
    curve: list[tuple[date, float]] = []
    fees = _DataFeeLedger(s)
    for day, cands in sorted(candidates_by_day, key=lambda dc: dc[0]):
        equity = round(equity - fees.roll(day), 4)
        day_trades = _take_day(day, cands, equity, s, tr, be)
        fees.observe(day_trades)
        trades.extend(day_trades)
        equity = day_trades[-1].equity_after if day_trades else equity
        curve.append((day, equity))
    equity = round(equity - fees.close(), 4)
    if curve:  # the final month's fee lands on the last day
        curve[-1] = (curve[-1][0], equity)
    return _finalize(trades, curve, s, equity, fees.total_charged)


def simulate_portfolio_adaptive(
    candidates_by_day: Sequence[tuple[date, Sequence[CandidateTrade]]],
    s: Settings,
    *,
    breakeven_r: float | None = None,
) -> tuple[PortfolioResult, list[tuple[date, float]]]:
    """Walk days chronologically, re-fitting the R target each day from a TRAILING window.

    Each day's target = the highest-expectancy grid target over the candidates from the prior
    ``portfolio_adaptive_window_days`` days (strictly before today — no look-ahead). Until at least
    ``portfolio_adaptive_min_samples`` trailing candidates exist the target falls back to the
    configured ``portfolio_target_r``. Returns the book plus the per-day (date, chosen_target) list
    so the page can show how the target drifted. Overfit is real at low N — the window + a
    plateau-preferring :func:`best_target` are the guards, not a cure."""
    be = s.portfolio_breakeven_r if breakeven_r is None else breakeven_r
    grid = list(s.portfolio_target_grid)
    days = sorted(candidates_by_day, key=lambda dc: dc[0])
    equity = s.portfolio_start_equity_usd
    trades: list[PaperTrade] = []
    curve: list[tuple[date, float]] = []
    chosen: list[tuple[date, float]] = []
    fees = _DataFeeLedger(s)

    for i, (day, cands) in enumerate(days):
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
        equity = round(equity - fees.roll(day), 4)
        day_trades = _take_day(day, cands, equity, s, target, be)
        fees.observe(day_trades)
        trades.extend(day_trades)
        equity = day_trades[-1].equity_after if day_trades else equity
        curve.append((day, equity))
    equity = round(equity - fees.close(), 4)
    if curve:  # the final month's fee lands on the last day
        curve[-1] = (curve[-1][0], equity)
    return _finalize(trades, curve, s, equity, fees.total_charged), chosen


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


def _book_json(
    res: PortfolioResult, daily_targets: list[tuple[date, float]] | None
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
        },
        "equity_curve": [{"date": d.isoformat(), "equity": e} for d, e in res.equity_curve],
        "trades": [_trade_json(t) for t in res.trades],
    }
    if daily_targets is not None:
        book["daily_targets"] = [{"date": d.isoformat(), "target": t} for d, t in daily_targets]
    return book


def build_portfolio_payload(
    store: Store, s: Settings, generated_utc: datetime
) -> dict[str, object]:
    """Build the ``portfolio.json`` the web page reads: the adaptive book plus a fixed-target sweep.

    Extracts every day's qualifying trades once, then simulates the adaptive (daily re-fit) book
    and one fixed-target book per selectable target — all server-side so the page needs no bars and
    no duplicated logic. Written to ``/data/dashboard`` at EOD and shipped by publish-dashboard."""
    by_day = [(d, extract_day_trades(store, s, d)) for d in collected_dates(store)]
    adaptive_res, daily_targets = simulate_portfolio_adaptive(by_day, s)
    # Selectable fixed targets: the adaptive grid widened with a couple of extremes for exploration.
    targets = sorted(set(s.portfolio_target_grid) | {1.0, 4.0, 5.0})
    books: dict[str, object] = {"adaptive": _book_json(adaptive_res, daily_targets)}
    for t in targets:
        books[f"{t:g}"] = _book_json(simulate_portfolio(by_day, s, target_r=t), None)
    return {
        "generated_utc": generated_utc.isoformat(),
        "start_equity": s.portfolio_start_equity_usd,
        "config": {
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
        },
        "targets": [f"{t:g}" for t in targets],
        "books": books,
    }
