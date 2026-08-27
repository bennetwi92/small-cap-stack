"""Forward projection: what the next year looks like, and when the book starts paying you.

The rest of :mod:`small_cap_stack.portfolio` is a *record* — what the book would have done over the
data already collected. This module is the only forward-looking piece, and it answers three
questions the record structurally cannot:

1. **What drawdown would I have to stomach?** The book's realised max DD is one draw from a
   distribution; over a year you get many more chances to hit a bad run. The projection reports the
   *distribution* of max drawdown across paths, so the number to plan around is a percentile
   ("1 in 10 years looks like −X%"), not the one sample that happened.
2. **When does it start paying out?** Withdrawals are dormant below ``portfolio_withdraw_floor_usd``
   and CGT below the annual exempt amount, so at the funded balance both layers read as zeros
   forever. The projection dates the first withdrawal and the first CGT bill.
3. **When can this replace the day rate?** Not by extrapolating the equity curve — the withdrawal
   policy takes money *out*, so the curve that pays you is not the curve that compounds. Instead:
   fit a growth rate, invert the "how much capital supports £X/month, net of CGT and running costs"
   arithmetic, and report the years to get there.

**Method — moving-block bootstrap over trading days.** Each historical trading day contributes one
sample: its trades' P&L as a fraction of that day's *opening* equity (which is what both concurrent
positions are sized against), plus the commission it generated as the same fraction. Days the book
sat out contribute an empty sample — a real outcome, and the one that sets the *cadence* of
everything below. A path draws days in contiguous blocks of ``portfolio_projection_block_days``
rather than one at a time, so a losing run stays a losing run; i.i.d. days would wash streaks out
and quietly halve the projected drawdown, which is the number this whole module exists to produce.

The four period ledgers (VPS, market data, CGT, withdrawals) are the *same objects* the historical
day-walk uses and settle in the same order, so a projected pound and a historical pound are
computed by one implementation.

**Stated assumptions** (all visible on the page, none of them hidden here):

- *Returns are scale-free.* A day that made 4% of equity is replayed as 4% at any balance. Real
  small-cap fills are not scale-free — this book trades ~$250 of notional today, and the same
  strategy at $250k would move the tape it is trading. **This is the single biggest reason the
  multi-year income arithmetic is an upper bound, not a forecast.**
- *Share granularity is not re-derived.* Each sample already embeds the integer-share rounding that
  applied at its own equity; replaying it elsewhere approximates.
- *The projection opens a fresh high-water mark and a fresh CGT year* at today's balance. Exactly
  right while the book has never withdrawn (it hasn't — the floor is well above it) and a small
  understatement of the first CGT bill if it starts mid-year with gains banked.
- *The kill-switch and the daily target re-fit are inside the samples, not re-run.* A resampled day
  carries whatever risk rung and target it was actually taken at.
"""

from __future__ import annotations

import math
import random
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from ..config import Settings
from ..market_calendar import is_trading_day
from .ledgers import _DataFeeLedger, _TaxLedger, _VpsLedger, _WithdrawalLedger
from .models import PortfolioResult

# Percentiles the fan chart and every "how bad does it get" tile are cut at. p5/p95 are the outer
# band, p25/p75 the inner one, p50 the line — five numbers is what a fan can show without becoming
# a smear, and they bracket the two questions asked (a bad year, a typical year).
_BANDS = (0.05, 0.25, 0.50, 0.75, 0.95)

# Above this annualised growth the income arithmetic stops meaning anything. A fixed-fractional
# book with a genuine edge compounds explosively on paper — 30-odd days that happened to break well
# can annualise to hundreds of times the account — and `capital_for_income` then divides by that
# rate and reports that £91k/yr needs roughly no capital at all. The arithmetic is right; the input
# is a small-sample artifact. Flagging it lets the page lead with "this rate is not survivable
# evidence" instead of printing $0 with a straight face. 10×/yr is far above anything a real book
# sustains and far below what a lucky month extrapolates to, so it separates the two cleanly.
_IMPLAUSIBLE_GROWTH = 9.0

# One equity sample per week of sessions. A point per session would be ~250 points per band per
# book — payload weight and pixel mush at a rail's width, for a curve whose shape is already fully
# described at weekly resolution.
_SAMPLE_EVERY = 5


