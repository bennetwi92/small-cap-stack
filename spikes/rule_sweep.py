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
import itertools
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


# The pool the combination search draws from. **Deliberately includes conditions that are flat or
# negative on their own** — that is the entire point of searching combinations rather than stacking
# individual winners. `cmd_stack` only ever added rules that already looked good alone, so it could
# not find a feature that matters only in company (2 consolidation bars *given* a fresh scan, say).
POOL: list[tuple[str, pl.Expr]] = [
    ("price>=3", pl.col("entry_fill") >= 3.0),
    ("price>=5", pl.col("entry_fill") >= 5.0),
    ("price<=20", pl.col("entry_fill") <= 20.0),
    ("stop>=2.5%", pl.col("stop_pct") >= 0.025),
    ("stop>=4%", pl.col("stop_pct") >= 0.04),
    ("stop<=10%", pl.col("stop_pct") <= 0.10),
    ("break<08:00", pl.col("trigger_et_min") < 480),
    ("break<09:15", pl.col("trigger_et_min") < 555),
    ("1st pump", pl.col("cycle_num") <= 1),
    ("<=15m stale", pl.col("staleness_delay_min") <= 15),
    ("<=30m stale", pl.col("staleness_delay_min") <= 30),
    ("<=4 hits", pl.col("hits_before_trigger") <= 4),
    ("<=10 hits", pl.col("hits_before_trigger") <= 10),
    # --- the shape features the trader asked about: flat alone, kept in on purpose ---
    ("cons==2", pl.col("cons_len") == 2),
    ("cons<=2", pl.col("cons_len") <= 2),
    ("cons>=2", pl.col("cons_len") >= 2),
    ("pole==1", pl.col("pole_len") == 1),
    ("pole>=2", pl.col("pole_len") >= 2),
    ("retr>=100%", pl.col("retracement") >= 1.0),
    ("retr<50%", pl.col("retracement") < 0.50),
    # The U-shape: the 50-75% band is the worst bucket in the record, so "avoid the middle" is a
    # different rule from any one-sided threshold and no threshold can express it.
    ("not retr 50-75%", ~pl.col("retracement").is_between(0.50, 0.75, closed="left")),
    ("shape gates pass", pl.col("passed")),
    ("score>=0.5", pl.col("score") >= 0.5),
    # --- tape ---
    ("ran>=25% pre-scan", pl.col("runup_pre_appearance") >= 0.25),
    ("<75% up at break", pl.col("ext_at_trigger") < 0.75),
    ("pole>=40% of vol", pl.col("vol_share_pole") >= 0.40),
    ("$1M+ by break", pl.col("cum_dollar_vol_to_trigger") >= 1_000_000),
]


TARGET_GRID = (1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0)


