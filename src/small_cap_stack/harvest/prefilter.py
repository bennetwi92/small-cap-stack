"""Grouped-daily bars → the symbol-days worth pulling minute bars for (#431, from spike #428).

The harvest's budget is set here and nowhere else. One grouped-daily request returns the whole US
equity market's OHLCV for one session (~12,400 rows); minute bars cost one request *per candidate*,
so this filter is the difference between ~218 calls a session and ~12,400. At the free tier's
5 calls/min — the ingest path #430 chose — every candidate this admits costs 13 seconds of a
finite nightly window, and every one it wrongly rejects is a symbol-day the backtest will never see.

## Two filters, kept apart on purpose

:func:`universe_rows` applies the **strategy's own locked gates** — the $1–50 price band and
change > 10% — and is what gets *stored*. Those come from ``Settings.scan_*``; changing them
changes the strategy, not the harvest.

:func:`candidates` then applies the **day-volume floor**, which is a harvest-only heuristic and
therefore stays compute-on-read. It is a proxy: the real gate is trailing 5-min volume > 100k,
which one daily bar cannot evaluate. Using day volume ≥ 100k is *airtight but very loose* — a name
clearing a 100k trailing 5-min sum must trade at least 100k on the day, so the floor cannot drop a
true hit, but it admits a great many names that never come close. #430 measured the consequence:
~217 candidates/day, about 4× what the plan assumed, which is what prices the harvest at ~45
nights. :func:`sweep_floors` is the pre-flight measurement #431 asks for before the first full
night — re-running the filter at several floors costs a handful of grouped-daily calls and answers
"would a tighter floor halve the budget?" against real market breadth rather than intuition.

**The floor is not a free tightening.** All 25 committed review cases — every one a name the live
scanner actually surfaced — carry ≥1.25M captured-window volume (p10 2.5M, median 17.5M), so a
500k floor retains 25/25 with 2.5× headroom. But 25 cases is a small, survivorship-shaped sample:
they are names that made it onto the scanner *and* were worth reviewing. What a floor cuts from the
wider population is what :func:`sweep_floors` measures; nothing here changes the default.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ..config import Settings


@dataclass(frozen=True)
class DailyRow:
    """One symbol's daily bar, plus the previous close the change gate needs.

    ``day_change_pct`` is measured on the day's **high**, not its close: the strategy trades a
    runner intraday, so a name that ripped to +180% by 07:00 and closed red was a candidate while
    it mattered. Measuring on the close would drop exactly the pump-and-fade shape the engine is
    built for.
    """

    symbol: str
    high: float
    low: float | None
    close: float
    day_volume: float
    prev_close: float | None
    day_change_pct: float | None  # None when the previous close is unknown

    def as_record(self) -> dict[str, Any]:
        """Storage row for the harvest's ``daily_universe`` dataset (see :mod:`.runner`)."""
        return {
            "symbol": self.symbol,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "day_volume": self.day_volume,
            "prev_close": self.prev_close,
            "day_change_pct": self.day_change_pct,
        }

    @staticmethod
    def from_record(row: Mapping[str, Any]) -> DailyRow:
        return DailyRow(
            symbol=str(row["symbol"]),
            high=float(row["high"]),
            low=None if row["low"] is None else float(row["low"]),
            close=float(row["close"]),
            day_volume=float(row["day_volume"] or 0.0),
            prev_close=None if row["prev_close"] is None else float(row["prev_close"]),
            day_change_pct=(
                None if row["day_change_pct"] is None else float(row["day_change_pct"])
            ),
        )


def universe_rows(
    grouped: Sequence[Mapping[str, Any]],
    prev_close: Mapping[str, float],
    s: Settings,
) -> list[DailyRow]:
    """Grouped-daily rows that clear the strategy's locked price + change gates.

    This is the *stored* universe (the harvest's ``daily_universe`` dataset). The volume floor is
    deliberately NOT applied here — it is a tunable heuristic, and baking it in would mean
    re-spending ~500 grouped-daily calls to sweep it later. Price band and change are the
    strategy's own definition of "in the universe at all", so storing rows outside them would be
    storing names the scanner could never surface.

    A symbol with **no** previous close is kept: a first-day or relisted symbol is exactly the kind
    of runner the strategy wants, and silently dropping it here would bias the harvest toward names
    that already existed.
    """
    out: list[DailyRow] = []
    for row in grouped:
        sym = str(row.get("T") or "")
        high, close = row.get("h"), row.get("c")
        if not sym or high is None or close is None:
            continue
        if not (s.scan_min_price <= float(high) <= s.scan_max_price):
            continue
        pc = prev_close.get(sym)
        change = None if not pc else (float(high) / pc - 1.0) * 100.0
        if change is not None and change <= s.scan_change_pct:
            continue
        low = row.get("l")
        out.append(
            DailyRow(
                symbol=sym,
                high=float(high),
                low=None if low is None else float(low),
                close=float(close),
                day_volume=float(row.get("v") or 0.0),
                prev_close=pc,
                day_change_pct=None if change is None else round(change, 2),
            )
        )
    return _ranked(out)


def candidates(rows: Sequence[DailyRow], *, min_day_volume: float) -> list[DailyRow]:
    """The stored universe narrowed by the day-volume floor, in rank order.

    Rank is by day change descending — the closest a daily bar gets to the live scanner's
    ``TOP_PERC_GAIN`` ordering. It is *not* the live rank and cannot be: the IBKR 50-row cap ranks
    on intraday change at each scan tick, which #428 showed is a real, load-bearing effect this
    reconstruction does not reproduce. It is recorded so a later rank-cap model has a starting
    order, and so a truncated night takes the biggest movers first rather than an arbitrary slice.
    """
    return _ranked([r for r in rows if r.day_volume >= min_day_volume])


def _ranked(rows: Sequence[DailyRow]) -> list[DailyRow]:
    # A TOTAL order: symbol breaks ties on change, so the candidate list — and therefore which
    # symbols a truncated night got through — does not depend on however the vendor happened to
    # order its response. Same reasoning as the paper book's candidate sort (#381).
    return sorted(rows, key=lambda r: (-(r.day_change_pct or 0.0), r.symbol))


def sweep_floors(rows: Sequence[DailyRow], floors: Sequence[float]) -> list[dict[str, float | int]]:
    """Candidate count at each day-volume floor — the #431 pre-flight measurement.

    Answers the only question that can cut the harvest's 45 nights without buying anything: how
    much of the 217/day candidate set sits between the current 100k floor and a tighter one. Costs
    no extra API calls when run against stored ``daily_universe`` rows.
    """
    base = len(rows)
    out: list[dict[str, float | int]] = []
    for floor in sorted(floors):
        kept = sum(1 for r in rows if r.day_volume >= floor)
        out.append(
            {
                "floor": floor,
                "candidates": kept,
                "dropped": base - kept,
                "retained_pct": round(100.0 * kept / base, 2) if base else 0.0,
            }
        )
    return out