@dataclass(frozen=True)
class DaySample:
    """One historical trading day, expressed scale-free so it can be replayed at any balance.

    ``returns`` is per *trade* rather than summed because the day's positions are sized off the same
    opening equity and settle against it independently; keeping them apart costs nothing and lets a
    future refinement (per-trade fee floors, say) reach them. An empty tuple is a day the book took
    nothing — kept, because sit-out days are most of the calendar and they set the pace at which
    everything downstream (the withdrawal floor, the tax year) arrives."""

    day: date
    returns: tuple[float, ...]  # net P&L / opening equity, per trade
    commission_frac: float  # IBKR commission / opening equity — feeds the market-data waiver


def day_samples(res: PortfolioResult) -> list[DaySample]:
    """Every collected trading day as a scale-free sample, ascending.

    Driven by ``equity_curve`` (one point per collected day) rather than by ``trades``, so days the
    book sat out are present instead of being silently dropped — see :class:`DaySample`."""
    by_day: dict[date, list[tuple[float, float, float]]] = defaultdict(list)
    for t in res.trades:
        by_day[t.trading_date].append((t.net_pnl_usd, t.commission_usd, t.equity_before))
    out: list[DaySample] = []
    for day, _equity in res.equity_curve:
        rows = by_day.get(day, [])
        if not rows:
            out.append(DaySample(day, (), 0.0))
            continue
        # The day's opening equity — what BOTH concurrent positions were sized against. The second
        # trade's own `equity_before` is post-first-trade, so using each trade's own would divide
        # the day's second result by the wrong base.
        opening = rows[0][2]
        if opening <= 0:
            out.append(DaySample(day, (), 0.0))
            continue
        out.append(
            DaySample(
                day,
                tuple(pnl / opening for pnl, _c, _e in rows),
                sum(c for _p, c, _e in rows) / opening,
            )
        )
    return out


def future_sessions(start: date, n: int, s: Settings) -> list[date]:
    """The next ``n`` trading sessions strictly after ``start``.

    Asks the exchange calendar while it can. It publishes ~1 year ahead, which is exactly the
    horizon here, so the tail of a 252-day projection can fall off the end — past that we degrade to
    weekdays rather than raising. The cost of the fallback is a handful of misplaced holidays a year
    out, which shifts nothing that matters (month and tax-year boundaries are calendar-dated, not
    session-counted); raising instead would take the whole page down for a horizon change."""
    out: list[date] = []
    d = start
    calendar_ok = True
    while len(out) < n:
        d += timedelta(days=1)
        if calendar_ok:
            try:
                if is_trading_day(d, extra_closed=s.calendar_closed_dates):
                    out.append(d)
                continue
            except Exception:  # past the calendar's published horizon — weekdays from here on
                calendar_ok = False
        if d.weekday() < 5 and d not in s.calendar_closed_dates:
            out.append(d)
    return out


def _pctile(sorted_vals: Sequence[float], q: float) -> float:
    """Linear-interpolated quantile of an already-sorted sequence (empty → 0.0)."""
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    pos = q * (len(sorted_vals) - 1)
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return sorted_vals[lo]
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


@dataclass(frozen=True)
class PathOutcome:
    """One Monte-Carlo path's summary. Only the sampled equity points are kept — a full per-session
    path × ``portfolio_projection_paths`` is memory the box does not have to spend (CLAUDE.md's
    OOM history is the reason this is spelled out)."""

    equity_at: tuple[float, ...]  # balance at each sampled session index
    end_equity: float
    compounded_end: float  # the reinvest-everything shadow book — see _simulate_path
    max_drawdown_pct: float
    withdrawals_gbp: float
    tax_gbp: float
    first_withdrawal: date | None
    first_tax: date | None


def _draw_days(
    samples: Sequence[DaySample], n_days: int, block: int, rng: random.Random
) -> list[DaySample]:
    """A moving-block bootstrap draw of ``n_days`` days — module docstring says why blocks."""
    out: list[DaySample] = []
    m = len(samples)
    step = max(1, block)
    while len(out) < n_days:
        start = rng.randrange(m)
        for k in range(min(step, n_days - len(out))):
            out.append(samples[(start + k) % m])
    return out


