"""Spike #690 (stage 2): does an aggregate of the last N days tell you anything about today?

Reads the wide panel built by ``regime_panel.py`` — 5,024 setups over 197 sessions, with **no**
fitted selection rule applied (see that module's docstring) — and asks the regime question three
ways:

1. **scan** — every trailing aggregate against every same-day target. Spearman rho, a circular-shift
   permutation p-value (which preserves the target's own autocorrelation and destroys only the
   pairing, so an autocorrelated series can't manufacture significance), Benjamini-Hochberg FDR
   over the whole grid, and the hypothesis count printed at the top. Discovery runs on **recon**
   (166 sessions); **live** (31 sessions) is held out and reported beside it.
2. **terciles** — split sessions by a trailing feature and compare the pooled setup-level outcome
   in each third, with day-block bootstrap CIs. A correlation of 0.15 and a flat tercile table mean
   different things, and the tercile table is what a risk rule would actually read.
3. **interact** — the question "should the filter be different per regime?", asked in the only form
   this sample can answer: does the *ordering* of a filter feature's buckets change across regime
   terciles? A per-regime threshold grid is the unpooled extreme and is exactly the D-39/D-40
   failure mode at a third of the data; a rank-order flip is the cheap precondition for it being
   worth anything at all.

## Trailing windows are pooled and strictly causal

A window of N ending yesterday pools **every setup in those N sessions** and computes the statistic
on the pooled set — not the mean of N daily statistics, which would give a 7-setup session the same
weight as a 108-setup one. Every feature is decidable before today's open.

## Pre-market only, and why that also fixes the store boundary

The population is every setup whose **scanner appearance** is before 09:15 ET. Names first seen
in-market are not what this book trades, so they are not what a regime should be measured on.

That cut does a second job. On the raw population the two stores look incomparable — recon 19.7
setups/session against live 56.5 — which would make any count-like feature partly a reading of the
store boundary rather than of the tape. The gap is almost entirely **in-market appearances**: the
recon store reconstructs pre-market only (no appearance later than 09:30 in 166 sessions) while the
live store scans all day. Restricted to pre-market the two sit at 18.5 and 21.4 setups/session and
the monthly series runs continuously across the 2026-06-30 -> 07-01 boundary (26.1 -> 20.6/day),
with no step. So the record is usable as **one** 197-session trailing series, which is what a
trailing-20 window needs.

Count-like features are still carried **twice** — raw, and scale-free as a ratio to their own
trailing-20 baseline — because a residual level drift is cheap to guard against and the ratio
version is the one that says "busier than lately" rather than "later in the record".

⚠️ Trigger time stays a **filter** feature (it is in ``FILTER_FEATURES`` below), not a population
rule. A name seen at 08:50 that breaks at 10:30 is in the population; whether to take it is exactly
the sort of thing the regime-conditional filter is supposed to decide.

    python spikes/regime_scan.py scan --panel data/spikes/regime_panel.parquet
    python spikes/regime_scan.py terciles --feature p50_max_r_w5 --target p2r
    python spikes/regime_scan.py interact --feature p50_max_r_w5
"""

from __future__ import annotations

import argparse
import math
from collections.abc import Sequence
from pathlib import Path

import numpy as np
import polars as pl

PANEL_DEFAULT = Path("data/spikes/regime_panel.parquet")
WINDOWS = (1, 3, 5, 10, 20)
RNG_SEED = 690

# The target grid the "total R" measures price against. A fixed target-or-stop reading of max R:
# +T if the peak reached T before the stop, else -1. Realisable, unlike a sum of peaks.
TARGET_R = 2.0


# --------------------------------------------------------------------------------------------
# day statistics
# --------------------------------------------------------------------------------------------


def _stats(max_r: np.ndarray, stopped: np.ndarray) -> dict[str, float]:
    """The aggregate vocabulary, computed over a pooled set of setups."""
    n = len(max_r)
    if n == 0:
        return {}
    realised = np.where(max_r >= TARGET_R, TARGET_R, -1.0)
    return {
        "n_opps": float(n),
        "max_max_r": float(np.max(max_r)),
        "p25_max_r": float(np.percentile(max_r, 25)),
        "p50_max_r": float(np.percentile(max_r, 50)),
        "p75_max_r": float(np.percentile(max_r, 75)),
        "mean_max_r": float(np.mean(max_r)),
        "total_r": float(np.sum(realised)),
        "r_per_opp": float(np.mean(realised)),
        "sum_max_r": float(np.sum(max_r)),
        "p1r": float(np.mean(max_r >= 1.0)),
        "p2r": float(np.mean(max_r >= 2.0)),
        "p3r": float(np.mean(max_r >= 3.0)),
        "stop_rate": float(np.mean(stopped)),
    }


STAT_NAMES = (
    "n_opps",
    "max_max_r",
    "p25_max_r",
    "p50_max_r",
    "p75_max_r",
    "mean_max_r",
    "total_r",
    "r_per_opp",
    "sum_max_r",
    "p1r",
    "p2r",
    "p3r",
    "stop_rate",
)
# Statistics whose LEVEL is not comparable across the recon/live boundary, so they also get a
# scale-free twin (ratio to the same statistic over the trailing 20). See the module docstring.
COUNT_LIKE = ("n_opps", "total_r", "sum_max_r")


