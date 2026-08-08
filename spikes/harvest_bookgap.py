"""Why does a well-formed setup fail to become takeable, and does recon differ from live?

⚠️ **Ported to the post-#567 engine.** Written 2026-08-07, this asked "why does *takeable* convert
to *book* so differently" and attributed each drop to the two filters the book applied on top of
`takeable` — an entry-fill price band and a trigger-time window. #567 moved both **into the
engine**, where they sit beside the shape gates and reach the book already folded into `takeable`
(`portfolio/extract._qualify` says so in its own docstring). So the original question no longer has
an answer: on a takeable setup those two tests are True by construction, and the harness would have
reported a meaningless 100% conversion with zero attributed drops. It also read four `Settings`
fields the same PR renamed, so it raised `AttributeError` before getting that far — which is what
being untracked, unlinted and unimported for a day costs.

The same question, asked where the answer now lives: `DaySetup.takeable` is

    passed AND triggered AND not exhausted AND in_price_band AND in_window

so a setup that is well-formed and fired, but not takeable, was rejected by exactly one of
exhaustion, the price band, or the window — and the setup carries which. That is the funnel stage
worth attributing, and it is the one that can legitimately differ between the two stores.

⚠️ Read-only: opens both stores and calls `Store.read` only. Nothing here writes.

Run:  .venv/bin/python spikes/harvest_bookgap.py
"""

from __future__ import annotations

import statistics
from collections import Counter
from pathlib import Path

import polars as pl

from small_cap_stack.bullflag import detect_day_with_settings
from small_cap_stack.clock import ET
from small_cap_stack.config import Settings
from small_cap_stack.report import day_chart_bars, day_opportunities, symbol_runs
from small_cap_stack.storage import Store

S = Settings()

# Labels are derived, never written out: #551 — the numbers live in `config.py` and are rendered
# into `research/strategy.md`. A hardcoded "$2-20" here is how this file went stale the first time.
BAND = f"price outside ${S.select_price_min:g}-${S.select_price_max:g}"
WINDOW = f"trigger outside {S.select_window_start:%H:%M}-{S.select_window_end:%H:%M} ET"


def dates_for(root: str) -> list:
    dts = sorted(p.name.split("=")[1] for p in Path(f"{root}/opportunities").glob("dt=*"))
    return [pl.Series([d]).str.to_date().item() for d in dts]


def analyse(root: str, label: str) -> None:
    store = Store(Path(root))
    days = dates_for(root)
    reasons: Counter = Counter()
    hours: Counter = Counter()
    excluded = {s.upper() for s in S.portfolio_exclude_symbols}
    fills: list[float] = []
    takeable = 0
    n_fired = 0
    for d in days:
        opps = day_opportunities(store, d)
        bars_df = store.read("bars", dt=d)
        scans = store.read("scanner_hits", dt=d)
        for row in opps.iter_rows(named=True):
            if str(row["symbol"]).upper() in excluded:
                continue
            day_bars = day_chart_bars(bars_df, row["opportunity_id"], S)
            if not day_bars:
                continue
            for run in symbol_runs(row, bars_df, scans, S):
                setup = detect_day_with_settings(day_bars, S, run.first_hit)
                if setup is None or not setup.passed or setup.trigger_idx is None:
                    continue  # not a well-formed setup that fired — a different funnel stage
                n_fired += 1
                fills.append(setup.entry_fill)
                hours[day_bars[setup.trigger_idx].start.astimezone(ET).time().hour] += 1
                # Attributed in `takeable`'s own order, and only ever to ONE reason, so the counts
                # sum to the drops rather than double-counting a setup that fails two rules.
                if setup.exhausted:
                    reasons["exhausted (late cycle)"] += 1
                elif not setup.in_price_band:
                    reasons[BAND] += 1
                elif not setup.in_window:
                    reasons[WINDOW] += 1
                else:
                    takeable += 1

    print(f"\n=== {label.upper()} ({len(days)} sessions) ===")
    print(
        f"  passed+fired {n_fired}  ->  takeable {takeable}  "
        f"({takeable / max(n_fired, 1):.1%} conversion)"
    )
    print("  drops attributed:")
    for r, n in reasons.most_common():
        print(f"    {r:34s} {n:4d}  ({n / max(n_fired, 1):5.1%} of fired)")
    if fills:
        p = sorted(fills)
        in_band = sum(1 for v in p if S.select_price_min <= v <= S.select_price_max)
        print(
            f"  entry_fill: median ${statistics.median(p):.2f}  "
            f"in band: {in_band}/{len(p)} ({in_band / len(p):.1%})"
        )
    print("  trigger hour (ET) histogram:")
    for h in sorted(hours):
        print(f"    {h:02d}:00  {'#' * hours[h]} {hours[h]}")


if __name__ == "__main__":
    analyse("data/recon", "recon")
    analyse("data/live", "live")