def _simulate_path(
    samples: Sequence[DaySample],
    sessions: Sequence[date],
    start_equity: float,
    s: Settings,
    rng: random.Random,
    sample_idx: frozenset[int],
) -> PathOutcome:
    """Walk one path day by day, settling the same four ledgers in the same order as ``_run_book``.

    Two balances are carried. ``equity`` is the real book: it pays every cost, reserves CGT and pays
    you out, so it is what the fan chart and the payout dates are read off. ``compounded`` is a
    shadow that takes the identical trade returns and the identical *fixed* costs but never
    withdraws and never settles CGT — the reinvest-everything path. Its growth rate is what the
    income arithmetic needs: "how long until the account is big enough" is a question about the
    account you did **not** spend, and reading a growth rate off a curve that is being withdrawn
    from would understate the wait every time."""
    draws = _draw_days(samples, len(sessions), s.portfolio_projection_block_days, rng)
    equity = start_equity
    compounded = start_equity
    # Drawdown walks the pure trading-P&L path, matching `_finalize`: a scheduled withdrawal is not
    # a drawdown, and painting one as such is how a book that is working reads like one that isn't.
    trading = start_equity
    peak = start_equity
    max_dd = 0.0
    vps = _VpsLedger(s)
    data = _DataFeeLedger(s)
    tax = _TaxLedger(s)
    wd = _WithdrawalLedger(s, hwm=start_equity)
    equity_at: list[float] = []

    # No `round()` inside this loop, unlike the historical day-walk. That walk rounds every step
    # because its numbers are shown to the trader as a ledger and must reconcile to the cent; these
    # are one draw of many, summarised into percentiles, so per-step rounding buys nothing and this
    # loop runs `paths × sessions × books` times — 1.1M iterations per publish. Rounding happens
    # once, at the outputs.
    for i, day in enumerate(sessions):
        # A wiped-out account is CLOSED, not overdrawn: once equity reaches zero the subscriptions
        # get cancelled and the box gets turned off, so the fixed charges stop with it. Letting them
        # keep billing walked dead paths to −$130 and dragged the whole low band negative — a
        # balance a cash account cannot have, on a chart whose floor is the thing being read.
        if equity <= 0:
            equity = 0.0
            if i in sample_idx:
                equity_at.append(0.0)
            continue
        # Fixed charges are flat USD and equity-independent, so the shadow book pays exactly what
        # the real one pays — no second set of ledgers needed to keep the two comparable.
        fixed = vps.roll(day) + data.roll(day)
        equity -= fixed
        equity -= tax.roll(day)  # settle CGT before deciding the withdrawal
        equity -= wd.roll(day, equity, tax.reserve_usd())
        if compounded > 0:
            compounded -= fixed

        rets = draws[i].returns
        if rets:
            day_return = sum(rets)
            if equity > 0:
                opening = equity  # both positions size off the day's OPEN, so costs scale off it
                pnl = opening * day_return
                equity += pnl
                trading += pnl
                if trading > peak:
                    peak = trading
                elif peak > 0:
                    max_dd = max(max_dd, (peak - trading) / peak)
                tax.observe_usd(pnl)
                data.observe_commission(opening * draws[i].commission_frac)
            if compounded > 0:
                compounded += compounded * day_return
        equity = max(equity, 0.0)
        compounded = max(compounded, 0.0)
        if i in sample_idx:
            equity_at.append(round(equity, 2))

    if (
        equity > 0
    ):  # a closed account settles nothing further — see the guard at the top of the loop
        equity -= data.close()
        if sessions:
            last = sessions[-1]
            equity -= vps.close(last)
            equity -= tax.close(last)
        equity = max(equity, 0.0)
    if equity_at:  # the closing charges land on the final sampled point, as they do on the curve
        equity_at[-1] = round(equity, 2)
    return PathOutcome(
        equity_at=tuple(equity_at),
        end_equity=round(equity, 4),
        compounded_end=round(compounded, 4),
        max_drawdown_pct=round(max_dd, 6),
        withdrawals_gbp=round(wd.total_gbp, 4),
        tax_gbp=round(tax.total_gbp, 4),
        first_withdrawal=wd.events[0].date if wd.events else None,
        first_tax=tax.events[0].date if tax.events else None,
    )


def _annualised(end: float, start: float, years: float) -> float:
    """Compound annual growth from ``start`` to ``end`` over ``years`` (a busted path → −100%)."""
    if start <= 0 or years <= 0:
        return 0.0
    return float((max(end, 0.0) / start) ** (1.0 / years)) - 1.0


