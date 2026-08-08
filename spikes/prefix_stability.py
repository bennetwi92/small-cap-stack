"""Spike #675 (Gate 5 / #312): does the detector's answer change as the day's bars accumulate?

`research/phase-2-roadmap.md` calls this **"the sleeper"** and makes it the reason Gate 5 is
log-only and comes before any order code:

> The v2 detector segments the **longest valid** pole+consolidation over a day's bars. Run live
> against a *growing prefix*, the segmentation it picks at 08:35 may differ from the one it picks at
> 16:00 … so live and replay disagreeing would **silently invalidate the sim as a predictor of the
> live book**.

That is falsifiable offline, and it fails. This harness is the falsification, kept re-runnable so a
future detector change has to re-prove it rather than inherit the result.

**Method.** For every run in the store, replay `detect_day_with_settings` over growing prefixes of
the day's bars and compare the answer at *first fire* against the *full-day* answer, on every field
a live system would act on: `entry_trigger`, `stop`, the trigger bar's timestamp, the segment
indices, `passed`, `takeable`, `exhausted` and `score`. Also count **churn** — any run whose emitted
answer changed at any intermediate prefix, even if it ended up back at the full-day answer.

`--minute` runs the harder variant against the recon store's `bars_1m`: synthesise the in-progress
forms of each 5-min bar (1, 2, 3, 4 minutes elapsed, then complete) and fire on the earliest one
that triggers. This asks whether acting on a *partially formed* bar changes the answer — which is
what a live `keepUpToDate` stream actually hands you.

**Result as of 2026-08-08** (81 sessions: 51 recon 2026-04-17→06-30, 30 live 2026-07-01→08-07,
under post-#643/#584/#644 settings):

    recon   1,220 runs   909 fired   909 first==full   0 churn
    live    1,454 runs 1,109 fired 1,109 first==full   0 churn
    total   2,674 runs 2,018 fired 2,018 first==full   0 churn

    --minute (recon)   909 fired   762 fired on a PARTIAL bar   909 match

**Why it holds — structural, not luck.** In `bullflag/day.py` the candidate loop takes the
*earliest* cycle with a valid trigger and breaks. `entry_trigger`/`entry_fill` come from
`bars[cons_end].high` and `stop` from the consolidation lows — all closed bars strictly before the
trigger. Gates, score, exhaustion and both selection rules read only bars <= trigger. The chosen
setup is **causal**. The only full-day-dependent outputs are `total_significant_cycles` (context,
gates nothing) and `bar_interval`'s modal spacing.

⚠️ **What this does NOT clear.** Both arms use the *same bars, truncated*. It clears the
**algorithm**; it says nothing about the **inputs** — live bar formation and revision, missing or
late bars, feed restarts, or run/`first_hit` segmentation from live scanner hits. Gate 5's question
is therefore "are the live bars the same bars", not "is the detector prefix-stable".

    python spikes/prefix_stability.py --store data/recon
    python spikes/prefix_stability.py --store data/live
    python spikes/prefix_stability.py --store data/recon --minute
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
from collections import Counter
from typing import Any

import polars as pl

from small_cap_stack.bullflag import detect_day_with_settings
from small_cap_stack.capture import Bar
from small_cap_stack.config import Settings
from small_cap_stack.report import day_chart_bars, day_opportunities, symbol_runs
from small_cap_stack.storage import Store


def answer(setup: Any, bars: list[Bar]) -> tuple | None:
    """Everything a live system would act on. ``None`` when the setup has not fired.

    Segment positions are carried as **indices into the prefix**, which is sound only because every
    prefix starts at bar 0 — a live series beginning at a different bar would need timestamps.
    """
    if setup is None or setup.trigger_idx is None:
        return None
    return (
        setup.entry_trigger,
        setup.entry_fill,
        setup.stop,
        bars[setup.trigger_idx].start.isoformat(),
        setup.segment.base_idx,
        setup.segment.peak_idx,
        setup.segment.cons_end_idx,
        setup.passed,
        setup.takeable,
        setup.exhausted,
        round(setup.score, 4),
    )


def _dates(root: str) -> list[str]:
    opps = pathlib.Path(root) / "opportunities"
    return sorted(p.name.split("=")[1] for p in opps.iterdir() if p.name.startswith("dt="))


def prefix_sweep(root: str, s: Settings) -> tuple[Counter, list]:
    """Grow the 5-min series a bar at a time; compare first fire and every step to the full day."""
    store = Store(root)
    stats: Counter = Counter()
    diffs: list = []
    for d in _dates(root):
        date = dt.date.fromisoformat(d)
        opps = day_opportunities(store, date)
        if opps.is_empty():
            continue
        bars_df = store.read("bars", dt=date)
        scans = store.read("scanner_hits", dt=date)
        for row in opps.iter_rows(named=True):
            day_bars = day_chart_bars(bars_df, row["opportunity_id"], s)
            if not day_bars:
                continue
            for run in symbol_runs(row, bars_df, scans, s):
                full = answer(detect_day_with_settings(day_bars, s, run.first_hit), day_bars)
                seq = []
                for k in range(3, len(day_bars) + 1):
                    pre = day_bars[:k]
                    got = answer(detect_day_with_settings(pre, s, run.first_hit), pre)
                    if got is not None:
                        seq.append(got)
                stats["runs"] += 1
                if not seq:
                    stats["never_fired"] += 1
                    if full is not None:
                        stats["full_only_fire"] += 1  # would be a live MISS
                    continue
                stats["fired"] += 1
                if full is None:
                    stats["prefix_fired_full_silent"] += 1
                    diffs.append((d, row["symbol"], run.idx, "full_silent", seq[0]))
                    continue
                if seq[0] == full:
                    stats["first_eq_full"] += 1
                else:
                    stats["first_ne_full"] += 1
                    diffs.append((d, row["symbol"], run.idx, "first!=full", seq[0], full))
                if len(set(seq)) > 1:
                    stats["churned"] += 1
                    diffs.append((d, row["symbol"], run.idx, "churn", seq[0], seq[-1]))
    return stats, diffs


def _minute_bars(mdf: pl.DataFrame, oid: str) -> dict[dt.datetime, Bar]:
    sub = mdf.filter(pl.col("opportunity_id") == oid)
    if sub.is_empty():
        return {}
    uniq = sub.unique(subset="bar_start_utc", keep="first").sort("bar_start_utc")
    rows = uniq.iter_rows(named=True)
    out: dict[dt.datetime, Bar] = {}
    for r in rows:
        out.setdefault(
            r["bar_start_utc"],
            Bar(
                start=r["bar_start_utc"],
                open=r["open"],
                high=r["high"],
                low=r["low"],
                close=r["close"],
                volume=r["volume"],
            ),
        )
    return out


def partial_sweep(root: str, s: Settings) -> tuple[Counter, list]:
    """Fire on the earliest *in-progress* form of a bar, the way a live stream would offer it."""
    store = Store(root)
    stats: Counter = Counter()
    diffs: list = []
    for d in _dates(root):
        date = dt.date.fromisoformat(d)
        opps = day_opportunities(store, date)
        if opps.is_empty():
            continue
        bars_df = store.read("bars", dt=date)
        scans = store.read("scanner_hits", dt=date)
        mdf = store.read("bars_1m", dt=date)
        if mdf.is_empty():
            stats["days_without_1m"] += 1
            continue
        stats["days_with_1m"] += 1
        for row in opps.iter_rows(named=True):
            oid = row["opportunity_id"]
            day_bars = day_chart_bars(bars_df, oid, s)
            by_start = _minute_bars(mdf, oid)
            if not day_bars or not by_start:
                continue
            for run in symbol_runs(row, bars_df, scans, s):
                full = answer(detect_day_with_settings(day_bars, s, run.first_hit), day_bars)
                stats["runs"] += 1
                found = None
                for i in range(2, len(day_bars)):
                    for elapsed in (1, 2, 3, 4, 5):
                        if elapsed == 5:
                            series = day_bars[: i + 1]
                        else:
                            mins = [
                                by_start[day_bars[i].start + dt.timedelta(minutes=j)]
                                for j in range(elapsed)
                                if day_bars[i].start + dt.timedelta(minutes=j) in by_start
                            ]
                            if not mins:
                                continue
                            series = day_bars[:i] + [
                                Bar(
                                    start=day_bars[i].start,
                                    open=mins[0].open,
                                    high=max(b.high for b in mins),
                                    low=min(b.low for b in mins),
                                    close=mins[-1].close,
                                    volume=sum(b.volume for b in mins),
                                )
                            ]
                        got = answer(detect_day_with_settings(series, s, run.first_hit), series)
                        if got is not None:
                            found = (elapsed, got)
                            break
                    if found:
                        break
                if found is None:
                    stats["never_fired"] += 1
                    continue
                elapsed, got = found
                stats["fired"] += 1
                if elapsed < 5:
                    stats["fired_on_partial_bar"] += 1
                if full is None:
                    stats["partial_fire_full_silent"] += 1
                    diffs.append((d, row["symbol"], run.idx, "full_silent", got))
                elif got == full:
                    stats["match"] += 1
                else:
                    stats["differs"] += 1
                    diffs.append((d, row["symbol"], run.idx, "differs", got, full))
    return stats, diffs


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--store", required=True, help="path to a Parquet store (data/recon|live)")
    ap.add_argument(
        "--minute",
        action="store_true",
        help="fire on in-progress 5-min bars synthesised from bars_1m (recon store only)",
    )
    ap.add_argument("--show", type=int, default=20, help="how many differing runs to print")
    args = ap.parse_args()

    s = Settings()
    stats, diffs = (partial_sweep if args.minute else prefix_sweep)(args.store, s)
    print(f"{args.store} {'(minute)' if args.minute else '(5-min prefixes)'}")
    print(json.dumps(dict(sorted(stats.items())), indent=1))
    if diffs:
        print(f"\n{len(diffs)} differing run(s) — the first {args.show}:")
        for row in diffs[: args.show]:
            print("  ", row)
    else:
        print("\nNo run differed from its full-day answer at any prefix.")


if __name__ == "__main__":
    main()
