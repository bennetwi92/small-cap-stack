"""Spike #690 (stage 5): which single rules actually pick better setups?

The regime work said the information in this record sits at the **opportunity** level, and the
adaptive-book sweep said the target is already right and the risk ladder is only braking. So this is
the piece that matters: a scorecard for the rules that decide *which setups to take*.

## The one discipline that makes this different from what came before

§D-38 records >150 threshold variants swept over 79 sessions; §D-39/§D-40 were fitted on 61 and
collapsed out of sample (+0.414R per trade inside the fit window, −0.262R after). Sweeping harder is
what caused that, so the defence here is not a bigger sweep, it is a **cheaper verdict**:

**A rule counts only if it helps in the OLD data and the RECENT data separately.** The record splits
naturally — 166 reconstructed sessions (2025-10-30 → 2026-06-30) and 31 live ones (2026-07-01 →
08-13) — and that split has already killed things that looked strong pooled: trailing-quality
terciles run 0.228 → 0.287 on recon and reverse to 0.302 → 0.214 on live. Every rule below is
reported both ways, and `both` is the column to read.

That is a weak test, deliberately. It cannot confirm a rule; it can only refuse one. With 3,740
setups and a base rate near 25%, nothing available here can do better, and pretending otherwise is
how the last two rules got shipped.

## What the numbers mean

- **keeps** — how many of the 3,740 pre-market setups survive the rule.
- **hit** — of those, the share that run to 2R before stopping out.
- **R/trade** — mean R at a fixed 2R target: +2 when it hits, −1 when it stops. Pre-cost.
- Break-even is **hit = 0.333**. After the costs a $500 account actually pays (~0.287R per trade,
  measured in the 2026-08-13 report) the real bar is **hit ≈ 0.429**, printed as a reference line.

    python spikes/rule_sweep.py single
    python spikes/rule_sweep.py stack
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import polars as pl

PANEL_DEFAULT = Path("data/spikes/regime_panel.parquet")
PREMARKET_CUT = 555.0  # 09:15 ET — the population, not a rule (see regime_scan.py)
TARGET_R = 2.0
COST_DRAG_R = 0.287  # per-trade cost at $500, from the 2026-08-13 report

# Every candidate rule, as a single condition. Grouped by what it is about, so the scorecard reads
# as "here is what each lever buys" rather than as an undifferentiated grid.
RULES: list[tuple[str, str, pl.Expr]] = [
    # --- price ---
    ("price", "price >= $2", pl.col("entry_fill") >= 2.0),
    ("price", "price >= $3  (shipped)", pl.col("entry_fill") >= 3.0),
    ("price", "price >= $5", pl.col("entry_fill") >= 5.0),
    ("price", "price <= $10", pl.col("entry_fill") <= 10.0),
    ("price", "price <= $20", pl.col("entry_fill") <= 20.0),
    ("price", "price <= $50 (shipped)", pl.col("entry_fill") <= 50.0),
    # --- stop distance ---
    ("stop", "stop >= 2%", pl.col("stop_pct") >= 0.02),
    ("stop", "stop >= 2.5% (shipped)", pl.col("stop_pct") >= 0.025),
    ("stop", "stop >= 4%", pl.col("stop_pct") >= 0.04),
    ("stop", "stop <= 10%", pl.col("stop_pct") <= 0.10),
    ("stop", "stop <= 15%", pl.col("stop_pct") <= 0.15),
    ("stop", "stop <= 20%", pl.col("stop_pct") <= 0.20),
    # --- time of day ---
    ("time", "break before 08:00", pl.col("trigger_et_min") < 480),
    ("time", "break before 09:15 (shipped)", pl.col("trigger_et_min") < 555),
    ("time", "break after 06:00", pl.col("trigger_et_min") >= 360),
    ("time", "break after 07:00", pl.col("trigger_et_min") >= 420),
    # --- freshness: how worn the move is by the time it breaks ---
    ("fresh", "first pump of the day", pl.col("cycle_num") <= 1),
    ("fresh", "cycle <= 2 (shipped)", pl.col("cycle_num") <= 2),
    ("fresh", "break within 30m of scan (shipped)", pl.col("staleness_delay_min") <= 30),
    ("fresh", "break within 15m of scan", pl.col("staleness_delay_min") <= 15),
    ("fresh", "<= 1 scan hit before break", pl.col("hits_before_trigger") <= 1),
    ("fresh", "<= 4 scan hits before break", pl.col("hits_before_trigger") <= 4),
    ("fresh", "<= 10 scan hits before break", pl.col("hits_before_trigger") <= 10),
    # --- flag shape ---
    ("shape", "all shape gates pass (shipped)", pl.col("passed")),
    ("shape", "consolidation 2-3 bars", pl.col("cons_len").is_between(2, 3)),
    ("shape", "consolidation <= 3 bars", pl.col("cons_len") <= 3),
    ("shape", "pole >= 2 bars", pl.col("pole_len") >= 2),
    ("shape", "pullback >= 25% of pole", pl.col("retracement") >= 0.25),
    ("shape", "pullback >= 50% of pole", pl.col("retracement") >= 0.50),
    ("shape", "quality score >= 0.5", pl.col("score") >= 0.5),
    ("shape", "quality score >= 0.6", pl.col("score") >= 0.6),
    # --- the tape ---
    ("tape", "pole is >=40% of day's volume", pl.col("vol_share_pole") >= 0.40),
    ("tape", "already ran >=25% before scan", pl.col("runup_pre_appearance") >= 0.25),
    ("tape", "not yet up 75% at the break", pl.col("ext_at_trigger") < 0.75),
    ("tape", "traded >= $1M by the break", pl.col("cum_dollar_vol_to_trigger") >= 1_000_000),
]


def _load(panel: Path) -> pl.DataFrame:
    df = pl.read_parquet(panel).filter(pl.col("triggered"))
    return df.filter(pl.col("first_hit_et_min") < PREMARKET_CUT)


def _score(df: pl.DataFrame) -> tuple[int, float, float]:
    """``(n, hit rate at 2R, mean R per trade)`` — the whole vocabulary of this spike."""
    if df.is_empty():
        return (0, float("nan"), float("nan"))
    mr = df["max_r"].to_numpy()
    hit = float(np.mean(mr >= TARGET_R))
    r = float(np.mean(np.where(mr >= TARGET_R, TARGET_R, -1.0)))
    return (df.height, hit, r)


def cmd_single(args: argparse.Namespace) -> None:
    df = _load(Path(args.panel))
    n0, hit0, r0 = _score(df)
    print(f"\nall {n0} pre-market setups: hit {hit0:.3f}, R/trade {r0:+.3f}")
    print(f"break-even hit rate = 0.333 before costs, {0.333 + COST_DRAG_R / 3:.3f} after them\n")

    print(
        f"{'':7} {'rule':<34} {'keeps':>6} {'hit':>6} {'R/trade':>8} {'vs base':>8} "
        f"{'old':>7} {'recent':>7}  both?"
    )
    print("-" * 100)
    rows = []
    for group, label, expr in RULES:
        sub = df.filter(expr)
        n, hit, r = _score(sub)
        if n < 40:
            print(f"{group:<7} {label:<34} {n:>6}   (too few to judge)")
            continue
        _, h_old, _ = _score(sub.filter(pl.col("source") == "recon"))
        _, h_new, _ = _score(sub.filter(pl.col("source") == "live"))
        _, b_old, _ = _score(df.filter(pl.col("source") == "recon"))
        _, b_new, _ = _score(df.filter(pl.col("source") == "live"))
        # A rule "works in both halves" if it lifts the hit rate above that half's OWN base rate —
        # the halves have different base rates, so comparing both against the pooled one would
        # credit or punish a rule for which store it is measured in.
        both = (h_old > b_old) and (h_new > b_new)
        rows.append((group, label, n, hit, r, hit - hit0, h_old - b_old, h_new - b_new, both))
        print(
            f"{group:<7} {label:<34} {n:>6} {hit:>6.3f} {r:>+8.3f} {hit - hit0:>+8.3f} "
            f"{h_old - b_old:>+7.3f} {h_new - b_new:>+7.3f}  {'YES' if both else '-'}"
        )

    keep = [x for x in rows if x[8]]
    print(f"\n{len(keep)} of {len(rows)} rules help in BOTH halves:")
    for _g, label, n, hit, r, lift, _o, _n2, _b in sorted(keep, key=lambda x: -x[5]):
        print(f"   {label:<34} keeps {n:>5}  hit {hit:.3f} ({lift:+.3f})  R/trade {r:+.3f}")


def cmd_stack(args: argparse.Namespace) -> None:
    """Add the surviving rules one at a time, best first, and watch what the stack does.

    Two things this is looking for. Rules overlap — a second rule that only removes setups the first
    already removed adds nothing but shrinks the sample. And the stack has to stay large enough to
    trade: the shipped book already fills 0.55 of its 2 daily slots, so a stack that lands on 30
    setups in 197 sessions is not a strategy, it is a story about 30 setups.
    """
    df = _load(Path(args.panel))
    n0, hit0, r0 = _score(df)
    survivors: list[tuple[str, pl.Expr]] = []
    for group, label, expr in RULES:
        sub = df.filter(expr)
        if sub.height < 40:
            continue
        _, h_old, _ = _score(sub.filter(pl.col("source") == "recon"))
        _, h_new, _ = _score(sub.filter(pl.col("source") == "live"))
        _, b_old, _ = _score(df.filter(pl.col("source") == "recon"))
        _, b_new, _ = _score(df.filter(pl.col("source") == "live"))
        if h_old > b_old and h_new > b_new:
            _n, hit, _r = _score(sub)
            survivors.append((f"{group}: {label}", expr))

    # Greedy: at each step add whichever remaining survivor most improves the stack's hit rate,
    # subject to keeping at least `--min-keep` setups. Greedy is not optimal and is not meant to be
    # — it is a readable ordering, and the both-halves column is what any of it rests on.
    chosen: list[str] = []
    cur = df
    remaining = list(survivors)
    print(f"\nstart: {n0} setups, hit {hit0:.3f}, R/trade {r0:+.3f}")
    print(
        f"\n{'step':<5} {'rule added':<44} {'keeps':>6} {'hit':>6} {'R/trade':>8} "
        f"{'old':>6} {'recent':>7}"
    )
    print("-" * 92)
    for step in range(1, args.max_rules + 1):
        best = None
        for label, expr in remaining:
            cand = cur.filter(expr)
            if cand.height < args.min_keep:
                continue
            _n, hit, r = _score(cand)
            if best is None or hit > best[2]:
                best = (label, expr, hit, r, cand)
        if best is None:
            break
        label, expr, hit, r, cand = best
        _, h_old, _ = _score(cand.filter(pl.col("source") == "recon"))
        _, h_new, _ = _score(cand.filter(pl.col("source") == "live"))
        print(
            f"{step:<5} {label:<44} {cand.height:>6} {hit:>6.3f} {r:>+8.3f} "
            f"{h_old:>6.3f} {h_new:>7.3f}"
        )
        chosen.append(label)
        cur = cand
        remaining = [(lb, ex) for lb, ex in remaining if lb != label]

    n, hit, r = _score(cur)
    days = cur["dt"].n_unique()
    print(
        f"\nfinal stack: {n} setups over {days} sessions "
        f"({n / max(1, args.sessions):.2f} per session)"
    )
    print(f"hit {hit:.3f}, R/trade {r:+.3f}, after costs {r - COST_DRAG_R:+.3f}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("single", help="score every candidate rule on its own")
    s.add_argument("--panel", default=str(PANEL_DEFAULT))
    s.set_defaults(func=cmd_single)
    t = sub.add_parser("stack", help="add surviving rules one at a time, best first")
    t.add_argument("--panel", default=str(PANEL_DEFAULT))
    t.add_argument("--min-keep", type=int, default=150)
    t.add_argument("--max-rules", type=int, default=8)
    t.add_argument("--sessions", type=int, default=197)
    t.set_defaults(func=cmd_stack)
    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
