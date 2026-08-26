"""Spike #710 (phase 1): cluster trailing session-blocks on TAIL shape, not mean/2R-hit-rate.

#690 asked "is there a day-level regime" by looking at trailing means and P(2R) — found day-level
regime dead, multi-week blocks real but not exploitable via a rank-cap rule. #710 is a narrower
follow-on: the trader's belief is specifically about **+8R runs** — outsized outcomes a fixed 2.0R
target structurally can't capture — alternating with longer cool stretches. Rather than fit a
mean/hit-rate threshold and eyeball where it splits, this defines "hot" directly from the *shape of
the outcome tail* per block, via unsupervised clustering on tail-only features.

## Population: `--population {takeable,passed}`, pre-market appearance always applied

`regime_panel.py`'s panel is deliberately WIDE — every fitted selection rule (price band, trigger
window, min stop, exhaustion cap, staleness) is carried as a column, not applied, so the shipped
book is re-derivable but not assumed. That wideness is right for *measuring which rule matters*; it
is wrong for *reporting a rate* on an untradeable population.

Two population definitions are supported, both filtered to `first_hit_et_min < 555` (09:15 ET) —
the no-lookahead, pre-market-only constraint #690 established, not a fitted rule:

- `--population takeable` (default, backward-compatible): the shipped `takeable` verdict re-derived
  from the panel's own columns (the same expression `regime_panel._shipped_takeable` uses for its
  `verify` command) — shape gates *and* today's fitted selection rules (price band, trigger window,
  min stop %, exhaustion cap, staleness).
- `--population passed`: shape gates only (`setup.passed` — pole/consolidation/retracement/
  cons_vol_reducing etc.) plus `triggered` (so `entry_fill`/`max_r` are defined) — no price band,
  window, min stop %, exhaustion cap, or staleness filtering. This is the "all bull flags"
  population; #710's methodology correction flagged that clustering on `takeable` alone pre-filters
  through rules that were themselves fitted on a limited sample, which could hide a regime signal
  that should instead change one of those rules.

## Blocks: non-overlapping, ~20 sessions, across the FULL session calendar

Blocks are built over the panel's full 197-session index (recon 166 + live 31, one continuous
calendar per the panel's own stitching), not over the 94 sessions that happen to contain a
takeable row — a block with zero takeable setups is a legitimate (if uninformative) cold reading,
not a missing one. Non-overlapping, not rolling: rolling windows on ~10 blocks worth of session
count would make adjacent blocks share 19 of 20 sessions, which would make "cluster separation"
almost entirely an artifact of autocorrelation rather than a statement about distinct periods. 197
/ 20 leaves a
final partial block of 17 sessions — kept, flagged in the output rather than dropped, since dropping
real sessions to force equal block sizes would itself be a choice with no principled tie-break.

## Detrending

Per #690's rule (`research/how-we-work.md` and the panel docstring): pre-market activity drifts
upward across the whole record, so any block-level feature whose scale depends on activity volume
(setup counts, sums) will show a trend for reasons that have nothing to do with "hotness". Before
clustering, every block feature is rank-residualised against block order using the same
`_detrend_ranks` construction `regime_scan.py` uses for session-level features (imported, not
copied) — only the trend-adjusted feature is clustered on; the raw values are also reported so the
detrending's effect is visible.

## No sklearn in .venv — minimal from-scratch k-means + silhouette

`.venv` has no sklearn. K-means (multiple random restarts, deterministic seed) and silhouette score
are both reimplemented here in pure numpy, at the scale of ~10 points this is exact enough to trust.

    python spikes/regime_tail_cluster.py --panel data/spikes/regime_panel.parquet \\
        --block-size 20 --tail-threshold 8 --out data/spikes/regime_tail_clusters.json

`--tail-threshold` sweeps the max_r cutoff that defines a "tail" event for the clustering
features (rate_ge{T}/count_ge{T}); default 8, the trader's original belief. The +8R
corroboration count (how many actual >=8R events land in each threshold's hot vs cold split)
is always reported regardless of the swept threshold, since only 7-8 such events exist in the
whole record and any threshold's split should be checked against them.

## `null` — does the hot/cold split beat a random one?

Silhouette and a hot/cold rate contrast are descriptive: k=2 k-means will always partition 21
points into two groups and report *some* separation, even on pure noise, because that is what
it is built to do. The `null` subcommand asks the sharper question: is the OBSERVED hot/cold
gap bigger than the gap k-means finds on the same pipeline run over randomly reshuffled blocks?

Session-level max_r arrays (one per calendar session, built once from the population + pre-market
filter) are the unit of shuffling. Each trial draws a random permutation of session order, then
re-chunks that permuted order into the SAME fixed block sizes used for the real run (last block
the same remainder size) — this holds block *sizes* fixed and destroys only which sessions land
in which block, i.e. it destroys real time structure while preserving each session's own setups
and outcomes exactly (no setup is invented, dropped, or reassigned to a different session). Block
features are rank-detrended against block order and clustered with the identical k=2 pipeline
(same standardize + k-means + hot-is-higher-mean-rate rule) as the real run, and the same summary
statistic — hot rate minus cold rate, weighted by setups per block — is recorded. The empirical
p-value is the fraction of trials whose null contrast is >= the observed contrast. Reporting the
null distribution's mean/median alongside the observed value (not just the p-value) is the direct
check for the "k-means always finds a split" failure mode: if the null's typical contrast is
itself large, the real split is not distinguishable from noise even if p looks small by other
measures.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import polars as pl

sys.path.insert(0, str(Path(__file__).parent))
from regime_panel import _shipped_takeable  # noqa: E402
from regime_scan import _detrend_ranks  # noqa: E402

from small_cap_stack.config import Settings  # noqa: E402

# Tail threshold is swept via --tail-threshold (default 8, the trader's original +8R belief).
# The corroboration threshold (actual +8R events) is always 8 regardless of the clustering
# threshold, so it can be cross-tabbed against whatever split the swept threshold produces.
CORROBORATION_THRESHOLD = 8


def feature_names(threshold: int) -> list[str]:
    return [f"rate_ge{threshold}", f"count_ge{threshold}", "sum_max_r", "p75_max_r", "max_max_r"]


def _population_expr(population: str, df: pl.DataFrame, base: Settings) -> pl.Expr:
    """The population-selection verdict, named so both the ``passed`` and shipped-``takeable``
    populations run through the same code path.

    ``takeable`` = today's shipped, fitted selection rules (price band, trigger window, min stop
    %, exhaustion cap, staleness) applied on top of the shape gates. ``passed`` = shape gates only
    (`setup.passed`, i.e. pole/consolidation/retracement/cons_vol_reducing etc.) plus ``triggered``
    so ``entry_fill``/``max_r`` are actually defined for the setup — no price band, window, min
    stop %, exhaustion cap, or staleness filtering, since those are fitted selection rules, not
    shape gates (see #710's methodology correction).
    """
    if population == "takeable":
        return _shipped_takeable(df, base)
    if population == "passed":
        return pl.col("triggered") & pl.col("passed")
    raise ValueError(f"unknown --population {population!r}")


def load_takeable(
    panel_path: Path, population: str = "takeable"
) -> tuple[pl.DataFrame, list, dict]:
    """Full panel (for the session calendar) and the population rows to cluster on."""
    df = pl.read_parquet(panel_path)
    base = Settings()
    tk = df.with_columns(_population_expr(population, df, base).alias("takeable"))
    sessions = sorted(df["dt"].unique().to_list())
    sidx = {d: i for i, d in enumerate(sessions)}
    sub = tk.filter(pl.col("takeable"))
    pm = sub.filter(pl.col("first_hit_et_min") < 555)
    if pm.height != sub.height:
        print(
            f"note: {sub.height - pm.height} {population} row(s) had appearance >= 09:15 ET "
            "and were dropped by the pre-market filter",
            file=sys.stderr,
        )
    return df, sessions, sidx


def build_blocks(
    df: pl.DataFrame,
    sub: pl.DataFrame,
    sessions: list,
    sidx: dict,
    block_size: int,
    threshold: int,
) -> pl.DataFrame:
    n_blocks = -(-len(sessions) // block_size)  # ceil
    block_bounds = []
    for b in range(n_blocks):
        lo = b * block_size
        hi = min(lo + block_size, len(sessions))
        block_bounds.append((b, sessions[lo], sessions[hi - 1], hi - lo))

    sub = sub.with_columns(
        pl.col("dt").replace_strict(sidx, return_dtype=pl.Int64).floordiv(block_size).alias("block")
    )
    ct_col = f"count_ge{threshold}"
    rt_col = f"rate_ge{threshold}"
    agg_exprs = [
        pl.len().alias("n_setups"),
        (pl.col("max_r") >= threshold).sum().alias(ct_col),
        pl.col("max_r").sum().alias("sum_max_r"),
        pl.col("max_r").quantile(0.75, interpolation="linear").alias("p75_max_r"),
        pl.col("max_r").max().alias("max_max_r"),
    ]
    # Always carry the fixed +8R corroboration count too, regardless of swept threshold.
    if threshold != CORROBORATION_THRESHOLD:
        agg_exprs.append(
            (pl.col("max_r") >= CORROBORATION_THRESHOLD).sum().alias("count_ge8_corrob")
        )
    g = sub.group_by("block").agg(agg_exprs)
    g = g.with_columns((pl.col(ct_col) / pl.col("n_setups")).alias(rt_col))
    if threshold == CORROBORATION_THRESHOLD:
        g = g.with_columns(pl.col(ct_col).alias("count_ge8_corrob"))

    rows = []
    for b, start, end, n_sessions in block_bounds:
        bg = g.filter(pl.col("block") == b)
        if bg.is_empty():
            rows.append(
                {
                    "block": b,
                    "session_start": start,
                    "session_end": end,
                    "n_sessions": n_sessions,
                    "n_setups": 0,
                    ct_col: 0,
                    rt_col: None,
                    "sum_max_r": 0.0,
                    "p75_max_r": None,
                    "max_max_r": None,
                    "count_ge8_corrob": 0,
                }
            )
        else:
            r = bg.row(0, named=True)
            rows.append(
                {
                    "block": b,
                    "session_start": start,
                    "session_end": end,
                    "n_sessions": n_sessions,
                    "n_setups": r["n_setups"],
                    ct_col: r[ct_col],
                    rt_col: r[rt_col],
                    "sum_max_r": r["sum_max_r"],
                    "p75_max_r": r["p75_max_r"],
                    "max_max_r": r["max_max_r"],
                    "count_ge8_corrob": r["count_ge8_corrob"],
                }
            )
    return pl.DataFrame(rows).sort("block")


def detrend_features(blocks: pl.DataFrame, threshold: int) -> pl.DataFrame:
    out = blocks.clone()
    for f in feature_names(threshold):
        v = blocks[f].to_numpy().astype(float)
        out = out.with_columns(pl.Series(f"{f}_detrended", _detrend_ranks(v)))
    return out


def _kmeans(x: np.ndarray, k: int, *, seed: int = 0, n_init: int = 50) -> tuple[np.ndarray, float]:
    """Minimal k-means (Lloyd's algorithm), best of ``n_init`` random restarts by inertia."""
    rng = np.random.default_rng(seed)
    n = x.shape[0]
    best_labels: np.ndarray | None = None
    best_inertia = np.inf
    for _ in range(n_init):
        centers = x[rng.choice(n, size=k, replace=False)]
        labels = np.zeros(n, dtype=int)
        for _ in range(100):
            d = ((x[:, None, :] - centers[None, :, :]) ** 2).sum(axis=2)
            new_labels = d.argmin(axis=1)
            if np.array_equal(new_labels, labels) and _ > 0:
                labels = new_labels
                break
            labels = new_labels
            for c in range(k):
                if (labels == c).any():
                    centers[c] = x[labels == c].mean(axis=0)
        d = ((x[:, None, :] - centers[None, :, :]) ** 2).sum(axis=2)
        inertia = d[np.arange(n), labels].sum()
        if inertia < best_inertia and len(set(labels.tolist())) == k:
            best_inertia = inertia
            best_labels = labels
    if best_labels is None:
        best_labels = np.zeros(n, dtype=int)
    return best_labels, float(best_inertia)


def _silhouette(x: np.ndarray, labels: np.ndarray) -> float:
    n = x.shape[0]
    if len(set(labels.tolist())) < 2:
        return float("nan")
    dist = np.sqrt(((x[:, None, :] - x[None, :, :]) ** 2).sum(axis=2))
    scores = []
    for i in range(n):
        same = (labels == labels[i]) & (np.arange(n) != i)
        a = dist[i, same].mean() if same.any() else 0.0
        others = set(labels.tolist()) - {labels[i]}
        b = min(dist[i, labels == c].mean() for c in others)
        scores.append(0.0 if max(a, b) == 0 else (b - a) / max(a, b))
    return float(np.mean(scores))


def cluster(
    blocks: pl.DataFrame, k: int, threshold: int, *, seed: int = 0
) -> tuple[np.ndarray, float, float]:
    cols = [f"{f}_detrended" for f in feature_names(threshold)]
    x = blocks.select(cols).to_numpy().astype(float)
    mu, sigma = x.mean(axis=0), x.std(axis=0)
    sigma[sigma == 0] = 1.0
    xs = (x - mu) / sigma
    labels, inertia = _kmeans(xs, k, seed=seed)
    sil = _silhouette(xs, labels)
    return labels, inertia, sil


def cmd_run(args: argparse.Namespace) -> None:
    panel_path = Path(args.panel)
    threshold = args.tail_threshold
    population = args.population
    df, sessions, sidx = load_takeable(panel_path, population)
    base = Settings()
    tk = df.with_columns(_population_expr(population, df, base).alias("takeable"))
    sub = tk.filter(pl.col("takeable") & (pl.col("first_hit_et_min") < 555))

    print(
        f"panel: {df.height} wide rows, {df['dt'].n_unique()} sessions "
        f"({sessions[0]} .. {sessions[-1]})"
    )
    print(
        f"population={population!r} + pre-market-appearance population: {sub.height} setups "
        f"over {sub['dt'].n_unique()} sessions"
    )
    print(
        f"  tail threshold swept: max_r >= {threshold}: "
        f"{int((sub['max_r'] >= threshold).sum())} "
        f"({(sub['max_r'] >= threshold).mean():.4f} rate)"
    )
    print(
        f"  corroboration threshold (fixed): max_r >= {CORROBORATION_THRESHOLD}: "
        f"{int((sub['max_r'] >= CORROBORATION_THRESHOLD).sum())} "
        f"({(sub['max_r'] >= CORROBORATION_THRESHOLD).mean():.4f} rate)\n"
    )

    ct_col = f"count_ge{threshold}"
    rt_col = f"rate_ge{threshold}"

    blocks = build_blocks(df, sub, sessions, sidx, args.block_size, threshold)
    blocks = detrend_features(blocks, threshold)
    print(
        f"blocks: {blocks.height} (non-overlapping, {args.block_size} sessions each, "
        f"last block may be shorter)"
    )
    with pl.Config(tbl_cols=-1, tbl_width_chars=200):
        print(
            blocks.select(
                "block",
                "session_start",
                "session_end",
                "n_sessions",
                "n_setups",
                ct_col,
                rt_col,
                "sum_max_r",
                "p75_max_r",
                "max_max_r",
                "count_ge8_corrob",
            )
        )
    print()

    results = {}
    for k in (2, 3):
        labels, inertia, sil = cluster(blocks, k, threshold, seed=args.seed)
        blocks = blocks.with_columns(pl.Series(f"cluster_k{k}", labels))
        print(f"k={k}: inertia={inertia:.3f}  silhouette={sil:.4f}")
        for c in sorted(set(labels.tolist())):
            bs = blocks.filter(pl.col(f"cluster_k{k}") == c)
            print(
                f"  cluster {c}: {bs.height} block(s), blocks={bs['block'].to_list()}, "
                f"mean {rt_col}={bs[rt_col].mean():.4f}, "
                f"mean sum_max_r={bs['sum_max_r'].mean():.3f}"
            )
        results[f"k{k}"] = {
            "silhouette": sil,
            "inertia": inertia,
            "labels": {
                int(b): int(c)
                for b, c in zip(blocks["block"].to_list(), labels.tolist(), strict=True)
            },
        }
    print()

    # Primary split: k=2, "hot" = the cluster with the higher mean rate_ge{threshold}.
    labels2 = np.array([results["k2"]["labels"][b] for b in blocks["block"].to_list()])
    c0_rate = blocks.filter(pl.Series(labels2 == 0))[rt_col].mean() or 0.0
    c1_rate = blocks.filter(pl.Series(labels2 == 1))[rt_col].mean() or 0.0
    hot_label = 0 if c0_rate >= c1_rate else 1
    hot = blocks.filter(pl.Series(labels2 == hot_label))
    cold = blocks.filter(pl.Series(labels2 != hot_label))

    def _agg(bs: pl.DataFrame) -> dict:
        return {
            "n_blocks": bs.height,
            "n_setups": int(bs["n_setups"].sum()),
            f"ge{threshold}_hits": int(bs[ct_col].sum()),
            "rate": ((bs[ct_col].sum() / bs["n_setups"].sum()) if bs["n_setups"].sum() else None),
        }

    hot_agg, cold_agg = _agg(hot), _agg(cold)
    print(f"HEADLINE (k=2, threshold=+{threshold}R, hot = higher mean {rt_col} cluster):")
    print(
        f"  hot:  blocks={hot['block'].to_list()}  n_setups={hot_agg['n_setups']}  "
        f"+{threshold}R hits={hot_agg[f'ge{threshold}_hits']}  rate={hot_agg['rate']:.4f}"
        if hot_agg["rate"] is not None
        else "  hot: no setups"
    )
    print(
        f"  cold: blocks={cold['block'].to_list()}  n_setups={cold_agg['n_setups']}  "
        f"+{threshold}R hits={cold_agg[f'ge{threshold}_hits']}  rate={cold_agg['rate']:.4f}"
        if cold_agg["rate"] is not None
        else "  cold: no setups"
    )
    print("\n  hot block date ranges:")
    for r in hot.iter_rows(named=True):
        print(
            f"    block {r['block']}: {r['session_start']} .. {r['session_end']} "
            f"({r['n_sessions']} sessions)"
        )

    # Corroboration: how do the fixed +8R events distribute across THIS split's hot/cold blocks?
    hot_ge8 = int(hot["count_ge8_corrob"].sum())
    cold_ge8 = int(cold["count_ge8_corrob"].sum())
    total_ge8 = hot_ge8 + cold_ge8
    print(
        f"\n  CORROBORATION: of {total_ge8} actual +{CORROBORATION_THRESHOLD}R events, "
        f"{hot_ge8} fall in hot blocks, {cold_ge8} fall in cold blocks "
        f"({hot_ge8 / total_ge8:.4f} share hot)"
        if total_ge8
        else "\n  CORROBORATION: no +8R events in this population"
    )

    out = {
        "panel": str(panel_path),
        "population_kind": population,
        "n_sessions": df["dt"].n_unique(),
        "session_range": [str(sessions[0]), str(sessions[-1])],
        "tail_threshold": threshold,
        "corroboration_threshold": CORROBORATION_THRESHOLD,
        "population": {
            "n_setups": sub.height,
            "n_sessions_with_setups": sub["dt"].n_unique(),
            f"ge{threshold}_hits": int((sub["max_r"] >= threshold).sum()),
            "ge8_hits": int((sub["max_r"] >= CORROBORATION_THRESHOLD).sum()),
        },
        "block_size": args.block_size,
        "blocks": blocks.to_dicts(),
        "clusters": results,
        "headline": {
            "hot": hot_agg | {"blocks": hot["block"].to_list()},
            "cold": cold_agg | {"blocks": cold["block"].to_list()},
        },
        "corroboration": {
            "hot_ge8_hits": hot_ge8,
            "cold_ge8_hits": cold_ge8,
            "total_ge8_hits": total_ge8,
        },
    }
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2, default=str))
    print(f"\nwrote {out_path}", file=sys.stderr)


