"""Risk-adjusted smoothness of the book: Sharpe, Sortino, Ulcer index (#648).

Everything here is a pure function of a :class:`PortfolioResult`, replayed on read like the rest of
:mod:`small_cap_stack.portfolio` — no new state, no new dependency (stdlib ``statistics``/``math``
only).
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from collections.abc import Sequence
from datetime import date

from .models import PortfolioResult

#: Trading days per year, for annualising a daily Sharpe/Sortino ratio.
_TRADING_DAYS_PER_YEAR = 252


def daily_returns(res: PortfolioResult) -> list[float]:
    """One fractional return per session in ``res.equity_curve``, ascending, ``0.0`` on a flat day.

    ⚠️ Deliberately NOT a diff of ``equity_curve``. The VPS and market-data charges settle at month
    rollover inside ``_run_book``, so the 1st of each month carries a step-down that is an admin
    cost, not strategy volatility — on a ~$600 book that would read as a fake −2.8% day and destroy
    the downside deviation. Rebuilt from trade P&L instead, exactly the way
    :func:`.projection.day_samples` builds its scale-free per-trade samples: each session's return
    is that day's total ``net_pnl_usd`` over the day's *opening* equity (the first trade's
    ``equity_before`` — what both concurrent positions were sized against), so a session with no
    trades is a true ``0.0`` rather than an artefact of the cost ledger."""
    by_day: dict[date, list[tuple[float, float]]] = defaultdict(list)
    for t in res.trades:
        by_day[t.trading_date].append((t.net_pnl_usd, t.equity_before))
    out: list[float] = []
    for day, _equity in res.equity_curve:
        rows = by_day.get(day, [])
        if not rows:
            out.append(0.0)
            continue
        opening = rows[0][1]
        if opening <= 0:
            out.append(0.0)
            continue
        out.append(sum(pnl for pnl, _e in rows) / opening)
    return out


def sharpe(rs: Sequence[float], *, annualise: bool = True) -> float | None:
    """``mean(rs) / stdev(rs)``, annualised by ``sqrt(252)`` unless told not to.

    Risk-free rate is 0 — a Phase-1 paper book holds cash, not a T-bill. ``None`` below 3 points or
    when the series has zero variance (constant returns can't be Sharpe-ranked)."""
    if len(rs) < 3:
        return None
    sd = statistics.stdev(rs)
    if sd == 0:
        return None
    val = statistics.mean(rs) / sd
    return val * math.sqrt(_TRADING_DAYS_PER_YEAR) if annualise else val


def sortino(rs: Sequence[float], *, mar: float = 0.0, annualise: bool = True) -> float | None:
    """``(mean(rs) - mar) / downside_deviation``, annualised by ``sqrt(252)`` unless told not to.

    Downside deviation divides the sum of squared negative excesses by the FULL count ``len(rs)``,
    not by the number of losing days — the standard definition, and the one that stops a book with
    rare-but-huge losses scoring well simply because it rarely loses. Same ``None`` guards as
    :func:`sharpe`: fewer than 3 points, or zero downside deviation (nothing below the MAR)."""
    if len(rs) < 3:
        return None
    downside_sq_sum = sum((r - mar) ** 2 for r in rs if r < mar)
    downside_dev = math.sqrt(downside_sq_sum / len(rs))
    if downside_dev == 0:
        return None
    val = (statistics.mean(rs) - mar) / downside_dev
    return val * math.sqrt(_TRADING_DAYS_PER_YEAR) if annualise else val


def ulcer_index(rs: Sequence[float]) -> float:
    """RMS of the percentage drawdown depth at every point on the compounded series.

    ``100 * (peak - eq) / peak`` at each step, then root-mean-square over the whole series —
    penalises depth × duration rather than just the single worst trough, so a long shallow drawdown
    scores worse than a brief deep one of the same max depth. ``0.0`` for an empty series."""
    if not rs:
        return 0.0
    equity = 1.0
    peak = equity
    sq_sum = 0.0
    for r in rs:
        equity *= 1.0 + r
        peak = max(peak, equity)
        depth_pct = 100.0 * (peak - equity) / peak if peak > 0 else 0.0
        sq_sum += depth_pct * depth_pct
    return math.sqrt(sq_sum / len(rs))
