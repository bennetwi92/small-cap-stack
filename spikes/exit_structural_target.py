"""Spike #713: structure-derived exit target (pole-height measured move) vs fixed 2.0R.

The live book (`portfolio/exit.py::simulate_exit`) walks every filled trade to the SAME fixed
target — `portfolio_target_r = 2.0` — regardless of how big the move that built the flag actually
was. This spike asks whether a target set ONCE at entry as a multiple of the pole's own height
(`target_price = entry_price + m * pole_height_abs`, expressed back as an equivalent `target_r`)
fits small vs. large moves better than one blanket R.

Population: every **takeable** opportunity (`extract_day_trades`'s own qualify test — shape gates +
triggered + not exhausted + selected), across the full pulled record (`data/live` + `data/recon`,
both stores, exactly how other spikes combine them — see `spikes/regime_panel.py`). No lookahead:
the target is a pure function of the setup's own pre-entry shape (`pole_height_abs`) and is fixed at
entry; this deliberately does NOT recompute/trail the target off later bars. Scored on
`realized_r`, never `max_r`. No untradable populations — every trade compared here is one the book
would actually have taken.

Both exits (baseline 2.0R and every structural `m`) are simulated over the SAME `bars`/
`entry_index`/`entry_price`/`stop` via `portfolio.exit.simulate_exit` directly, so only the target
value differs — never the walk conventions (stop-first, gap-through, resting-limit target).

Usage:
    .venv/bin/python spikes/exit_structural_target.py
    .venv/bin/python spikes/exit_structural_target.py --live data/live --recon data/recon \\
        --json data/spikes/exit_structural_target.json
"""

from __future__ import annotations

import argparse
import json
import statistics
from datetime import date
from pathlib import Path
from typing import Any

from small_cap_stack.bullflag.day import detect_day_with_settings
from small_cap_stack.config import Settings
from small_cap_stack.portfolio.exit import simulate_exit
from small_cap_stack.portfolio.extract import extract_day_trades
from small_cap_stack.portfolio.models import CandidateTrade
from small_cap_stack.portfolio.payload import collected_dates
from small_cap_stack.report import _funds_for, day_chart_bars, day_opportunities, symbol_runs
from small_cap_stack.storage import Store

M_GRID = (0.5, 0.75, 1.0, 1.25, 1.5, 2.0)
BASELINE_TARGET_R = 2.0
BREAKEVEN_R = 0.0


def pole_heights_for_day(
    store: Store, s: Settings, trading_date: date
) -> dict[tuple[str, str, int], float | None]:
    """(symbol, seg_id, run) -> pole_height_abs (dollars), mirroring `extract_day_trades`'s own
    iteration exactly (same excluded-symbol filter, same day_bars/first_hit/shares_outstanding) so
    the keys line up with the `CandidateTrade`s it returns. `None` when a pole never formed for
    that run (`detect_day_with_settings` returned no setup) — the caller skips those.
    """
    opps = day_opportunities(store, trading_date)
    if opps.is_empty():
        return {}
    bars_df = store.read("bars", dt=trading_date)
    scans = store.read("scanner_hits", dt=trading_date)
    funds = store.read("fundamentals", dt=trading_date)
    excluded = {sym.upper() for sym in s.portfolio_exclude_symbols}
    out: dict[tuple[str, str, int], float | None] = {}
    for row in opps.iter_rows(named=True):
        if str(row["symbol"]).upper() in excluded:
            continue
        oid = row["opportunity_id"]
        day_bars = day_chart_bars(bars_df, oid, s)
        if not day_bars:
            continue
        _float_shares, _short_percent, shares_outstanding = _funds_for(funds, oid)
        for run in symbol_runs(row, bars_df, scans, s):
            setup = detect_day_with_settings(day_bars, s, run.first_hit, shares_outstanding)
            pole = setup.features.pole_height_abs if setup is not None else None
            out[(row["symbol"], run.seg_id, run.idx)] = pole
    return out


def paired_stats(diffs: list[float]) -> tuple[float, float | None]:
    """Mean and standard error of a paired per-trade difference. `(mean, None)` under n=2."""
    n = len(diffs)
    if n == 0:
        return 0.0, None
    m = sum(diffs) / n
    if n < 2:
        return round(m, 6), None
    var = sum((d - m) ** 2 for d in diffs) / (n - 1)
    return round(m, 6), round((var / n) ** 0.5, 6)


def summarize(rs: list[float]) -> dict[str, Any]:
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]
    return {
        "n": len(rs),
        "total_r": round(sum(rs), 4),
        "mean_r": round(statistics.mean(rs), 4) if rs else None,
        "win_rate": round(len(wins) / len(rs), 4) if rs else None,
        "avg_winner_r": round(statistics.mean(wins), 4) if wins else None,
        "avg_loser_r": round(statistics.mean(losses), 4) if losses else None,
    }