def day_table(df: pl.DataFrame) -> pl.DataFrame:
    """One row per session: the statistic vocabulary computed on that session's setups."""
    rows = []
    for (dt, source), g in sorted(df.group_by(["dt", "source"], maintain_order=False)):  # type: ignore[misc]
        mr = g["max_r"].to_numpy()
        st = g["stopped_out"].to_numpy()
        rows.append({"dt": dt, "source": source, **_stats(mr, st)})
    return pl.DataFrame(rows).sort("dt")


def trailing_features(df: pl.DataFrame, days: pl.DataFrame) -> pl.DataFrame:
    """For each session, the pooled statistics over each trailing window ENDING YESTERDAY.

    Pooled, not averaged: a window's statistic is computed over every setup in it, so a 7-setup
    session does not carry the same weight as a 108-setup one.
    """
    dts = days["dt"].to_list()
    idx = {d: i for i, d in enumerate(dts)}
    per_day: list[tuple[np.ndarray, np.ndarray]] = []
    for d in dts:
        g = df.filter(pl.col("dt") == d)
        per_day.append((g["max_r"].to_numpy(), g["stopped_out"].to_numpy()))

    out = []
    for d in dts:
        i = idx[d]
        row: dict[str, object] = {"dt": d}
        base20: dict[str, float] = {}
        for w in sorted(WINDOWS, reverse=True):
            lo = i - w
            if lo < 0:  # not enough history yet — leave the whole window null
                continue
            mr = np.concatenate([per_day[j][0] for j in range(lo, i)])
            st = np.concatenate([per_day[j][1] for j in range(lo, i)])
            s = _stats(mr, st)
            if w == 20:
                base20 = s
            for k, v in s.items():
                row[f"{k}_w{w}"] = v
                if k in COUNT_LIKE and w != 20:
                    # Scale-free twin: the window's level against its own 20-day baseline, so the
                    # feature says "busier than lately" rather than "in the live store".
                    b = base20.get(k)
                    per = None if b is None else b / 20.0 * w  # the 20-day rate over w sessions
                    row[f"{k}_rel_w{w}"] = (
                        None if not per or abs(per) < 1e-9 else float(v / per)  # type: ignore[arg-type]
                    )
        out.append(row)
    return pl.DataFrame(out, infer_schema_length=None).sort("dt")


# --------------------------------------------------------------------------------------------
# statistics
# --------------------------------------------------------------------------------------------


def _rank(a: np.ndarray) -> np.ndarray:
    order = a.argsort(kind="stable")
    r = np.empty(len(a), dtype=float)
    r[order] = np.arange(len(a), dtype=float)
    # average ties, so a feature with repeated values (a count) is not given a spurious ordering
    _, inv, counts = np.unique(a, return_inverse=True, return_counts=True)
    sums = np.zeros(len(counts))
    np.add.at(sums, inv, r)
    return (sums / counts)[inv]


def spearman(x: np.ndarray, y: np.ndarray) -> float:
    if len(x) < 3:
        return float("nan")
    rx, ry = _rank(x), _rank(y)
    sx, sy = rx.std(), ry.std()
    if sx == 0 or sy == 0:
        return float("nan")
    return float(((rx - rx.mean()) * (ry - ry.mean())).mean() / (sx * sy))


def circular_shift_p(x: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    """``(rho, p)`` for Spearman(x, y) under circular shifts of y — the **exact** test.

    A plain shuffle of ``y`` destroys its serial correlation, which is what makes an autocorrelated
    predictor look significant against an autocorrelated target; a circular shift preserves ``y``'s
    autocorrelation exactly and breaks only the pairing with ``x``. That is the null we want.

    There are only ``n - 1`` non-trivial shifts, so the test is enumerated rather than sampled —
    which is both exact and far cheaper than drawing from it. Spearman over ranks is Pearson, so
    every shift is one row of a single matrix product: at n < 250 the whole null distribution costs
    one ``(n, n) @ (n,)``. The sampled version of this was the bottleneck that made a full scan
    (845 pairs x 3 scopes) run for over ten minutes.
    """
    n = len(x)
    if n < 8:
        return (float("nan"), float("nan"))
    rx, ry = _rank(x), _rank(y)
    xc, yc = rx - rx.mean(), ry - ry.mean()
    denom = math.sqrt(float((xc**2).sum()) * float((yc**2).sum()))
    if denom == 0:
        return (float("nan"), float("nan"))
    # Row s of `idx` indexes yc rolled by s, so `Y @ xc` is every shift's covariance at once.
    idx = (np.arange(n)[None, :] - np.arange(n)[:, None]) % n
    rhos = (yc[idx] @ xc) / denom
    obs = float(rhos[0])
    null = np.abs(rhos[1:])
    return (obs, float((np.sum(null >= abs(obs)) + 1) / n))


def bh_fdr(pvals: Sequence[float]) -> list[float]:
    """Benjamini-Hochberg adjusted p-values (nan-safe, order-preserving)."""
    p = np.asarray(pvals, dtype=float)
    ok = ~np.isnan(p)
    out = np.full(len(p), np.nan)
    if not ok.any():
        return out.tolist()
    sub = p[ok]
    m = len(sub)
    order = np.argsort(sub)
    ranked = sub[order] * m / (np.arange(m) + 1)
    ranked = np.minimum.accumulate(ranked[::-1])[::-1]
    adj = np.empty(m)
    adj[order] = np.clip(ranked, 0, 1)
    out[ok] = adj
    return out.tolist()


def day_block_bootstrap_ci(
    day_groups: list[np.ndarray],
    fn,
    draws: int,
    rng: np.random.Generator,
) -> tuple[float, float]:
    """Percentile CI resampling whole SESSIONS with replacement (setups in a day share a tape)."""
    if not day_groups:
        return (float("nan"), float("nan"))
    n = len(day_groups)
    vals = []
    for _ in range(draws):
        pick = rng.integers(0, n, size=n)
        pooled = np.concatenate([day_groups[k] for k in pick])
        vals.append(fn(pooled))
    return (float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5)))


