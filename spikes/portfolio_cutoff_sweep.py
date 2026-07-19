"""Spike #379: sweep the virtual book's pre-market cutoff (and price cap) over the archive.

Answers "what would the paper book have done if I hadn't limited myself to pre-market?" by running
the **real** `build_portfolio_payload` under `Settings` overrides — no re-implementation, so the
sweep can never drift from the live book. Two views:

1. **book sweep** — the full adaptive book per variant (trades, R, equity, drawdown). This is what
   you'd actually have experienced, kill-switch ladder and costs included.
2. **signal isolation** — every candidate unsized, no per-day cap, no costs, exited at a fixed 2R,
   bucketed pre-market vs post-open. Strips out the ladder so the buckets are compared directly;
   the book sweep alone conflates "these trades lose" with "these losses throttle risk to zero".

Conclusion as of 2026-07-19 (12 sessions, 45 candidates): **keep the 09:30 cutoff** — every
relaxation is worse and only the baseline is profitable. See #379 for the numbers and caveats.

⚠️ Any variant tested here must be **decidable at trigger time**. Ranking a day's candidates
against each other ("take the best two") is look-ahead bias and is not a valid experiment; only
thresholds on values known when the trade fires are.

⚠️ **The book sweep is currently not reproducible run-to-run.** `day_opportunities` uses polars
`.unique(keep="first")` without `maintain_order=True`, so opportunity order permutes every run;
`extract_day_trades` stable-sorts by `trigger_at`, so same-bar ties break by that arbitrary order,
and the `max_trades_per_day` cap then takes a different pair whenever a tie straddles the boundary
(e.g. MULL/SNDU/SNXX all trigger 12:45 on 2026-07-14). Expect the book rows to wobble by a trade or
two; the *signal* view is unaffected (it caps nothing, so order cannot matter). Filed as its own
bug — re-pin these numbers once it is fixed.

Run against a copy of the box's store (`docker cp` it out, or query `/data` on the VPS directly):

    .venv/bin/python spikes/portfolio_cutoff_sweep.py --store /path/to/store
    .venv/bin/python spikes/portfolio_cutoff_sweep.py --store /data --json data/spikes/sweep.json
"""

import argparse
import json
import statistics
from datetime import UTC, date, datetime, time
from pathlib import Path

from small_cap_stack.clock import ET
from small_cap_stack.config import Settings
from small_cap_stack.portfolio.extract import extract_day_trades
from small_cap_stack.portfolio.payload import build_portfolio_payload, collected_dates
from small_cap_stack.storage import Store

# (label, Settings overrides). The first entry is the live configuration.
VARIANTS: list[tuple[str, dict]] = [
    ("pre-market only (09:30) — live", {}),
    ("cutoff 10:00", {"portfolio_premarket_cutoff": time(10, 0)}),
    ("cutoff 11:00", {"portfolio_premarket_cutoff": time(11, 0)}),
    ("no cutoff (full 04:00-11:59)", {"portfolio_premarket_cutoff": time(12, 0)}),
    (
        "no cutoff + price cap $50",
        {"portfolio_premarket_cutoff": time(12, 0), "portfolio_entry_price_max": 50.0},
    ),
    ("pre-market only + price cap $50", {"portfolio_entry_price_max": 50.0}),
]

CUTOFF = time(9, 30)  # the bucket boundary for the signal view, not a filter


def book_sweep(store: Store, now: datetime) -> dict:
    """Run the real adaptive book once per variant."""
    out = {}
    print("=== Adaptive book per variant ===")
    for label, overrides in VARIANTS:
        book = build_portfolio_payload(store, Settings(**overrides), now)["books"]["adaptive"]
        st = book["stats"]
        out[label] = {
            "stats": st,
            "trades": book["trades"],
            "equity_curve": book["equity_curve"],
            "daily_risk": book["daily_risk"],
        }
        print(
            f"{label:34s} n={st['n_trades']:3d} win%={st['win_rate']:.3f} "
            f"totR={st['total_r']:+7.2f} end=${st['end_equity']:8.2f} "
            f"ret={st['return_pct']:+.4f} maxDD={st['max_drawdown_pct']:.4f}"
        )
    return out


def signal_isolation(store: Store) -> dict:
    """Bucket every candidate pre-market vs post-open, unsized and cost-free at a 2R target."""
    # Widest band so nothing is hidden by the price cap — this view is about time of day.
    s = Settings(portfolio_premarket_cutoff=time(12, 0), portfolio_entry_price_max=50.0)
    buckets: dict[str, list[float]] = {"pre-market": [], "post-open": []}
    per_day: dict[str, dict[str, int]] = {}
    for d in collected_dates(store):
        for c in extract_day_trades(store, s, d):
            b = "pre-market" if c.trigger_at.astimezone(ET).time() < CUTOFF else "post-open"
            buckets[b].append(c.exit_under(s, target_r=2.0, breakeven_r=0.0).realized_r)
            per_day.setdefault(str(d), {"pre-market": 0, "post-open": 0})[b] += 1

    print("\n=== Signal: unsized, no per-day cap, no costs, 2R target ===")
    summary = {}
    for b, rs in buckets.items():
        if not rs:
            continue
        summary[b] = {
            "n": len(rs),
            "win_rate": round(len([r for r in rs if r > 0]) / len(rs), 4),
            "total_r": round(sum(rs), 4),
            "avg_r": round(statistics.mean(rs), 4),
            "median_r": round(statistics.median(rs), 4),
        }
        v = summary[b]
        print(
            f"{b:12s} n={v['n']:3d}  win%={v['win_rate']:.3f}  totR={v['total_r']:+7.2f}  "
            f"avgR={v['avg_r']:+.3f}  medR={v['median_r']:+.3f}"
        )

    print("\n=== Candidates per day (pre / post) ===")
    for d, v in sorted(per_day.items()):
        print(f"  {d}  pre={v['pre-market']:2d}  post={v['post-open']:2d}")
    return {"buckets": summary, "per_day": per_day}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--store", required=True, help="path to a Parquet store (a copy of /data)")
    ap.add_argument("--json", help="also write the full result here (use data/spikes/, gitignored)")
    ap.add_argument("--as-of", help="generated-at date, YYYY-MM-DD (default: today)")
    args = ap.parse_args()

    store = Store(Path(args.store))
    as_of = date.fromisoformat(args.as_of) if args.as_of else datetime.now(UTC).date()
    now = datetime.combine(as_of, time(12, 0), tzinfo=UTC)

    result = {
        "as_of": str(as_of),
        "books": book_sweep(store, now),
        "signal": signal_isolation(store),
    }
    if args.json:
        Path(args.json).write_text(json.dumps(result, indent=1, default=str))
        print(f"\nwrote {args.json}")


if __name__ == "__main__":
    main()