def _first_event(dates: Sequence[date], n_paths: int) -> dict[str, Any]:
    """How often a dated event happened across paths, and when it typically landed."""
    return {
        "probability": round(len(dates) / n_paths, 4) if n_paths else 0.0,
        "median_date": dates[len(dates) // 2].isoformat() if dates else None,
    }


# ---------------------------------------------------------------------------------------------
# Income replacement: how much capital pays a given salary, and how long to get there.
# ---------------------------------------------------------------------------------------------


def day_rate_net_annual_gbp(s: Settings) -> float:
    """The contract income being replaced, after tax — the number the strategy has to beat.

    Net, not gross, because the thing on the other side of the comparison (a withdrawal) is also
    net of its own tax. Comparing a gross assignment rate to a post-CGT payout would flatter the
    day job by roughly the whole PAYE bill."""
    gross = s.portfolio_day_rate_gbp * s.portfolio_day_rate_days_per_year
    return round(gross * s.portfolio_day_rate_net_fraction, 2)


def annual_fixed_costs_gbp(s: Settings) -> float:
    """Running costs the account carries whether or not it trades: the box and the data feed."""
    data_gbp = s.portfolio_market_data_usd_per_month / s.portfolio_gbpusd_rate
    return round(12.0 * (s.portfolio_vps_gbp_per_month + data_gbp), 2)


def income_from_capital(capital_usd: float, growth: float, s: Settings) -> float:
    """Sustainable annual take-home (GBP) from ``capital_usd`` earning ``growth``, floored at zero.

    The forward direction of :func:`capital_for_income`, and the two are each other's inverse by
    test. Steady state: the account earns ``capital × growth``, pays its running costs and its CGT,
    and hands over the rest — capital held flat, which is what "living off it" means."""
    profit_gbp = capital_usd * growth / s.portfolio_gbpusd_rate
    cgt = max(0.0, profit_gbp - s.portfolio_cgt_annual_exempt_gbp) * s.portfolio_cgt_rate
    return max(0.0, profit_gbp - annual_fixed_costs_gbp(s) - cgt)


def capital_for_income(target_gbp_per_year: float, growth: float, s: Settings) -> float | None:
    """USD capital whose annual profit sustains ``target_gbp_per_year`` of take-home, at ``growth``.

    Steady state, not the configured 50%-of-new-profit cadence: this is "the account stops growing
    and pays out everything it earns", which is the honest definition of *replacing* an income.
    Working backwards from take-home ``T`` to gross annual profit ``P`` (GBP)::

        T = P − fixed − max(0, P − exempt) × cgt_rate

    Above the CGT exempt amount that inverts to ``P = (T + fixed − exempt × rate) / (1 − rate)``;
    below it the tax term vanishes and ``P = T + fixed``. Capital is then ``P × fx / growth``.

    Returns None when ``growth`` is not positive — a book with no edge needs infinite capital, and
    a number would be worse than an honest blank."""
    if growth <= 0:
        return None
    fixed = annual_fixed_costs_gbp(s)
    rate = s.portfolio_cgt_rate
    exempt = s.portfolio_cgt_annual_exempt_gbp
    profit = target_gbp_per_year + fixed
    if profit > exempt and rate < 1.0:
        profit = (target_gbp_per_year + fixed - exempt * rate) / (1.0 - rate)
    return round(profit * s.portfolio_gbpusd_rate / growth, 2)


def years_to_capital(target_usd: float, start_usd: float, growth: float) -> float | None:
    """Years at ``growth`` to take ``start_usd`` to ``target_usd`` (0 when already there).

    None when the growth rate can't get there — non-positive growth, or a start at/below zero."""
    if start_usd <= 0 or target_usd <= 0:
        return None
    if start_usd >= target_usd:
        return 0.0
    if growth <= 0:
        return None
    return round(math.log(target_usd / start_usd) / math.log(1.0 + growth), 2)


# How far out the income ramp is plotted. It grows until the median line clears the day rate (so
# the crossing — the answer to "when can I stop contracting" — is always on screen) but stops at
# the cap, because a chart that runs to year 40 is drawing arithmetic, not a plan.
_RAMP_MIN_YEARS = 5
_RAMP_MAX_YEARS = 15


def income_ramp(
    start_usd: float, growths: dict[str, float], target_gbp: float, s: Settings
) -> dict[str, Any]:
    """Sustainable annual take-home, year by year, at each growth quartile — the ramp chart's data.

    This is the "when can I live off it" picture: reinvest everything, and at each year ask what the
    account could pay you if you *stopped* reinvesting then. The horizon self-sizes to include the
    year the median line crosses ``target_gbp``, so the crossing is never just off the right edge.

    Computed here rather than in the page because it is the same tax-and-costs arithmetic as the
    ladder — duplicating it in JavaScript would put an untested second copy of the CGT rules in
    front of the one number this whole view exists to produce."""
    mid = growths.get("p50", 0.0)
    horizon = _RAMP_MIN_YEARS
    if mid > 0:
        capital = capital_for_income(target_gbp, mid, s)
        needed = None if capital is None else years_to_capital(capital, start_usd, mid)
        if needed is not None:
            horizon = max(_RAMP_MIN_YEARS, min(_RAMP_MAX_YEARS, math.ceil(needed) + 1))
    years = list(range(horizon + 1))
    return {
        "years": years,
        "target_gbp": round(target_gbp, 2),
        "series": {
            key: [round(income_from_capital(start_usd * (1.0 + g) ** y, g, s), 2) for y in years]
            for key, g in growths.items()
        },
    }


# ---------------------------------------------------------------------------------------------
# The projection itself
# ---------------------------------------------------------------------------------------------


def _milestone(
    label: str, gbp_per_month: float, growth: float, start: float, s: Settings
) -> dict[str, Any]:
    """One rung of the income ladder: capital needed, and the wait at ``growth``."""
    per_year = gbp_per_month * 12.0
    capital = capital_for_income(per_year, growth, s)
    return {
        "label": label,
        "gbp_per_month": round(gbp_per_month, 2),
        "gbp_per_year": round(per_year, 2),
        "capital_usd": capital,
        "years": None if capital is None else years_to_capital(capital, start, growth),
        # The notional ONE position would carry at that capital. Full-buying-power sizing (#694
        # follow-up) means this is now the whole capital — the reality check the rest of the row
        # can't give you: the ladder assumes percentage returns are scale-free, and this column is
        # where you see what that assumption is actually claiming — that the same bull-flag entries
        # fill the same way at $X0,000 a clip on a 20M-float name. Judging that is the trader's
        # call, so the number is shown rather than a cap invented.
        "position_usd": None if capital is None else round(capital, 2),
    }


def build_projection(res: PortfolioResult, s: Settings) -> dict[str, Any]:
    """Project ``res`` forward ``portfolio_projection_days`` sessions and summarise the outcome.

    Returns the JSON-ready block ``portfolio.json`` carries per book. ``{"available": False, ...}``
    when there is nothing to resample from — a book with no collected days, or one whose days all
    sat out — because a fan chart drawn off zero samples is a straight line asserting certainty."""
    samples = day_samples(res)
    traded = [d for d in samples if d.returns]
    reason = None
    if not samples:
        reason = "No collected days yet."
    elif not traded:
        reason = "No trading days in the book yet — nothing to resample."
    if reason is not None:
        return {"available": False, "reason": reason}

    horizon = max(1, s.portfolio_projection_days)
    n_paths = max(1, s.portfolio_projection_paths)
    start_equity = res.end_equity
    last_day = res.equity_curve[-1][0] if res.equity_curve else samples[-1].day
    sessions = future_sessions(last_day, horizon, s)
    # Weekly sampling, and always the final session so the fan ends where the summary tiles do.
    idx = sorted(set(range(0, len(sessions), _SAMPLE_EVERY)) | {len(sessions) - 1})
    sample_idx = frozenset(idx)

    rng = random.Random(s.portfolio_projection_seed)
    paths = [
        _simulate_path(samples, sessions, start_equity, s, rng, sample_idx) for _ in range(n_paths)
    ]

    # Per-sampled-session percentile bands. Transposed once rather than per band.
    by_point: list[list[float]] = [sorted(p.equity_at[k] for p in paths) for k in range(len(idx))]
    bands = {f"p{int(q * 100)}": [round(_pctile(col, q), 2) for col in by_point] for q in _BANDS}

    dd = sorted(p.max_drawdown_pct for p in paths)
    ends = sorted(p.end_equity for p in paths)
    takehome = sorted(p.withdrawals_gbp for p in paths)
    tax_gbp = sorted(p.tax_gbp for p in paths)

    # Growth is read off the reinvest-everything shadow book and annualised, since the horizon need
    # not be exactly a year. A path that busted contributes its (terrible) growth honestly.
    years = (len(sessions) / 252.0) or 1.0
    growths = sorted(_annualised(p.compounded_end, start_equity, years) for p in paths)
    g25, g50, g75 = (_pctile(growths, q) for q in (0.25, 0.50, 0.75))

    wd_dates = sorted(p.first_withdrawal for p in paths if p.first_withdrawal is not None)
    tax_dates = sorted(p.first_tax for p in paths if p.first_tax is not None)

    day_rate_year = day_rate_net_annual_gbp(s)
    ladder = [
        _milestone(f"£{int(t):,}/mo", t, g50, start_equity, s)
        for t in sorted(s.portfolio_income_targets_gbp_per_month)
    ]
    ladder.append(_milestone("Day rate", day_rate_year / 12.0, g50, start_equity, s))
    quartiles = {"p25": g25, "p50": g50, "p75": g75}

    return {
        "available": True,
        "start_date": sessions[0].isoformat() if sessions else last_day.isoformat(),
        "end_date": sessions[-1].isoformat() if sessions else last_day.isoformat(),
        "start_equity": round(start_equity, 2),
        "sessions": len(sessions),
        "paths": n_paths,
        "block_days": s.portfolio_projection_block_days,
        "sample_days": [sessions[i].isoformat() for i in idx],
        "bands": bands,
        # Where a year lands.
        "end_equity": {f"p{int(q * 100)}": round(_pctile(ends, q), 2) for q in _BANDS},
        "p_profit": round(sum(1 for e in ends if e > start_equity) / n_paths, 4),
        # What you'd have to sit through. Named by how they read on the page: "typical" is the
        # median year, "bad" the 1-in-10, "worst" the deepest path drawn. `p_halved` is the blunt
        # one — how often the year contained a 50% peak-to-trough — because a percentile answers
        # "how deep" and this answers "how likely", and the second is the one that decides whether
        # you can actually hold the position.
        "drawdown": {
            "p50": round(_pctile(dd, 0.50), 4),
            "p90": round(_pctile(dd, 0.90), 4),
            "max": round(dd[-1], 4),
            "p_halved": round(sum(1 for d in dd if d >= 0.5) / n_paths, 4),
        },
        # Getting paid, over the horizon.
        "take_home_gbp": {f"p{int(q * 100)}": round(_pctile(takehome, q), 2) for q in _BANDS},
        "tax_gbp": {"p50": round(_pctile(tax_gbp, 0.50), 2)},
        # Conditioned on happening at all: the median of the paths that DID pay, not of every path.
        # Mixing the never-paid paths in as "no date" would drag the median past the horizon and
        # report "never" for a book that pays out in most of the futures it was handed.
        "first_withdrawal": _first_event(wd_dates, n_paths),
        "first_tax": _first_event(tax_dates, n_paths),
        # Replacing the day job.
        "growth": {k: round(v, 6) for k, v in quartiles.items()},
        # See _IMPLAUSIBLE_GROWTH: the ladder and the ramp are arithmetically fine and practically
        # meaningless once this is true, so the page says so rather than quoting them.
        "growth_implausible": bool(g50 >= _IMPLAUSIBLE_GROWTH),
        "income_ramp": income_ramp(start_equity, quartiles, day_rate_year, s),
        "day_rate": {
            "gbp_per_day": s.portfolio_day_rate_gbp,
            "days_per_year": s.portfolio_day_rate_days_per_year,
            "gross_annual_gbp": round(
                s.portfolio_day_rate_gbp * s.portfolio_day_rate_days_per_year, 2
            ),
            "net_annual_gbp": day_rate_year,
            "net_fraction": s.portfolio_day_rate_net_fraction,
        },
        "annual_fixed_costs_gbp": annual_fixed_costs_gbp(s),
        "ladder": ladder,
        # The day-rate rung again at the growth-rate quartiles, so the headline reads as a range
        # rather than as a date. p25 growth is the slow case, p75 the fast one.
        "day_rate_years": {
            "p25": years_to_capital(
                capital_for_income(day_rate_year, g25, s) or 0.0, start_equity, g25
            ),
            "p50": years_to_capital(
                capital_for_income(day_rate_year, g50, s) or 0.0, start_equity, g50
            ),
            "p75": years_to_capital(
                capital_for_income(day_rate_year, g75, s) or 0.0, start_equity, g75
            ),
        },
        # Sample provenance — the page leads with it, because 30-odd days of history is the
        # dominant uncertainty here and a smooth fan chart is very good at hiding that.
        "sample": {
            "days": len(samples),
            "trading_days": len(traded),
            "trades": res.n_trades,
        },
    }