# --------------------------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------------------------


def _load(args: argparse.Namespace) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    df = pl.read_parquet(args.panel).filter(pl.col("triggered"))
    cut = getattr(args, "premarket_cut", 555)
    if cut is not None:
        before = df.height
        df = df.filter(pl.col("first_hit_et_min") < cut)
        print(
            f"population: appearance before {int(cut) // 60:02d}:{int(cut) % 60:02d} ET "
            f"-> {df.height} of {before} setups"
        )
    days = day_table(df)
    feats = trailing_features(df, days)
    joined = days.join(feats, on="dt", how="left")
    return df, days, joined


def _detrend_ranks(v: np.ndarray) -> np.ndarray:
    """Residual of ``v``'s ranks after removing a linear trend in calendar order.

    Necessary, not decorative. Pre-market activity drifts upward across the record (14.6/session in
    2025-11 to 23.4 in 2026-08), so a trailing-count feature and today's count both trend and
    correlate for reasons that have nothing to do with regime. A circular shift does not control
    for that — it preserves the trend and only breaks the alignment, which a shared monotone drift
    survives. Removing the trend from both series first is what makes the remaining correlation a
    statement about deviations rather than about the calendar.
    """
    n = len(v)
    r = _rank(v)
    t = np.arange(n, dtype=float)
    t = t - t.mean()
    denom = float((t**2).sum())
    if denom == 0:
        return r
    return r - t * float((t * (r - r.mean())).sum()) / denom


def cmd_scan(args: argparse.Namespace) -> None:
    df, days, joined = _load(args)

    feature_cols = [c for c in joined.columns if any(c.endswith(f"_w{w}") for w in WINDOWS)]
    # `r_per_opp` is 3*p2r - 1 at TARGET_R=2 — rank-identical to p2r, so keeping both would double
    # every one of those hypotheses while adding no information. Dropped from the target grid.
    target_cols = [t for t in STAT_NAMES if t != "r_per_opp"]
    feature_cols = [f for f in feature_cols if not f.startswith("r_per_opp_")]
    print(
        f"sessions: {joined.height}   features: {len(feature_cols)}   targets: {len(target_cols)}"
    )
    print(f"HYPOTHESES TESTED: {len(feature_cols) * len(target_cols)} (per scope)")
    print(f"detrend: {'ON — ranks residualised on calendar order' if args.detrend else 'OFF'}\n")

    scopes = {"recon (discovery)": "recon", "live (holdout)": "live", "all": None}
    results: dict[str, dict[tuple[str, str], tuple[float, float, int]]] = {}
    for label, src in scopes.items():
        sub = joined if src is None else joined.filter(pl.col("source") == src)
        res: dict[tuple[str, str], tuple[float, float, int]] = {}
        for f in feature_cols:
            for t in target_cols:
                pair = sub.select([f, t]).drop_nulls()
                if pair.height < 12:
                    continue
                x = pair[f].to_numpy().astype(float)
                y = pair[t].to_numpy().astype(float)
                if args.detrend:
                    x, y = _detrend_ranks(x), _detrend_ranks(y)
                rho, p = circular_shift_p(x, y)
                res[(f, t)] = (rho, p, pair.height)
        results[label] = res

    # What is predictable AT ALL: the single best feature for each target, which the top-N table
    # below hides whenever one target dominates it.
    print("best feature per target (recon discovery, ranked by |rho|):")
    print(f"{'target':<12} {'best feature':<22} {'recon rho':>10} {'p':>7} {'live rho':>9}")
    print("-" * 64)
    for t in target_cols:
        cands = [(k, v) for k, v in results["recon (discovery)"].items() if k[1] == t]
        if not cands:
            continue
        (bf, _), (rho, p, _n) = max(cands, key=lambda kv: abs(kv[1][0]))
        lv = results["live (holdout)"].get((bf, t))
        lrho = f"{lv[0]:+.3f}" if lv else "   —  "
        print(f"{t:<12} {bf:<22} {rho:>+10.3f} {p:>7.4f} {lrho:>9}")
    print()

    disc = results["recon (discovery)"]
    keys = list(disc)
    adj = bh_fdr([disc[k][1] for k in keys])
    ranked = sorted(zip(keys, adj, strict=True), key=lambda kv: abs(disc[kv[0]][0]), reverse=True)

    print(
        f"{'feature':<22} {'target':<12} {'recon rho':>10} {'p':>7} {'FDR q':>7} "
        f"{'live rho':>9} {'live p':>7} {'n':>4}"
    )
    print("-" * 92)
    for shown, ((f, t), q) in enumerate(ranked, start=1):
        rho, p, n = disc[(f, t)]
        lv = results["live (holdout)"].get((f, t))
        lrho = f"{lv[0]:+.3f}" if lv else "   —  "
        lp = f"{lv[1]:.3f}" if lv else "  —  "
        flag = ""
        if q < 0.10:
            flag = "  <-- survives FDR 10%"
        print(f"{f:<22} {t:<12} {rho:>+10.3f} {p:>7.4f} {q:>7.3f} {lrho:>9} {lp:>7} {n:>4}{flag}")
        if shown >= args.top:
            break

    n_sig = sum(1 for _, q in zip(keys, adj, strict=True) if q < 0.10)
    print(f"\n{n_sig} of {len(keys)} recon hypotheses survive BH FDR at 10%.")
    print(
        f"At q=0.10 with no true effect you would expect ~{0.10 * len(keys):.0f} false positives "
        f"only among the SIGNIFICANT set, and ~{0.05 * len(keys):.0f} raw p<0.05 by chance."
    )

    # Sign agreement across the boundary: the cheap out-of-sample check.
    both = [k for k in keys if k in results["live (holdout)"]]
    agree = sum(
        1
        for k in both
        if not math.isnan(disc[k][0])
        and not math.isnan(results["live (holdout)"][k][0])
        and disc[k][0] * results["live (holdout)"][k][0] > 0
    )
    print(
        f"\nsign agreement recon -> live: {agree}/{len(both)} = {agree / max(1, len(both)):.1%} "
        f"(50% is chance)"
    )