def gather_trades(
    stores: list[tuple[Store, str]], s: Settings
) -> list[tuple[CandidateTrade, float]]:
    """All takeable (candidate, pole_height_abs) pairs across every store/date, pole_height_abs
    already validated positive (missing/non-positive poles are dropped here with a printed count —
    the caller only ever sees trades usable for every structural variant)."""
    out: list[tuple[CandidateTrade, float]] = []
    skipped_no_pole = 0
    for store, source in stores:
        dates = collected_dates(store)
        for d in dates:
            candidates = extract_day_trades(store, s, d, source=source)
            if not candidates:
                continue
            poles = pole_heights_for_day(store, s, d)
            for c in candidates:
                pole = poles.get((c.symbol, c.seg_id, c.run))
                if pole is None or pole <= 0 or c.risk <= 0:
                    skipped_no_pole += 1
                    continue
                out.append((c, pole))
    print(f"skipped (missing/non-positive pole height, or non-positive risk): {skipped_no_pole}")
    return out


def run_sweep(pairs: list[tuple[CandidateTrade, float]], s: Settings) -> dict[str, Any]:
    baseline_r: list[float] = []
    variant_r: dict[float, list[float]] = {m: [] for m in M_GRID}
    variant_diff: dict[float, list[float]] = {m: [] for m in M_GRID}
    variant_flip: dict[float, int] = dict.fromkeys(M_GRID, 0)

    for c, pole in pairs:
        base = simulate_exit(
            c.bars,
            c.entry_price,
            c.stop,
            c.entry_index,
            target_r=BASELINE_TARGET_R,
            breakeven_r=BREAKEVEN_R,
            tick_size=s.tick_size,
            exit_slippage_ticks=s.portfolio_exit_slippage_ticks,
        )
        baseline_r.append(base.realized_r)
        for m in M_GRID:
            target_r = m * pole / c.risk
            variant = simulate_exit(
                c.bars,
                c.entry_price,
                c.stop,
                c.entry_index,
                target_r=target_r,
                breakeven_r=BREAKEVEN_R,
                tick_size=s.tick_size,
                exit_slippage_ticks=s.portfolio_exit_slippage_ticks,
            )
            variant_r[m].append(variant.realized_r)
            variant_diff[m].append(variant.realized_r - base.realized_r)
            if variant.reason != base.reason:
                variant_flip[m] += 1

    baseline_summary = summarize(baseline_r)
    result: dict[str, Any] = {"n_trades": len(pairs), "baseline": baseline_summary, "variants": {}}
    print(f"\nN paired trades (valid pole height + positive risk): {len(pairs)}")
    print(
        f"{'variant':30s} {'N':>4s} {'totR':>8s} {'meanR':>7s} {'win%':>6s} "
        f"{'avgW':>6s} {'avgL':>7s} {'flip':>5s} {'pairedΔ':>8s} {'SE':>7s}"
    )
    b = baseline_summary
    print(
        f"{'baseline (2.0R fixed)':30s} {b['n']:4d} {b['total_r']:8.2f} {b['mean_r']:7.3f} "
        f"{(b['win_rate'] or 0):6.3f} {(b['avg_winner_r'] or 0):6.3f} "
        f"{(b['avg_loser_r'] or 0):7.3f} {'--':>5s} {'--':>8s} {'--':>7s}"
    )
    for m in M_GRID:
        vs = summarize(variant_r[m])
        mean_diff, se = paired_stats(variant_diff[m])
        result["variants"][str(m)] = {
            "m": m,
            "summary": vs,
            "n_flipped_outcome": variant_flip[m],
            "paired_mean_diff_r": mean_diff,
            "paired_se": se,
        }
        label = f"m={m} (pole x{m})"
        print(
            f"{label:30s} {vs['n']:4d} {vs['total_r']:8.2f} {vs['mean_r']:7.3f} "
            f"{(vs['win_rate'] or 0):6.3f} {(vs['avg_winner_r'] or 0):6.3f} "
            f"{(vs['avg_loser_r'] or 0):7.3f} {variant_flip[m]:5d} "
            f"{mean_diff:8.4f} {(se if se is not None else float('nan')):7.4f}"
        )
    return result


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--live", default="data/live", help="live store root")
    ap.add_argument("--recon", default="data/recon", help="recon store root")
    ap.add_argument(
        "--json",
        default="data/spikes/exit_structural_target.json",
        help="write the full result here (gitignored)",
    )
    args = ap.parse_args()

    s = Settings()
    stores: list[tuple[Store, str]] = []
    live_path = Path(args.live)
    recon_path = Path(args.recon)
    if live_path.exists():
        stores.append((Store(live_path), "live"))
    if recon_path.exists():
        stores.append((Store(recon_path), "recon"))
    if not stores:
        raise SystemExit(f"neither {live_path} nor {recon_path} exist")

    pairs = gather_trades(stores, s)
    result = run_sweep(pairs, s)

    out_path = Path(args.json)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=1, default=str))
    print(f"\nwrote {out_path}")


if __name__ == "__main__":
    main()