def _session_max_r(sub: pl.DataFrame, sessions: list) -> list[np.ndarray]:
    """Per full-session (chronological, all `sessions`) array of this population's ``max_r``
    values — the unit the null permutation shuffles. A session with no population rows gets an
    empty array, matching the "legitimate cold reading" treatment ``build_blocks`` gives it."""
    grouped = sub.group_by("dt").agg(pl.col("max_r"))
    by_dt = dict(zip(grouped["dt"].to_list(), grouped["max_r"].to_list(), strict=True))
    return [np.array(by_dt.get(d, []), dtype=float) for d in sessions]


def _block_features(
    session_max_r: list[np.ndarray], order: np.ndarray, block_size: int, threshold: int
) -> tuple[np.ndarray, np.ndarray]:
    """Aggregate ``session_max_r`` into blocks by chunking ``order`` (a permutation of session
    indices, or ``arange`` for the real chronological assignment) into the same fixed block sizes
    ``build_blocks`` uses. Returns ``(n_setups, features)`` where features columns are
    ``[rate_ge{T}, count_ge{T}, sum_max_r, p75_max_r, max_max_r]`` — the same 5 features
    ``feature_names`` clusters on, in that order."""
    n_sessions = len(order)
    n_blocks = -(-n_sessions // block_size)
    n_setups = np.zeros(n_blocks)
    feats = np.zeros((n_blocks, 5))
    for b in range(n_blocks):
        lo, hi = b * block_size, min((b + 1) * block_size, n_sessions)
        idxs = order[lo:hi]
        mr = (
            np.concatenate([session_max_r[i] for i in idxs])
            if len(idxs)
            else np.array([], dtype=float)
        )
        n = len(mr)
        n_setups[b] = n
        if n == 0:
            feats[b] = [0.0, 0.0, 0.0, 0.0, 0.0]
            continue
        cnt = float((mr >= threshold).sum())
        feats[b] = [cnt / n, cnt, float(mr.sum()), float(np.quantile(mr, 0.75)), float(mr.max())]
    return n_setups, feats


def _hot_cold_contrast(n_setups: np.ndarray, feats: np.ndarray, *, seed: int) -> float:
    """Run the SAME pipeline the headline uses (rank-detrend each feature against block order,
    standardize, k=2 k-means, hot = the cluster with the higher mean raw rate_ge{T}) and return
    the weighted hot-rate minus cold-rate contrast — the one summary statistic the observed table
    and the null distribution are both built from."""
    detrended = np.column_stack([_detrend_ranks(feats[:, j]) for j in range(feats.shape[1])])
    mu, sigma = detrended.mean(axis=0), detrended.std(axis=0)
    sigma[sigma == 0] = 1.0
    xs = (detrended - mu) / sigma
    labels, _ = _kmeans(xs, 2, seed=seed)
    labels = np.asarray(labels)
    rate = feats[:, 0]
    m0 = rate[labels == 0].mean() if (labels == 0).any() else -np.inf
    m1 = rate[labels == 1].mean() if (labels == 1).any() else -np.inf
    hot_label = 0 if m0 >= m1 else 1
    hot, cold = labels == hot_label, labels != hot_label
    hot_setups, cold_setups = n_setups[hot].sum(), n_setups[cold].sum()
    hot_hits, cold_hits = feats[hot, 1].sum(), feats[cold, 1].sum()
    hot_rate = hot_hits / hot_setups if hot_setups > 0 else 0.0
    cold_rate = cold_hits / cold_setups if cold_setups > 0 else 0.0
    return float(hot_rate - cold_rate)


def cmd_null(args: argparse.Namespace) -> None:
    panel_path = Path(args.panel)
    population = args.population
    df, sessions, sidx = load_takeable(panel_path, population)
    base = Settings()
    tk = df.with_columns(_population_expr(population, df, base).alias("takeable"))
    sub = tk.filter(pl.col("takeable") & (pl.col("first_hit_et_min") < 555))
    n_sessions = len(sessions)

    print(f"panel: {df.height} wide rows, {n_sessions} sessions ({sessions[0]} .. {sessions[-1]})")
    print(
        f"population={population!r} + pre-market-appearance population: {sub.height} setups "
        f"over {sub['dt'].n_unique()} sessions"
    )
    print(
        f"null: {args.null_trials} permutation trials, block-size={args.block_size}, "
        f"thresholds={args.thresholds}\n"
    )

    session_max_r = _session_max_r(sub, sessions)
    identity = np.arange(n_sessions)
    rng = np.random.default_rng(args.seed)

    results = {}
    print(
        f"{'threshold':>9} {'observed':>10} {'null mean':>10} {'null median':>12} "
        f"{'null p95':>9} {'p-value':>9}"
    )
    print("-" * 64)
    for threshold in args.thresholds:
        n_setups, feats = _block_features(session_max_r, identity, args.block_size, threshold)
        observed = _hot_cold_contrast(n_setups, feats, seed=args.seed)

        null = np.empty(args.null_trials)
        for trial in range(args.null_trials):
            order = rng.permutation(n_sessions)
            n_p, feats_p = _block_features(session_max_r, order, args.block_size, threshold)
            null[trial] = _hot_cold_contrast(n_p, feats_p, seed=args.seed)

        p = float((np.sum(null >= observed) + 1) / (args.null_trials + 1))
        print(
            f"{'>=' + str(threshold) + 'R':>9} {observed:>10.4f} {float(null.mean()):>10.4f} "
            f"{float(np.median(null)):>12.4f} {float(np.percentile(null, 95)):>9.4f} {p:>9.4f}"
        )
        results[f"t{threshold}"] = {
            "observed_contrast": observed,
            "null_mean": float(null.mean()),
            "null_median": float(np.median(null)),
            "null_p95": float(np.percentile(null, 95)),
            "null_std": float(null.std()),
            "p_value": p,
            "n_trials": args.null_trials,
        }

    print(
        "\ncontrast = hot rate minus cold rate (weighted by setups per block), same k=2 pipeline "
        "as the headline run. p-value = fraction of permutation trials with null contrast >= "
        "observed. If null mean/median sits close to observed, k-means is finding a comparably "
        "large split on shuffled data too, which is the 'k-means always separates something' "
        "failure mode this test is checking for."
    )

    out = {
        "panel": str(panel_path),
        "population_kind": population,
        "n_sessions": n_sessions,
        "session_range": [str(sessions[0]), str(sessions[-1])],
        "block_size": args.block_size,
        "null_trials": args.null_trials,
        "seed": args.seed,
        "population_setups": sub.height,
        "results": results,
    }
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2, default=str))
    print(f"\nwrote {out_path}", file=sys.stderr)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--panel", default="data/spikes/regime_panel.parquet")
    ap.add_argument("--block-size", type=int, default=20)
    ap.add_argument(
        "--tail-threshold",
        type=int,
        default=8,
        help="max_r threshold defining a 'tail' event for the clustering features "
        "(rate_ge{T}/count_ge{T}). The +8R corroboration count is always reported "
        "regardless of this value.",
    )
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument(
        "--population",
        choices=["takeable", "passed"],
        default="takeable",
        help="'takeable' (default, backward-compatible) = shipped fitted selection rules on top "
        "of shape gates. 'passed' = shape gates only (setup.passed) + triggered (so entry_fill/"
        "max_r are defined) — no price band, trigger window, min stop %%, exhaustion cap, or "
        "staleness filtering.",
    )
    ap.add_argument("--out", default="data/spikes/regime_tail_clusters.json")
    ap.add_argument(
        "--null-trials",
        type=int,
        default=0,
        help="if > 0, run the permutation null test instead of the normal cluster+headline "
        "output: shuffle session-to-block assignment (holding block sizes fixed) N times, rerun "
        "the same detrend+k-means pipeline on each shuffle, and report an empirical p-value for "
        "each of --null-thresholds. --tail-threshold is ignored in this mode.",
    )
    ap.add_argument(
        "--null-thresholds",
        default="2,4,8",
        help="comma-separated max_r thresholds to null-test (only used with --null-trials > 0).",
    )
    args = ap.parse_args()
    if args.null_trials > 0:
        args.thresholds = [int(t) for t in args.null_thresholds.split(",")]
        args.func = cmd_null
    else:
        args.func = cmd_run
    args.func(args)


if __name__ == "__main__":
    main()