def cmd_terciles(args: argparse.Namespace) -> None:
    df, days, joined = _load(args)
    rng = np.random.default_rng(RNG_SEED)
    scopes = {"recon": "recon", "live": "live", "all": None}
    for label, src in scopes.items():
        sub = joined if src is None else joined.filter(pl.col("source") == src)
        pair = sub.select(["dt", args.feature]).drop_nulls()
        if pair.height < 15:
            print(f"\n[{label}] too few sessions ({pair.height})")
            continue
        vals = pair[args.feature].to_numpy().astype(float)
        lo, hi = np.percentile(vals, [33.33, 66.67])
        print(f"\n[{label}] {args.feature}: n={pair.height} sessions, cuts at {lo:.3f} / {hi:.3f}")
        print(
            f"{'bucket':<10} {'sessions':>8} {'setups':>7} {'P(2R)':>8} {'95% CI':>18} "
            f"{'meanMaxR':>9} {'R/opp@2R':>9}"
        )
        for name, mask in (
            ("low", vals <= lo),
            ("mid", (vals > lo) & (vals <= hi)),
            ("high", vals > hi),
        ):
            dts = [d for d, m in zip(pair["dt"].to_list(), mask, strict=True) if m]
            g = df.filter(pl.col("dt").is_in(dts))
            if g.is_empty():
                continue
            groups = [
                g.filter(pl.col("dt") == d)["max_r"].to_numpy()
                for d in dts
                if g["dt"].is_in([d]).any()
            ]
            groups = [a for a in groups if len(a)]
            mr = g["max_r"].to_numpy()
            p2 = float(np.mean(mr >= 2.0))
            ci = day_block_bootstrap_ci(groups, lambda a: float(np.mean(a >= 2.0)), args.draws, rng)
            realised = np.where(mr >= TARGET_R, TARGET_R, -1.0)
            print(
                f"{name:<10} {len(dts):>8} {len(mr):>7} {p2:>8.4f} "
                f"[{ci[0]:>6.4f},{ci[1]:>6.4f}] {float(np.mean(mr)):>9.3f} "
                f"{float(np.mean(realised)):>+9.3f}"
            )


