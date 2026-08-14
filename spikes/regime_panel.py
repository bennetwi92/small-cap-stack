"""Spike #690 (stage 1): the **wide** setup-level panel — every opportunity-run, both stores.

The modelling set for the regime investigation. One row per (date, store, symbol, run) that the
flag grammar resolves to a setup, replaying the shipped detector over the raw bars in both the live
store and the recon store — **200 sessions** (166 reconstructed 2025-10-30 -> 2026-06-30, 34 live
2026-07-01 -> 08-13), against the 60 published chart days the 2026-08-13 design report had.

## Wide means wide: the fitted rules are columns, never filters

The selection thresholds and the book's execution rules were fitted on 61 sessions and are exactly
what this investigation may replace, so **none of them is applied here**. The panel is built under
a `WIDE` settings profile that switches each of them off, and records the raw quantity each one
reads so the shipped book is re-derivable from the panel by a `filter()` and nothing else:

| shipped rule            | switched off by            | re-derive from        |
|-------------------------|----------------------------|-----------------------|
| price band $3-50        | `select_price_min/max`     | `entry_fill`          |
| trigger window -> 09:15 | `select_window_*`          | `trigger_et`          |
| min stop 2.5%           | `select_min_stop_pct`      | `stop_pct`            |
| exhaustion cap 2        | `bull_flag_exhaustion_cap` | `cycle_num`           |
| entry staleness 30 min  | `entry_staleness_min`      | `staleness_delay_min` |
| 2 trades/day, sizing, loss limit, risk ladder | never run  | — |

**This is a strict superset, not a different measurement.** The greedy cycle walk in `detect_day`
picks a run's setup on the pole/trigger/appearance chain alone — it never consults a gate, a price,
a window or a stop distance — so relaxing them changes *which rows carry an R*, never *which setup
a row is*. Verified by `--verify`, which reproduces the shipped `takeable` verdict from the wide
panel's columns on every row.

Two things are deliberately **kept**, because neither is a fitted threshold:

- **The flag grammar** (pole -> consolidation -> trigger over the last cons high, stop = cons low).
  R is *defined* against the entry and stop it produces; without it there is no outcome variable.
- **The scanner-appearance gate.** A trigger bar that opened before we had ever seen the symbol was
  takeable by nobody. That is the no-lookahead constraint, not a rule that was tuned.

Two known limits of the population, neither fixable here. The detector yields **one setup per
run**, so the row count is bounded by run segmentation rather than by every flag that formed. And
**float is live-only** (the recon store carries EDGAR share counts, §D-41), so it cannot be a
regime input across the full record — `float_shares` is null on 83% of rows by construction.

    python spikes/regime_panel.py build --store data/live --recon-store data/recon
    python spikes/regime_panel.py verify --panel data/spikes/regime_panel.parquet
    python spikes/regime_panel.py summary --panel data/spikes/regime_panel.parquet
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, time
from pathlib import Path

import polars as pl

from small_cap_stack.bullflag import detect_day_with_settings
from small_cap_stack.capture import Bar
from small_cap_stack.clock import ET
from small_cap_stack.config import Settings
from small_cap_stack.report import _funds_for, day_chart_bars, day_opportunities, symbol_runs
from small_cap_stack.rmetrics import measure_resolved_trade
from small_cap_stack.storage import Store

OUT_DEFAULT = Path("data/spikes/regime_panel.parquet")

# The rule-off profile. Every value here is a *selection* threshold being disabled, never a change
# to the grammar — see the module docstring's table. `bull_flag_*` shape parameters, `token_eps`,
# the volume floors and `reentry_*` segmentation are all left at their shipped values.
WIDE_OVERRIDES: dict[str, object] = {
    "select_price_min": 0.0,
    "select_price_max": 1.0e12,
    "select_window_start": time(0, 0),
    "select_window_end": time(23, 59),
    "select_min_stop_pct": 0.0,
    "bull_flag_exhaustion_cap": 10_000,
    "entry_staleness_min": 10_000_000,
}


def wide_settings(base: Settings) -> Settings:
    # `Settings` is a pydantic BaseSettings, so `model_copy` rather than `dataclasses.replace` —
    # and it deliberately skips re-validation, which matters: the class carries a cross-field
    # validator on the IBKR mode that has nothing to do with detection and would run on every copy.
    return base.model_copy(update=WIDE_OVERRIDES)


def _partition_dates(store: Store, dataset: str = "opportunities") -> list[date]:
    root = store.data_dir / dataset
    if not root.exists():
        return []
    out: list[date] = []
    for p in sorted(root.glob("dt=*")):
        if not any(p.glob("*.parquet")):
            continue
        try:
            out.append(date.fromisoformat(p.name[3:]))
        except ValueError:
            continue
    return out


def _et_minutes(ts: datetime) -> float:
    """Minutes past ET midnight — the comparable clock for a trigger or an appearance."""
    t = ts.astimezone(ET)
    return t.hour * 60 + t.minute + t.second / 60.0


def _row_for_run(
    *,
    trading_date: date,
    source: str,
    symbol: str,
    oid: str,
    seg_id: str,
    run_idx: int,
    run_count: int,
    first_seen: datetime | None,
    first_hit: datetime | None,
    n_scanner_hits: int,
    day_bars: list[Bar],
    settings: Settings,
    float_shares: int | None,
    short_percent: float | None,
) -> dict[str, object] | None:
    """One panel row: the wide detection plus its measurement, or None if no pole forms."""
    setup = detect_day_with_settings(day_bars, settings, first_hit)
    if setup is None:
        return None
    seg, fv = setup.segment, setup.features
    risk = round(setup.entry_fill - setup.stop, 6)
    triggered = setup.trigger_idx is not None and risk > 0
    trigger_bar = day_bars[setup.trigger_idx] if setup.trigger_idx is not None else None

    row: dict[str, object] = {
        # identity
        "dt": trading_date,
        "source": source,
        "symbol": symbol,
        "opportunity_id": oid,
        "seg_id": seg_id,
        "run": run_idx,
        "run_count": run_count,
        # context available at trigger time
        "first_seen_utc": first_seen,
        "first_hit_utc": first_hit,
        "first_hit_et_min": _et_minutes(first_hit) if first_hit is not None else None,
        "n_scanner_hits": n_scanner_hits,
        "n_day_bars": len(day_bars),
        # the setup
        "entry_trigger": setup.entry_trigger,
        "entry_fill": setup.entry_fill,
        "breakout_level": setup.breakout_level,
        "stop": setup.stop,
        "planned_risk": risk,
        "stop_pct": (risk / setup.entry_fill) if setup.entry_fill > 0 else None,
        # shape
        "pole_len": seg.pole_len,
        "cons_len": seg.cons_len,
        "retracement": round(fv.retracement, 4),
        "cons_vol_reducing": fv.cons_vol_reducing,
        "pole_has_big_green": fv.pole_has_big_green,
        "score": round(setup.score, 4),
        "passed": setup.passed,
        "failing_gates": ",".join(g.name for g in setup.gates if not g.passed),
        "cycle_num": setup.cycle_num,
        "total_significant_cycles": setup.total_significant_cycles,
        "cons_has_range": setup.cons_has_range,
        "untraded_cons_bars": setup.untraded_cons_bars,
        "halted_consolidation": setup.halted_consolidation,
        # enrichment (live store only for float — see the module docstring)
        "float_shares": float_shares,
        "short_percent": short_percent,
        # outcome
        "triggered": triggered,
        "trigger_utc": trigger_bar.start if trigger_bar is not None else None,
        "trigger_et_min": _et_minutes(trigger_bar.start) if trigger_bar is not None else None,
        "trigger_idx": setup.trigger_idx,
        "staleness_delay_min": (
            (trigger_bar.start - first_hit).total_seconds() / 60.0
            if trigger_bar is not None and first_hit is not None
            else None
        ),
    }
    # Everything the pole itself did, as size-of-move context that does not depend on the stop.
    peak_bar = day_bars[seg.peak_idx]
    base_bar = day_bars[seg.base_idx]
    row["pole_pct"] = (
        round((peak_bar.high - base_bar.low) / base_bar.low, 5) if base_bar.low > 0 else None
    )
    row["pole_volume"] = sum(b.volume for b in day_bars[seg.base_idx : seg.peak_idx + 1])
    row["day_volume"] = sum(b.volume for b in day_bars)
    row["day_dollar_volume"] = sum(b.volume * b.close for b in day_bars)
    row["day_open"] = day_bars[0].open if day_bars else None
    row["day_high"] = max(b.high for b in day_bars) if day_bars else None
    row["day_low"] = min(b.low for b in day_bars) if day_bars else None

    if not triggered:
        return row
    assert setup.trigger_idx is not None
    m = measure_resolved_trade(
        day_bars, entry_fill=setup.entry_fill, stop=setup.stop, entry_index=setup.trigger_idx
    )
    row.update(
        {
            "entry_price": m["entry_price"],
            "realised_risk": m["initial_risk"],
            "max_r": m["max_r"],
            "max_gain_pct": m["max_gain_pct"],
            "mae_r": m["mae_r"],
            "stopped_out": m["stopped_out"],
            "stop_index": m["stop_index"],
            "bars_to_max_r": m["bars_to_max_r"],
            "fill_above_entry_bar_high": m["fill_above_entry_bar_high"],
            "same_bar_stop": bool(m["stopped_out"]) and m["stop_index"] == setup.trigger_idx,
        }
    )
    return row


def build_store(store: Store, source: str, settings: Settings) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    dates = _partition_dates(store)
    for i, d in enumerate(dates, start=1):
        opps = day_opportunities(store, d)
        if opps.is_empty():
            continue
        bars_df = store.read("bars", dt=d)
        if bars_df.is_empty():
            print(f"  [{source}] {d}: no bars — skipped", file=sys.stderr)
            continue
        scans = store.read("scanner_hits", dt=d)
        funds = store.read("fundamentals", dt=d)
        n_before = len(rows)
        for orow in opps.iter_rows(named=True):
            oid = orow["opportunity_id"]
            day_bars = day_chart_bars(bars_df, oid, settings)
            if not day_bars:
                continue
            float_shares, short_percent = _funds_for(funds, oid)
            hits = (
                0 if scans.is_empty() else scans.filter(pl.col("opportunity_id") == oid).height  # noqa: PD011
            )
            for run in symbol_runs(orow, bars_df, scans, settings):
                row = _row_for_run(
                    trading_date=d,
                    source=source,
                    symbol=orow["symbol"],
                    oid=oid,
                    seg_id=run.seg_id,
                    run_idx=run.idx,
                    run_count=run.run_count,
                    first_seen=orow.get("first_seen_utc"),
                    first_hit=run.first_hit,
                    n_scanner_hits=hits,
                    day_bars=day_bars,
                    settings=settings,
                    float_shares=float_shares,
                    short_percent=short_percent,
                )
                if row is not None:
                    rows.append(row)
        print(
            f"  [{source}] {i}/{len(dates)} {d}: {opps.height} opps -> {len(rows) - n_before} rows",
            file=sys.stderr,
        )
    return rows


def cmd_build(args: argparse.Namespace) -> None:
    base = Settings()
    wide = wide_settings(base)
    rows: list[dict[str, object]] = []
    if args.recon_store:
        print("recon store:", args.recon_store, file=sys.stderr)
        rows += build_store(Store(Path(args.recon_store)), "recon", wide)
    if args.store:
        print("live store:", args.store, file=sys.stderr)
        rows += build_store(Store(Path(args.store)), "live", wide)
    if not rows:
        print("no rows", file=sys.stderr)
        return
    df = pl.DataFrame(rows, infer_schema_length=None).sort(["dt", "source", "symbol", "run"])
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(out)
    print(f"\nwrote {df.height} rows x {df.width} cols -> {out}", file=sys.stderr)
    _describe(df)


def _describe(df: pl.DataFrame) -> None:
    by = df.group_by("source").agg(
        pl.col("dt").n_unique().alias("sessions"),
        pl.len().alias("setups"),
        pl.col("triggered").sum().alias("triggered"),
        pl.col("dt").min().alias("from"),
        pl.col("dt").max().alias("to"),
    )
    print(by.sort("source"))


def _shipped_takeable(df: pl.DataFrame, s: Settings) -> pl.Expr:
    """The shipped `takeable` verdict, rebuilt from the wide panel's columns alone."""
    win_start = s.select_window_start.hour * 60 + s.select_window_start.minute
    win_end = s.select_window_end.hour * 60 + s.select_window_end.minute
    return (
        pl.col("triggered")
        & pl.col("passed")
        & pl.col("cons_has_range")
        & (pl.col("cycle_num") <= s.bull_flag_exhaustion_cap)
        & (pl.col("staleness_delay_min") <= s.entry_staleness_min)
        & (pl.col("entry_fill") >= s.select_price_min)
        & (pl.col("entry_fill") <= s.select_price_max)
        & (pl.col("trigger_et_min") >= win_start)
        & (pl.col("trigger_et_min") < win_end)
        & (pl.col("stop_pct") >= s.select_min_stop_pct)
    )