def cmd_system(args: argparse.Namespace) -> None:
    """Search the filter AND the target together, at the capacity the trader actually wants.

    The remaining flaw in ``combos``, and it is a real one. Every filter there was scored at a
    **fixed 2R target**, which quietly selects for filters that produce 2R-shaped trades and throws
    away any filter whose edge is that its setups run *further*. A filter that hits 2R only 30% of
    the time but reaches 4R on most of those is better than one that hits 2R 40% of the time and
    stops dead — and scored at a fixed 2R the first one loses. Filter and target are one decision.

    Two changes follow:

    - **The objective is total R per session, not hit rate.** Hit rate cannot compare a 2R filter
      with a 4R one; R per session can, and it is what compounds.
    - **Capacity is a constraint, not an outcome.** The trader wants ~0.8 trades a day, so a
      combination is only admissible if it keeps roughly that many. That is what stops the search
      drifting to the 38-trade corner that looked best under a hit-rate objective and is not a
      strategy — and it is a *pre-declared* constraint, so it costs no evidence.

    Risk is left out on purpose and is not an oversight. At a fixed risk fraction, R per session is
    invariant to it, so risk cannot be chosen here — it is chosen against the *shape* of the
    resulting equity curve (drawdown, ruin), which needs the real book. That is the next step, not
    this one.

    Same discipline as ``combos``: fitted on the old sessions, scored on the recent ones, and the
    whole search re-run on shuffled outcomes so the luck benchmark moves with the larger grid —
    which it must, because searching filters x targets is a bigger search than filters alone.
    """
    df = _load(Path(args.panel))
    old = df.filter(pl.col("source") == "recon")
    new = df.filter(pl.col("source") == "live")
    n_old_days, n_new_days = old["dt"].n_unique(), new["dt"].n_unique()
    y_old = old["max_r"].to_numpy().astype(float)
    y_new = new["max_r"].to_numpy().astype(float)

    names = [n for n, _ in POOL]
    m_old = np.array([old.select(e.alias("m"))["m"].fill_null(False).to_numpy() for _, e in POOL])
    m_new = np.array([new.select(e.alias("m"))["m"].fill_null(False).to_numpy() for _, e in POOL])

    lo = args.per_day_min * n_old_days
    hi = args.per_day_max * n_old_days
    combos: list[tuple[tuple[int, ...], np.ndarray]] = []
    for k in range(1, args.max_rules + 1):
        for idx in itertools.combinations(range(len(POOL)), k):
            mask = np.logical_and.reduce(m_old[list(idx)], axis=0)
            if lo <= int(mask.sum()) <= hi:
                combos.append((idx, mask))

    print(
        f"\nold: {old.height} setups over {n_old_days} sessions ({old.height / n_old_days:.1f}/day)"
    )
    print(
        f"recent: {new.height} setups over {n_new_days} sessions "
        f"({new.height / n_new_days:.1f}/day)"
    )
    print(
        f"capacity constraint: {args.per_day_min}-{args.per_day_max} trades/day "
        f"-> {lo:.0f}-{hi:.0f} setups kept"
    )
    print(
        f"searching {len(combos)} filters x {len(TARGET_GRID)} targets = "
        f"{len(combos) * len(TARGET_GRID)} systems"
    )

    def best_system(y: np.ndarray, ms: list[np.ndarray]) -> tuple[float, int, float]:
        """Best (R per session, combo index, target) over the whole grid."""
        best = (-1e9, -1, 0.0)
        for i, m in enumerate(ms):
            yy = y[m]
            for t in TARGET_GRID:
                r_per_session = float(np.sum(np.where(yy >= t, t, -1.0))) / n_old_days
                if r_per_session > best[0]:
                    best = (r_per_session, i, t)
        return best

    masks = [m for _, m in combos]
    rng = np.random.default_rng(690)
    best_lucky = np.empty(args.shuffles)
    for s in range(args.shuffles):
        best_lucky[s] = best_system(rng.permutation(y_old), masks)[0]
    print(f"\nBEST BY LUCK — same search on shuffled outcomes, {args.shuffles} runs:")
    print(
        f"   median {np.median(best_lucky):+.3f} R/session, "
        f"90th pct {np.percentile(best_lucky, 90):+.3f}, best {best_lucky.max():+.3f}"
    )

    scored = []
    for i, (_idx, m) in enumerate(combos):
        yy = y_old[m]
        for t in TARGET_GRID:
            scored.append((float(np.sum(np.where(yy >= t, t, -1.0))) / n_old_days, i, t))
    scored.sort(reverse=True)

    print(f"\nTOP {args.top} SYSTEMS (filter + target, chosen on old data only)")
    print(f"{'filter':<50}{'targ':>5}{'/day':>6}{'R/sess':>8}{'rec/day':>8}{'rec R/sess':>11}")
    print("-" * 88)
    rec_rs: list[float] = []
    for r_sess, i, t in scored[: args.top]:
        idx, m = combos[i]
        nm = np.logical_and.reduce(m_new[list(idx)], axis=0)
        n_rec = int(nm.sum())
        rec = float(np.sum(np.where(y_new[nm] >= t, t, -1.0))) / n_new_days if n_rec >= 8 else None
        if rec is not None:
            rec_rs.append(rec)
        print(
            f"{' + '.join(names[j] for j in idx):<50}{t:>5.1f}"
            f"{int(m.sum()) / n_old_days:>6.2f}{r_sess:>+8.3f}{n_rec / n_new_days:>8.2f}"
            f"{(f'{rec:+.3f}' if rec is not None else '—'):>11}"
        )

    top = scored[0]
    idx0, _m0 = combos[top[1]]
    nm0 = np.logical_and.reduce(m_new[list(idx0)], axis=0)
    rec0 = float(np.sum(np.where(y_new[nm0] >= top[2], top[2], -1.0))) / n_new_days
    print(
        f"\nbest-on-old system carried to recent: {rec0:+.3f} R/session "
        f"on {int(nm0.sum())} trades ({int(nm0.sum()) / n_new_days:.2f}/day)"
    )
    if rec_rs:
        print(f"average of the top {len(rec_rs)} on recent: {np.mean(rec_rs):+.3f} R/session")
    print(
        f"beats the luck median? {'YES' if top[0] > np.median(best_lucky) else 'no'} "
        f"({top[0]:+.3f} vs {np.median(best_lucky):+.3f})"
    )

    print("\nwhich target the search chose, across the top 50:")
    tc: dict[float, int] = {}
    for _r, _i, t in scored[:50]:
        tc[t] = tc.get(t, 0) + 1
    for t, c in sorted(tc.items()):
        print(f"   {t:.1f}R: {c}")
    print("\nhow often each rule appears in the top 20:")
    counts: dict[str, int] = {}
    for _r, i, _t in scored[: args.top]:
        for j in combos[i][0]:
            counts[names[j]] = counts.get(names[j], 0) + 1
    for nm_, c in sorted(counts.items(), key=lambda kv: -kv[1])[:10]:
        print(f"   {nm_:<24}{c:>3} / {args.top}")


