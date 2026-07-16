"""Portfolio simulation: select -> size -> simulate-exit, day by day, into a result (#230).

The decision code real shadow/paper mode will use. Split out of the old single-file
``portfolio.py`` (#259) with no behaviour change.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date, timedelta

from ..config import Settings
from .adaptive import _day_signal_r, best_target, expectancy_curve, risk_ladder, step_risk_rung
from .costs import size_position, trade_costs
from .ledgers import _DataFeeLedger, _TaxLedger, _VpsLedger, _WithdrawalLedger
from .models import CandidateTrade, PaperTrade, PortfolioResult, SkippedTrade


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