def cmd_persist(args: argparse.Namespace) -> None:
    """Are there hot and cold PERIODS — and does yesterday carry into today?

    Two tests, because "regime" can mean either and they are not the same claim.

    **Block structure.** A hot/cold period is a *block* effect: a run of contiguous sessions that
    differs from another run. So assign sessions to consecutive blocks of B days and measure the
    share of setup-level outcome variance that sits between blocks. The null is not zero — any
    partition of a finite sample shows some between-block spread — so it is compared against
    blocks assembled from the same sessions in **shuffled order**. Contiguity is the whole
    hypothesis: if calendar-adjacent sessions cluster no more than randomly-drawn ones, there are
    no periods, only sessions. This is the test the day-level ICC in the 2026-08-13 report could
    not perform, because at B=1 the two are the same thing.

    ⚠️ **The shuffled null destroys ordering completely, so a slow global drift also reads as a
    block effect** — and there is drift in this record (monthly P(2R) runs 0.204 in 2026-02 to
    0.337 in 2026-06). So the test is run twice: raw, and on outcomes residualised on a linear
    trend in session index. Only the detrended row is evidence of *periods* as opposed to *drift*.

    **Serial correlation.** Lag-k autocorrelation of each daily statistic, k = 1..5, with the
    circular-shift p-value. Persistence at any lag is what a trailing feature would be reading.

    **Forward horizon.** The decisive question is predictive, not descriptive: 10 blocks differing
    is a fact about the sample, while a *tradeable* regime needs the block you are in to be
    identifiable from the block before it. So for each horizon H, correlate the trailing-H
    statistic (ending yesterday) against the forward-H statistic (starting today) — matched scale,
    strictly causal. Windows overlap, which inflates naive significance; the circular-shift null
    absorbs that because it preserves the target series' own autocorrelation.
    """
    df, days, _ = _load(args)
    rng = np.random.default_rng(RNG_SEED)
    day_of = df["dt"].to_list()
    dts = days["dt"].to_list()
    pos = {d: i for i, d in enumerate(dts)}
    day_idx = np.array([pos[d] for d in day_of])

    def _detrend_on_day(y: np.ndarray) -> np.ndarray:
        t = day_idx.astype(float) - day_idx.mean()
        denom = float((t**2).sum())
        return y if denom == 0 else y - t * float((t * (y - y.mean())).sum()) / denom

    raw = {
        "max R": df["max_r"].to_numpy().astype(float),
        "reaches 2R": (df["max_r"].to_numpy() >= 2.0).astype(float),
    }
    outcomes = dict(raw)
    outcomes.update({f"{k} (detrend)": _detrend_on_day(v) for k, v in raw.items()})

    print(f"\nBLOCK STRUCTURE — {len(dts)} sessions, {df.height} setups")
    print("between-block share of setup-level variance, vs the same blocks shuffled\n")
    print(
        f"{'outcome':<22} {'block':>6} {'blocks':>7} {'observed':>10} {'shuffled mean':>14} "
        f"{'95th pct':>9} {'p':>7}"
    )
    print("-" * 84)
    for label, y in outcomes.items():
        total_ss = float(((y - y.mean()) ** 2).sum())
        for b in (1, 5, 10, 20):
            assign = day_idx // b
            obs = _between_share(y, assign, total_ss)
            null = np.empty(args.draws)
            for k in range(args.draws):
                perm = rng.permutation(len(dts))
                null[k] = _between_share(y, perm[day_idx] // b, total_ss)
            p = float((np.sum(null >= obs) + 1) / (args.draws + 1))
            print(
                f"{label:<22} {b:>6} {len(np.unique(assign)):>7} {obs:>10.4f} "
                f"{float(null.mean()):>14.4f} {float(np.percentile(null, 95)):>9.4f} {p:>7.4f}"
            )

    print("\nFORWARD HORIZON — trailing-H (to yesterday) vs forward-H (from today), pooled")
    print(f"{'statistic':<12}" + "".join(f"{f'H={h}':>18}" for h in (1, 5, 10, 20)))
    print("-" * 84)
    per_day = [
        (
            df.filter(pl.col("dt") == d)["max_r"].to_numpy(),
            df.filter(pl.col("dt") == d)["stopped_out"].to_numpy(),
        )
        for d in dts
    ]
    for stat in ("n_opps", "p2r", "p50_max_r", "mean_max_r", "max_max_r", "total_r", "stop_rate"):
        line = f"{stat:<12}"
        for h in (1, 5, 10, 20):
            back, fwd = [], []
            for i in range(h, len(dts) - h + 1):
                b_mr = np.concatenate([per_day[j][0] for j in range(i - h, i)])
                b_st = np.concatenate([per_day[j][1] for j in range(i - h, i)])
                f_mr = np.concatenate([per_day[j][0] for j in range(i, i + h)])
                f_st = np.concatenate([per_day[j][1] for j in range(i, i + h)])
                back.append(_stats(b_mr, b_st)[stat])
                fwd.append(_stats(f_mr, f_st)[stat])
            rho, p = circular_shift_p(np.array(back), np.array(fwd))
            line += f"{rho:>+11.3f} ({p:.2f})"
        print(line)

    print("\nSERIAL CORRELATION of the daily series (all 197 sessions)")
    print(f"{'statistic':<12}" + "".join(f"{f'lag {k}':>16}" for k in range(1, 6)))
    print("-" * 92)
    for stat in STAT_NAMES:
        if stat == "r_per_opp":
            continue
        v = days[stat].to_numpy().astype(float)
        line = f"{stat:<12}"
        for k in range(1, 6):
            rho, p = circular_shift_p(v[:-k], v[k:])
            line += f"{rho:>+10.3f} ({p:.2f})"
        print(line)


def _between_share(y: np.ndarray, assign: np.ndarray, total_ss: float) -> float:
    """Share of ``y``'s total sum of squares explained by the block means (an eta-squared)."""
    if total_ss <= 0:
        return float("nan")
    grand = y.mean()
    between = 0.0
    for g in np.unique(assign):
        m = assign == g
        between += m.sum() * (y[m].mean() - grand) ** 2
    return float(between / total_ss)


# The morning's own cross-section, read at a cutoff. Each entry is (label, panel column, how to
# aggregate) over the setups that had already TRIGGERED by the cutoff — every one of these is a
# property of a bar at or before that setup's own trigger, so all of it is observable at the cutoff.
ASOF_STATS: tuple[tuple[str, str, str], ...] = (
    ("n_appeared", "first_hit_et_min", "count"),
    ("n_triggered", "trigger_et_min", "count"),
    ("med_ext_trigger", "ext_at_trigger", "median"),
    ("med_runup_pre", "runup_pre_appearance", "median"),
    ("med_rvol_pole", "rvol_pole", "median"),
    ("med_stop_pct", "stop_pct", "median"),
    ("med_price", "entry_fill", "median"),
    ("med_first_rank", "first_rank", "median"),
    ("med_dollar_vol", "cum_dollar_vol_to_trigger", "median"),
    ("med_range_pre_pole", "range_before_pole_pct", "median"),
)


def cmd_asof(args: argparse.Namespace) -> None:
    """A **concurrent** regime: this morning's own cross-section, not last fortnight's outcomes.

    Every regime feature tested by ``scan`` and ``persist`` is built from **outcomes** — Max-R
    percentiles, hit rates, counts of what worked. That is a lagging indicator by construction, and
    it is the likeliest reason the direction reversed across the store boundary: it estimates "was
    the last fortnight good" and hopes that carries.

    This asks the other question. At a cutoff time C, what the morning already looks like is
    observable: how many names have appeared, how many have broken out, how extended they were when
    they did, what their volume was doing. None of it needs an outcome, and none of it needs a
    trailing window — so it sidesteps both the lag and the power problem (a trailing-20 window
    leaves ~10 independent blocks in this record; this uses every session).

    **Causality is the whole design.** The cross-section at C is built only from setups whose
    trigger is at or before C, using features measured at or before their own trigger; the outcome
    is measured only over setups triggering strictly **after** C on the same session. A setup never
    contributes to the regime it is scored against.
    """
    df, _days, _ = _load(args)
    rows = []
    for cutoff in args.cutoffs:
        for d in sorted(df["dt"].unique().to_list()):
            g = df.filter(pl.col("dt") == d)
            before = g.filter(pl.col("trigger_et_min") <= cutoff)
            after = g.filter(pl.col("trigger_et_min") > cutoff)
            if after.height < args.min_after or before.height < args.min_before:
                continue
            rec: dict[str, object] = {"dt": d, "cutoff": cutoff, "n_after": after.height}
            appeared = g.filter(pl.col("first_hit_et_min") <= cutoff)
            for label, col, how in ASOF_STATS:
                src = appeared if label == "n_appeared" else before
                if how == "count":
                    rec[label] = float(src.height)
                else:
                    v = src[col].drop_nulls()
                    rec[label] = float(v.median()) if v.len() else None
            mr = after["max_r"].to_numpy()
            rec["fwd_p2r"] = float(np.mean(mr >= 2.0))
            rec["fwd_mean_max_r"] = float(np.mean(mr))
            rec["fwd_p50_max_r"] = float(np.median(mr))
            rows.append(rec)
    panel = pl.DataFrame(rows, infer_schema_length=None)

    print("\nCONCURRENT REGIME — this morning's cross-section vs the rest of the same morning")
    print("(cross-section from setups triggered by the cutoff; outcome from setups after it)\n")
    for cutoff in args.cutoffs:
        sub = panel.filter(pl.col("cutoff") == cutoff)
        if sub.height < 15:
            print(
                f"cutoff {int(cutoff) // 60:02d}:{int(cutoff) % 60:02d} — only {sub.height} "
                f"sessions qualify, skipped"
            )
            continue
        n_setups = int(sub["n_after"].sum())
        print(
            f"--- cutoff {int(cutoff) // 60:02d}:{int(cutoff) % 60:02d} ET — {sub.height} "
            f"sessions, {n_setups} scored setups ---"
        )
        print(f"{'feature':<20}" + "".join(f"{t:>22}" for t in ("fwd_p2r", "fwd_mean_max_r")))
        for label, _col, _how in ASOF_STATS:
            line = f"{label:<20}"
            for target in ("fwd_p2r", "fwd_mean_max_r"):
                pair = sub.select([label, target]).drop_nulls()
                if pair.height < 12:
                    line += f"{'—':>22}"
                    continue
                x = pair[label].to_numpy().astype(float)
                y = pair[target].to_numpy().astype(float)
                if args.detrend:
                    x, y = _detrend_ranks(x), _detrend_ranks(y)
                rho, p = circular_shift_p(x, y)
                line += f"{rho:>+15.3f} ({p:.2f})"
            print(line)
        print()


# The tape features added in stage 3 — what the name was DOING, as opposed to what the flag looked
# like. Tested at the opportunity level, which is where this record has power: day effects are ~zero
# (see `persist`), so 3,740 setups are near-independent against ~197 sessions or ~10 blocks.
TAPE_FEATURES: dict[str, list[tuple[str, str]]] = {
    "ext_at_trigger": [
        ("<10%", "ext_at_trigger < 0.10"),
        ("10-30%", "0.10 <= ext_at_trigger < 0.30"),
        ("30-75%", "0.30 <= ext_at_trigger < 0.75"),
        (">=75%", "ext_at_trigger >= 0.75"),
    ],
    "runup_pre_appearance": [
        ("<5%", "runup_pre_appearance < 0.05"),
        ("5-25%", "0.05 <= runup_pre_appearance < 0.25"),
        ("25-60%", "0.25 <= runup_pre_appearance < 0.60"),
        (">=60%", "runup_pre_appearance >= 0.60"),
    ],
    "rvol_pole": [
        ("<1x", "rvol_pole < 1.0"),
        ("1-3x", "1.0 <= rvol_pole < 3.0"),
        ("3-10x", "3.0 <= rvol_pole < 10.0"),
        (">=10x", "rvol_pole >= 10.0"),
    ],
    "vol_share_pole": [
        ("<20%", "vol_share_pole < 0.20"),
        ("20-40%", "0.20 <= vol_share_pole < 0.40"),
        ("40-70%", "0.40 <= vol_share_pole < 0.70"),
        (">=70%", "vol_share_pole >= 0.70"),
    ],
    "hits_before_trigger": [
        ("1", "hits_before_trigger <= 1"),
        ("2-4", "1 < hits_before_trigger <= 4"),
        ("5-15", "4 < hits_before_trigger <= 15"),
        (">15", "hits_before_trigger > 15"),
    ],
    "first_rank": [
        ("top 5", "first_rank <= 5"),
        ("6-15", "5 < first_rank <= 15"),
        ("16-30", "15 < first_rank <= 30"),
        (">30", "first_rank > 30"),
    ],
    "bars_before_pole": [
        ("<3", "bars_before_pole < 3"),
        ("3-10", "3 <= bars_before_pole < 10"),
        ("10-25", "10 <= bars_before_pole < 25"),
        (">=25", "bars_before_pole >= 25"),
    ],
    "cum_dollar_vol_to_trigger": [
        ("<$1M", "cum_dollar_vol_to_trigger < 1000000"),
        ("$1-5M", "1000000 <= cum_dollar_vol_to_trigger < 5000000"),
        ("$5-25M", "5000000 <= cum_dollar_vol_to_trigger < 25000000"),
        (">=$25M", "cum_dollar_vol_to_trigger >= 25000000"),
    ],
}


def cmd_tape(args: argparse.Namespace) -> None:
    """Do the stage-3 tape features separate outcomes at the OPPORTUNITY level?

    The regime tests all came back weak, so this asks the question where the sample actually has
    power. Because the day effect is ~zero, setups are near-independent: 3,740 observations against
    the ~10 independent blocks a trailing-20 regime window leaves.

    Reported with day-block bootstrap CIs and **recon and live side by side** — the split that
    caught the trailing-quality reversal, and the only cheap defence against reading a bucket that
    happens to be lucky in one store. The base rate is printed so a bucket can be read as a lift
    rather than as a level.
    """
    df, _days, _ = _load(args)
    rng = np.random.default_rng(RNG_SEED)
    base = float(np.mean(df["max_r"].to_numpy() >= 2.0))
    print(f"\nbase rate P(2R) = {base:.4f} over {df.height} pre-market setups\n")
    for feat, buckets in TAPE_FEATURES.items():
        print(f"--- {feat} ---")
        print(
            f"{'bucket':<10} {'n':>6} {'P(2R)':>8} {'95% CI':>18} {'lift':>7}   "
            f"{'recon':>14} {'live':>14}"
        )
        for lab, spec in buckets:
            b = df.filter(_bucket_expr(spec))
            if b.height < 30:
                print(f"{lab:<10} {b.height:>6}   (too few)")
                continue
            mr = b["max_r"].to_numpy()
            p2 = float(np.mean(mr >= 2.0))
            groups = [
                g["max_r"].to_numpy()
                for _k, g in b.group_by("dt")
                if g.height  # type: ignore[misc]
            ]
            ci = day_block_bootstrap_ci(groups, lambda a: float(np.mean(a >= 2.0)), args.draws, rng)
            cells = []
            for src in ("recon", "live"):
                s = b.filter(pl.col("source") == src)
                cells.append(
                    f"{float(np.mean(s['max_r'].to_numpy() >= 2.0)):.3f} [{s.height}]"
                    if s.height >= 30
                    else "—"
                )
            print(
                f"{lab:<10} {b.height:>6} {p2:>8.4f} [{ci[0]:>6.4f},{ci[1]:>6.4f}] "
                f"{p2 - base:>+7.3f}   {cells[0]:>14} {cells[1]:>14}"
            )
        print()


# The filter features the eventual selection rule would be built from. Bucketed, not thresholded:
# the question is whether the ORDER of the buckets changes with regime, which a threshold hides.
FILTER_FEATURES: dict[str, list[tuple[str, str]]] = {
    "stop_pct": [
        ("<2%", "stop_pct < 0.02"),
        ("2-4%", "0.02 <= stop_pct < 0.04"),
        ("4-8%", "0.04 <= stop_pct < 0.08"),
        (">=8%", "stop_pct >= 0.08"),
    ],
    "entry_fill": [
        ("<$2", "entry_fill < 2"),
        ("$2-5", "2 <= entry_fill < 5"),
        ("$5-15", "5 <= entry_fill < 15"),
        (">=$15", "entry_fill >= 15"),
    ],
    "cons_len": [
        ("1", "cons_len == 1"),
        ("2", "cons_len == 2"),
        ("3", "cons_len == 3"),
        (">=4", "cons_len >= 4"),
    ],
    "pole_len": [("1", "pole_len == 1"), ("2", "pole_len == 2"), (">=3", "pole_len >= 3")],
    "retracement": [
        ("<25%", "retracement < 0.25"),
        ("25-50%", "0.25 <= retracement < 0.5"),
        ("50-100%", "0.5 <= retracement < 1.0"),
        (">=100%", "retracement >= 1.0"),
    ],
    "trigger_et_min": [
        ("pre 07:00", "trigger_et_min < 420"),
        ("07:00-09:15", "420 <= trigger_et_min < 555"),
        ("09:15-11:00", "555 <= trigger_et_min < 660"),
        ("11:00+", "trigger_et_min >= 660"),
    ],
}


def _bucket_expr(spec: str) -> pl.Expr:
    """Tiny predicate parser — the bucket table above is data, not code, on purpose."""
    parts = spec.split()
    if len(parts) == 3:
        col, op, val = parts
        c, v = pl.col(col), float(val)
        return {"<": c < v, "<=": c <= v, ">": c > v, ">=": c >= v, "==": c == v}[op]
    lo, op1, col, op2, hi = parts  # "a <= col < b"
    c = pl.col(col)
    left = c >= float(lo) if op1 == "<=" else c > float(lo)
    right = c < float(hi) if op2 == "<" else c <= float(hi)
    return left & right


def cmd_interact(args: argparse.Namespace) -> None:
    """Does the ordering of a filter feature's buckets change across regime terciles?"""
    df, days, joined = _load(args)
    sub = joined if args.scope == "all" else joined.filter(pl.col("source") == args.scope)
    pair = sub.select(["dt", args.feature]).drop_nulls()
    vals = pair[args.feature].to_numpy().astype(float)
    lo, hi = np.percentile(vals, [33.33, 66.67])
    regimes = {
        "COLD": [d for d, v in zip(pair["dt"].to_list(), vals, strict=True) if v <= lo],
        "MID": [d for d, v in zip(pair["dt"].to_list(), vals, strict=True) if lo < v <= hi],
        "HOT": [d for d, v in zip(pair["dt"].to_list(), vals, strict=True) if v > hi],
    }
    print(f"regime = terciles of {args.feature} (scope={args.scope}), cuts {lo:.3f} / {hi:.3f}")
    print("cell = P(2R) [n].  The question is whether the best bucket MOVES between rows.\n")

    for feat, buckets in FILTER_FEATURES.items():
        print(f"--- {feat} ---")
        header = "".join(f"{lab:>18}" for lab, _ in buckets)
        print(f"{'regime':<8}{header}   best")
        orderings = {}
        for rname, dts in regimes.items():
            g = df.filter(pl.col("dt").is_in(dts))
            cells, line = [], f"{rname:<8}"
            for lab, spec in buckets:
                b = g.filter(_bucket_expr(spec))
                if b.height < 25:
                    cells.append((lab, float("nan"), b.height))
                    line += f"{'—':>18}"
                    continue
                p2 = float(np.mean(b["max_r"].to_numpy() >= 2.0))
                cells.append((lab, p2, b.height))
                line += f"{p2:>11.3f} [{b.height:>4}]"
            valid = [(lab, p) for lab, p, n in cells if not math.isnan(p)]
            best = max(valid, key=lambda kv: kv[1])[0] if valid else "—"
            orderings[rname] = [lab for lab, _ in sorted(valid, key=lambda kv: -kv[1])]
            print(line + f"   {best}")
        # Rank agreement between the extremes: Spearman over the buckets both rows scored.
        common = [b for b in orderings.get("COLD", []) if b in orderings.get("HOT", [])]
        if len(common) >= 3:
            rc = np.array([orderings["COLD"].index(b) for b in common], dtype=float)
            rh = np.array([orderings["HOT"].index(b) for b in common], dtype=float)
            print(
                f"{'':8}rank agreement COLD vs HOT over {len(common)} buckets: "
                f"rho = {spearman(rc, rh):+.2f}"
            )
        print()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan", help="every trailing feature vs every same-day target")
    s.add_argument("--panel", default=str(PANEL_DEFAULT))
    s.add_argument("--top", type=int, default=40)
    s.add_argument(
        "--detrend",
        action="store_true",
        help="residualise both series on calendar order before correlating",
    )
    s.add_argument(
        "--premarket-cut",
        type=float,
        default=555.0,
        help="ET minutes; drop setups whose scanner appearance is later (555 = 09:15)",
    )
    s.set_defaults(func=cmd_scan)

    t = sub.add_parser("terciles", help="pooled setup-level outcome by regime tercile")
    t.add_argument("--panel", default=str(PANEL_DEFAULT))
    t.add_argument("--feature", required=True)
    t.add_argument("--target", default="p2r")
    t.add_argument("--draws", type=int, default=3000)
    t.add_argument("--premarket-cut", type=float, default=555.0)
    t.set_defaults(func=cmd_terciles)

    pe = sub.add_parser(
        "persist", help="are there hot/cold PERIODS, and is the series serially correlated?"
    )
    pe.add_argument("--panel", default=str(PANEL_DEFAULT))
    pe.add_argument("--draws", type=int, default=2000)
    pe.add_argument("--premarket-cut", type=float, default=555.0)
    pe.set_defaults(func=cmd_persist)

    a = sub.add_parser("asof", help="concurrent regime: this morning's cross-section")
    a.add_argument("--panel", default=str(PANEL_DEFAULT))
    a.add_argument("--premarket-cut", type=float, default=555.0)
    a.add_argument("--cutoffs", type=float, nargs="+", default=[390.0, 420.0, 450.0, 480.0])
    a.add_argument("--min-after", type=int, default=3)
    a.add_argument("--min-before", type=int, default=3)
    a.add_argument("--detrend", action="store_true")
    a.set_defaults(func=cmd_asof)

    tp = sub.add_parser("tape", help="do the tape features separate outcomes per opportunity?")
    tp.add_argument("--panel", default=str(PANEL_DEFAULT))
    tp.add_argument("--premarket-cut", type=float, default=555.0)
    tp.add_argument("--draws", type=int, default=3000)
    tp.set_defaults(func=cmd_tape)

    i = sub.add_parser("interact", help="does the best filter bucket move with regime?")
    i.add_argument("--panel", default=str(PANEL_DEFAULT))
    i.add_argument("--feature", required=True)
    i.add_argument("--scope", default="all", choices=["all", "recon", "live"])
    i.add_argument("--premarket-cut", type=float, default=555.0)
    i.set_defaults(func=cmd_interact)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