def cmd_combos(args: argparse.Namespace) -> None:
    """Search every combination of up to N rules — and measure how good "lucky" looks.

    The trader's objection to ``single``/``stack`` is correct: a feature can be flat alone and
    matter in company, and neither of those commands could ever find one. This searches the pool
    exhaustively instead.

    That creates the obvious problem. Searching ~20,000 combinations against a 25% base rate will
    turn up something that looks excellent whether or not anything real is there — which is exactly
    how §D-38's 150-variant sweep and the §D-39/§D-40 collapse happened. Two defences, and the
    second is the one that matters:

    1. **Fit on the old data, score on the recent data.** Combinations are ranked on the 166 recon
       sessions; the live sessions are never consulted while choosing, only reported afterwards.
    2. **Run the identical search on shuffled outcomes.** Permuting max R across setups destroys
       every real relationship while preserving the sample size, the base rate and the correlation
       structure *between the rules themselves*. The best combination found on shuffled data is
       therefore a direct measurement of what this much searching buys from luck alone. If the real
       winner does not clear that bar, it is not a finding — and no amount of care in how the rules
       were chosen changes that.

    The shuffled benchmark is reported as a distribution, not a single number, so "the best real
    combination beat 90% of lucky ones" can be read off directly.
    """
    df = _load(Path(args.panel))
    old = df.filter(pl.col("source") == "recon")
    new = df.filter(pl.col("source") == "live")
    y_old = old["max_r"].to_numpy().astype(float)
    y_new = new["max_r"].to_numpy().astype(float)
    hit_old_base = float(np.mean(y_old >= TARGET_R))
    hit_new_base = float(np.mean(y_new >= TARGET_R))

    names = [n for n, _ in POOL]
    m_old = np.array([old.select(e.alias("m"))["m"].fill_null(False).to_numpy() for _, e in POOL])
    m_new = np.array([new.select(e.alias("m"))["m"].fill_null(False).to_numpy() for _, e in POOL])

    combos: list[tuple[tuple[int, ...], np.ndarray]] = []
    for k in range(1, args.max_rules + 1):
        for idx in itertools.combinations(range(len(POOL)), k):
            mask = np.logical_and.reduce(m_old[list(idx)], axis=0)
            if int(mask.sum()) >= args.min_old:
                combos.append((idx, mask))
    print(f"\nold data: {old.height} setups ({hit_old_base * 100:.1f} in 100)")
    print(f"recent data: {new.height} setups ({hit_new_base * 100:.1f} in 100)")
    print(
        f"combinations searched (up to {args.max_rules} rules, >={args.min_old} setups kept): "
        f"{len(combos)}"
    )

    hits = np.array([float(np.mean(y_old[m] >= TARGET_R)) for _, m in combos])

    # The luck benchmark: same combinations, same sample, outcomes shuffled.
    rng = np.random.default_rng(690)
    best_lucky = np.empty(args.shuffles)
    for s in range(args.shuffles):
        ys = rng.permutation(y_old)
        best_lucky[s] = max(float(np.mean(ys[m] >= TARGET_R)) for _, m in combos)
    print(f"\nBEST BY LUCK — same search on shuffled outcomes, {args.shuffles} runs:")
    print(
        f"   median {np.median(best_lucky) * 100:.1f} in 100, "
        f"90th pct {np.percentile(best_lucky, 90) * 100:.1f}, "
        f"best {best_lucky.max() * 100:.1f}"
    )

    order = np.argsort(-hits)[: args.top]
    print(f"\nTOP {args.top} COMBINATIONS (chosen on old data only)")
    print(f"{'rules':<56}{'old n':>6}{'old':>7}{'rec n':>7}{'recent':>8}")
    print("-" * 84)
    recents: list[float] = []
    for i in order:
        idx, mask = combos[i]
        label = " + ".join(names[j] for j in idx)
        nm = np.logical_and.reduce(m_new[list(idx)], axis=0)
        n_rec = int(nm.sum())
        rec = float(np.mean(y_new[nm] >= TARGET_R)) * 100 if n_rec >= args.min_new else None
        if rec is not None:
            recents.append(rec)
        print(
            f"{label:<56}{int(mask.sum()):>6}{hits[i] * 100:>7.1f}{n_rec:>7}"
            f"{(f'{rec:.1f}' if rec is not None else '—'):>8}"
        )

    # The two numbers that are actually defensible. The single best-on-old combination carried to
    # the recent data is the only *unbiased* estimate here — nothing about the recent data informed
    # the choice. The top-K average is the same idea made less brittle: reading down the recent
    # column and keeping the ones that held up would be a second round of selection, and would put
    # the search straight back where §D-39/§D-40 started.
    best_i = int(order[0])
    bnm = np.logical_and.reduce(m_new[list(combos[best_i][0])], axis=0)
    print(f"\nbase rate on recent data: {hit_new_base * 100:.1f} in 100")
    print(
        f"single best-on-old combination, carried to recent: "
        f"{float(np.mean(y_new[bnm] >= TARGET_R)) * 100:.1f} in 100 on {int(bnm.sum())} setups"
    )
    if recents:
        print(
            f"average of the top {len(recents)} on recent: {np.mean(recents):.1f} in 100 "
            f"(vs {hit_new_base * 100:.1f} base)"
        )
    # Which single conditions the search actually keeps choosing — a far more stable read than any
    # one winning combination, and it cannot be cherry-picked from the recent column.
    print("\nhow often each rule appears in the top 20 (the search's own preference):")
    counts: dict[str, int] = {}
    for i in order:
        for j in combos[i][0]:
            counts[names[j]] = counts.get(names[j], 0) + 1
    for nm_, c in sorted(counts.items(), key=lambda kv: -kv[1])[:10]:
        print(f"   {nm_:<24}{c:>3} / {len(order)}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    y = sub.add_parser("system", help="search filter AND target together, at a capacity constraint")
    y.add_argument("--panel", default=str(PANEL_DEFAULT))
    y.add_argument("--max-rules", type=int, default=4)
    y.add_argument("--per-day-min", type=float, default=0.6)
    y.add_argument("--per-day-max", type=float, default=1.0)
    y.add_argument("--shuffles", type=int, default=100)
    y.add_argument("--top", type=int, default=20)
    y.set_defaults(func=cmd_system)
    c = sub.add_parser("combos", help="search all rule combinations, against a luck benchmark")
    c.add_argument("--panel", default=str(PANEL_DEFAULT))
    c.add_argument("--max-rules", type=int, default=4)
    c.add_argument("--min-old", type=int, default=100)
    c.add_argument("--min-new", type=int, default=20)
    c.add_argument("--shuffles", type=int, default=200)
    c.add_argument("--top", type=int, default=20)
    c.set_defaults(func=cmd_combos)
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