def cmd_verify(args: argparse.Namespace) -> None:
    """Re-derive the shipped verdict from the wide panel and check it against the shipped run.

    The claim under test is that the wide panel is a strict SUPERSET — same setups, more of them
    carrying an R. Anything else means a relaxed knob fed back into which cycle was chosen, and
    every number built on the panel would be measuring a different population than it claims.
    """
    base = Settings()
    df = pl.read_parquet(args.panel)
    wide_takeable = df.with_columns(_shipped_takeable(df, base).alias("t"))
    print(f"panel: {df.height} setups, {df['triggered'].sum()} triggered")
    print(f"shipped-takeable re-derived from wide columns: {wide_takeable['t'].sum()}")

    # And the ground truth: run the real detector under the real Settings over the same days.
    stores = [(Store(Path(args.recon_store)), "recon")] if args.recon_store else []
    if args.store:
        stores.append((Store(Path(args.store)), "live"))
    mismatches = 0
    checked = 0
    for store, source in stores:
        dates = _partition_dates(store)
        sample = dates[:: max(1, len(dates) // args.sample_days)][: args.sample_days]
        for d in sample:
            shipped = build_store_day(store, source, base, d)
            wide_day = wide_takeable.filter((pl.col("dt") == d) & (pl.col("source") == source))
            got = set(wide_day.filter(pl.col("t"))["seg_id"].to_list())
            want = {r["seg_id"] for r in shipped if r.get("takeable")}
            checked += 1
            if got != want:
                mismatches += 1
                print(f"  MISMATCH {source} {d}: wide={sorted(got)} shipped={sorted(want)}")
    print(f"\nchecked {checked} day(s), {mismatches} mismatch(es)")


def build_store_day(
    store: Store, source: str, settings: Settings, d: date
) -> list[dict[str, object]]:
    """One day under an arbitrary settings profile, with the shipped `takeable` attached."""
    opps = day_opportunities(store, d)
    bars_df = store.read("bars", dt=d)
    if opps.is_empty() or bars_df.is_empty():
        return []
    scans = store.read("scanner_hits", dt=d)
    out: list[dict[str, object]] = []
    for orow in opps.iter_rows(named=True):
        oid = orow["opportunity_id"]
        day_bars = day_chart_bars(bars_df, oid, settings)
        if not day_bars:
            continue
        for run in symbol_runs(orow, bars_df, scans, settings):
            setup = detect_day_with_settings(day_bars, settings, run.first_hit)
            if setup is None:
                continue
            risk = round(setup.entry_fill - setup.stop, 6)
            out.append(
                {
                    "seg_id": run.seg_id,
                    "symbol": orow["symbol"],
                    "takeable": setup.takeable and risk > 0,
                }
            )
    return out


def cmd_summary(args: argparse.Namespace) -> None:
    df = pl.read_parquet(args.panel)
    base = Settings()
    _describe(df)
    trig = df.filter(pl.col("triggered"))
    print(f"\ntriggered setups: {trig.height}")
    for t in (1.0, 2.0, 3.0):
        p = (trig["max_r"] >= t).mean()
        print(f"  P(max R >= {t:.0f}R) = {p:.4f}")
    print(f"  mean max R = {trig['max_r'].mean():.4f}   median = {trig['max_r'].median():.4f}")
    print("\nfunnel (each rule applied ON ITS OWN to the triggered population):")
    rules = {
        "passed (shape gates)": pl.col("passed"),
        f"price ${base.select_price_min:.0f}-{base.select_price_max:.0f}": (
            pl.col("entry_fill").is_between(base.select_price_min, base.select_price_max)
        ),
        "window -> 09:15": pl.col("trigger_et_min").is_between(240, 555, closed="left"),
        f"stop >= {base.select_min_stop_pct:.1%}": pl.col("stop_pct") >= base.select_min_stop_pct,
        f"cycle <= {base.bull_flag_exhaustion_cap}": (
            pl.col("cycle_num") <= base.bull_flag_exhaustion_cap
        ),
        f"fresh (<= {base.entry_staleness_min}m)": (
            pl.col("staleness_delay_min") <= base.entry_staleness_min
        ),
    }
    for label, expr in rules.items():
        sub = trig.filter(expr)
        if sub.is_empty():
            continue
        print(
            f"  {label:<26} n={sub.height:>5}  P(2R)={(sub['max_r'] >= 2).mean():.4f}"
            f"  meanR={sub['max_r'].mean():+.4f}"
        )
    shipped = trig.filter(_shipped_takeable(trig, base))
    print(
        f"  {'ALL (shipped takeable)':<26} n={shipped.height:>5}"
        f"  P(2R)={(shipped['max_r'] >= 2).mean():.4f}  meanR={shipped['max_r'].mean():+.4f}"
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="replay both stores into the wide panel")
    b.add_argument("--store", help="live store root")
    b.add_argument("--recon-store", help="recon store root")
    b.add_argument("--out", default=str(OUT_DEFAULT))
    b.set_defaults(func=cmd_build)

    v = sub.add_parser("verify", help="wide panel reproduces the shipped takeable set")
    v.add_argument("--panel", default=str(OUT_DEFAULT))
    v.add_argument("--store")
    v.add_argument("--recon-store")
    v.add_argument("--sample-days", type=int, default=12)
    v.set_defaults(func=cmd_verify)

    s = sub.add_parser("summary", help="population + per-rule funnel")
    s.add_argument("--panel", default=str(OUT_DEFAULT))
    s.set_defaults(func=cmd_summary)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
