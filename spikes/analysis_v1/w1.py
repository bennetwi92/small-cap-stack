"""Analysis v1 — workstream W1 (#737): selection × exit on the frozen panel.

Plan: `research/analysis-plan-1-selection.md`. Shared contract: `research/analysis-protocol.md`.
Machinery: `stage0.py` — imported, never forked (panel-v1-spec §1).

    .venv/bin/python spikes/analysis_v1/w1.py stage0     # W1-0a … W1-0e — free, no outcome read

Reads ONLY the published, redacted artefacts under `data/spikes/panel-v1/publish/`, after checking
their sha256 against `research/panel-v1.sha256`. Never the custodian copy, never `panel-v1-full`.
"""

from __future__ import annotations

import json
import sys
from itertools import combinations
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import stage0  # noqa: E402

OUT = stage0.OUT / "w1"
SPEC_SHA = stage0.REPO / "research/panel-v1.sha256"
PUBLISHED = {
    "panel-v1.parquet": stage0.PUBLISH / "panel-v1.parquet",
    "paths-v1/paths-v1.parquet": stage0.PUBLISH / "paths-v1" / "paths-v1.parquet",
    "sessions-v1.parquet": stage0.PUBLISH / "sessions-v1.parquet",
}
#: panel-v1-spec §3 — the realised split arithmetic W1-0a checks against.
SPEC_ROWS = {
    ("FIT", "recon"): 4516,
    ("CHECK", "recon"): 2224,
    ("HOLDOUT", "recon"): 1300,
    ("HOLDOUT", "live"): 1155,
}
SPEC_SESSIONS = {"FIT": 266, "CHECK": 125, "HOLDOUT": 117}
GRID = (0.3, 0.5, 0.7)

# ---------------------------------------------------------------------------------------------
# W1-0b — every frozen RULE_COLUMN assigned to one selection family, or explicitly unassigned.
# Assigned from what a column MEANS (inventory §2–§3, raw-capture code), before any outcome read.
# ---------------------------------------------------------------------------------------------
FAMILY_OF: dict[str, str] = {
    # S1 — attention: the scanner-attention series, how much and since when
    "hits_before_trigger": "S1",
    "staleness_delay_min": "S1",
    "first_hit_et_min": "S1",
    "runup_pre_appearance": "S1",
    # S2 — participation: trigger-safe liquidity
    "cum_volume_pre_trigger": "S2",
    "cum_dollar_vol_pre_trigger": "S2",
    "pole_volume": "S2",
    "rvol_pole": "S2",
    # S3 — shape: the flag grammar's SHAPE / VOL / WICK / CONS quantities and its verdict
    "retracement": "S3",
    "cons_len": "S3",
    "pole_len": "S3",
    "cons_vol_reducing": "S3",
    "vol_share_pole_pre_trigger": "S3",
    "pole_has_big_green": "S3",
    "bars_before_pole": "S3",
    "range_before_pole_pct": "S3",
    "untraded_cons_bars": "S3",
    "halted_consolidation": "S3",
    "passed": "S3",
    # S4 — geometry and cost: stop distance, extension, pole height, price level, time of day
    "stop_pct": "S4",
    "ext_at_trigger": "S4",
    "ext_at_peak": "S4",
    "pole_pct": "S4",
    "planned_risk": "S4",
    "trigger_et_min": "S4",
    "entry_fill": "S4",
    "entry_trigger": "S4",
    "breakout_level": "S4",
    "stop": "S4",
    "day_open": "S4",
}
#: Unassigned and unusable (plan §3 W1-0b): may not be pulled into a family later.
UNASSIGNED: dict[str, str] = {
    "run": "segmentation bookkeeping (which run of the day), not a property of the setup",
    "cycle_num": "segmentation bookkeeping (which cycle of the run)",
    "trigger_idx": "bar index of the trigger — a restatement of trigger_et_min in bars",
    "cons_has_range": "constant True on every row (the build's cut, panel-v1-spec §2)",
    "failing_gates": "string list of gate names; not thresholdable",
}


#: W1-0e drops (|rho| > 0.9 on FIT). Coverage ties everywhere (all 0 % null), so the tie-break is
#: semantic and ledgered: the price-level cluster keeps `entry_fill` (the price sizing and the
#: tick-slippage cost term use); `first_hit_et_min` is exactly `trigger_et_min -
#: staleness_delay_min` on 100 % of FIT rows, so it carries nothing the other two don't.
CORR_DROPS = {
    "entry_trigger": "rho 1.000 with entry_fill",
    "breakout_level": "rho 1.000 with entry_fill",
    "stop": "rho 0.997 with entry_fill",
    "day_open": "rho 0.964 with entry_fill",
    "first_hit_et_min": "rho 0.969 with trigger_et_min; = trigger_et_min - staleness_delay_min",
}

# ---------------------------------------------------------------------------------------------
# Stage 1 — the pre-registered predicate per family: ONE column, a direction fixed a priori, and
# three grid levels. Level L keeps ~70 % of FIT rows, M ~50 %, T ~30 % (plan §5.1; protocol §5.3).
# ---------------------------------------------------------------------------------------------
PRED: dict[str, tuple[str, str]] = {
    "S1": ("hits_before_trigger", ">="),  # more scanner attention before the break
    "S2": ("cum_dollar_vol_pre_trigger", ">="),  # more participation before the break
    "S3": ("retracement", "<="),  # a shallower consolidation relative to the pole
    "S4": ("stop_pct", ">="),  # a wider percentage stop: the §11.1 cost lever
}
LEVEL_Q = {">=": {"L": 0.3, "M": 0.5, "T": 0.7}, "<=": {"L": 0.7, "M": 0.5, "T": 0.3}}
LEVELS = ("L", "M", "T")
EXITS = ("F1", "F2", "F3", "F4")
TP_BAND = (0.6, 1.0)


def threshold(rows: pl.DataFrame, fam: str, lvl: str) -> float:
    col, op = PRED[fam]
    return float(rows[col].quantile(LEVEL_Q[op][lvl], interpolation="linear"))


def predicate(thr: dict[str, float]) -> Any:
    """A conjunction of the named families' one-column predicates at literal thresholds. It is
    handed `df.select(RULE_COLUMNS)` by `stage0.run_rule`, so it cannot read anything else."""

    def pred(x: pl.DataFrame) -> pl.Series:
        m = pl.Series([True] * x.height)
        for fam, t in thr.items():
            col, op = PRED[fam]
            m = m & ((x[col] >= t) if op == ">=" else (x[col] <= t)).fill_null(False)
        return m

    return pred


def select(df: pl.DataFrame, thr: dict[str, float]) -> pl.DataFrame:
    """§5.1 layers 1 + 3: column-subsetted predicate, then the earliest trigger per session."""
    return earliest_one(stage0.run_rule(df, predicate(thr)))


def _verify() -> dict[str, Any]:
    want = {}
    for line in SPEC_SHA.read_text().splitlines():
        h, name = line.split()
        want[name] = h
    got = {name: stage0.sha256(p) for name, p in PUBLISHED.items()}
    bad = {n: (got[n], want.get(n)) for n in got if got[n] != want.get(n)}
    if bad:
        raise SystemExit(f"STOP-AND-REPORT: sha256 mismatch vs research/panel-v1.sha256: {bad}")
    return got


def _usable(s: pl.Series) -> bool:
    """A column a quantile grid means something on: numeric, ≤20 % null, q30 < q50 < q70 on FIT."""
    if not s.dtype.is_numeric() or s.null_count() / len(s) > 0.20:
        return False
    q = [s.quantile(p, interpolation="linear") for p in GRID]
    return bool(q[0] < q[1] < q[2])


def earliest_one(d: pl.DataFrame) -> pl.DataFrame:
    """Capacity N = 1: the earliest trigger per session (ties: symbol, run). Never ranked."""
    d = d.sort(["dt", "trigger_et_min", "symbol", "run"])
    return d.with_columns(pl.int_range(pl.len()).over("dt").alias("_seq")).filter(
        pl.col("_seq") == 0
    )


def cmd_stage0() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    out: dict[str, Any] = {}
    # W1-0a
    hashes = _verify()
    df = stage0.load_panel_v1()
    sess = stage0.load_sessions_v1()
    rows = {
        (r["split"], r["source"]): r["len"]
        for r in df.group_by("split", "source").len().iter_rows(named=True)
    }
    nsess = {r["split"]: r["len"] for r in sess.group_by("split").len().iter_rows(named=True)}
    ok = rows == SPEC_ROWS and nsess == SPEC_SESSIONS
    out["W1-0a"] = {
        "sha256": hashes,
        "rows": {f"{k[0]}/{k[1]}": v for k, v in rows.items()},
        "sessions": nsess,
        "matches_spec": ok,
    }
    if not ok:
        raise SystemExit(f"STOP-AND-REPORT: split counts differ from panel-v1-spec §3: {out}")

    rc = stage0.rule_columns()
    fit = df.filter(pl.col("split") == "FIT")
    # W1-0b
    covered = set(FAMILY_OF) | set(UNASSIGNED)
    if covered != set(rc):
        raise SystemExit(
            f"W1-0b incomplete: missing {set(rc) - covered}, extra {covered - set(rc)}"
        )
    fams: dict[str, list[str]] = {}
    for c, f in FAMILY_OF.items():
        fams.setdefault(f, []).append(c)
    out["W1-0b"] = {"families": fams, "unassigned": UNASSIGNED}
    # W1-0c
    usable = {f: [c for c in cs if _usable(fit[c])] for f, cs in fams.items()}
    viable = [f for f, cs in usable.items() if len(cs) >= 2]
    out["W1-0c"] = {
        "usable": usable,
        "viable": viable,
        "n_viable": len(viable),
        "stop": len(viable) < 3,
    }

    # W1-0e — Spearman |rho| on FIT among every usable column; drift per split
    num = sorted({c for cs in usable.values() for c in cs})
    ranks = fit.select([pl.col(c).rank().alias(c) for c in num]).drop_nulls().to_numpy()
    rho = np.corrcoef(ranks, rowvar=False)
    pairs = []
    for i, j in combinations(range(len(num)), 2):
        if abs(rho[i, j]) > 0.9:
            a, b = num[i], num[j]
            na, nb = fit[a].null_count(), fit[b].null_count()
            keep, drop = (a, b) if na <= nb else (b, a)
            pairs.append(
                {
                    "a": a,
                    "b": b,
                    "rho": round(float(rho[i, j]), 4),
                    "same_family": FAMILY_OF[a] == FAMILY_OF[b],
                    "keep": keep,
                    "drop": drop,
                }
            )
    top = sorted(
        ((abs(float(rho[i, j])), num[i], num[j]) for i, j in combinations(range(len(num)), 2)),
        reverse=True,
    )[:25]
    drift = {}
    for c in num:
        qf = fit[c].drop_nulls().to_numpy()
        row = {}
        for sp, src in (("FIT", None), ("CHECK", None), ("HOLDOUT", "recon"), ("HOLDOUT", "live")):
            d = df.filter(pl.col("split") == sp)
            if src:
                d = d.filter(pl.col("source") == src)
            x = d[c].drop_nulls().to_numpy()
            # where the split's median sits in the FIT distribution: 0.50 = no shift
            row[f"{sp}{'/' + src if src else ''}"] = {
                "q10_q50_q90": [round(float(np.quantile(x, q)), 4) for q in (0.1, 0.5, 0.9)],
                "median_as_FIT_quantile": round(float((qf <= np.median(x)).mean()), 3),
            }
        drift[c] = row
    out["W1-0e"] = {
        "rho_gt_0.9": pairs,
        "top25_abs_rho": [[round(r, 3), a, b] for r, a, b in top],
        "drift": drift,
    }
    # staleness semantics check (free): is staleness = trigger - first hit?
    st = fit.select(
        (pl.col("trigger_et_min") - pl.col("first_hit_et_min") - pl.col("staleness_delay_min"))
        .abs()
        .alias("d")
    )["d"]
    out["staleness_vs_trigger_minus_first_hit"] = {
        "share_equal_within_1min": round(float((st <= 1).mean()), 3),
        "q50_q90_abs_diff": [float(st.quantile(0.5)), float(st.quantile(0.9))],
    }
    out["W1-0e"]["drops_applied"] = CORR_DROPS
    after = {f: [c for c in cs if c not in CORR_DROPS] for f, cs in usable.items()}
    out["W1-0c"]["usable_after_W1-0e"] = after
    out["W1-0c"]["viable_after_W1-0e"] = [f for f, cs in after.items() if len(cs) >= 2]
    for c in CORR_DROPS:
        assert c not in {col for col, _ in PRED.values()}, c
    # material drift = a HOLDOUT half's median outside FIT's q35..q65 (flagged, never dropped)
    out["W1-0e"]["drift_flags"] = [
        {
            "col": c,
            "family": FAMILY_OF[c],
            "split": k,
            "median_as_FIT_quantile": v["median_as_FIT_quantile"],
        }
        for c, row in drift.items()
        if c not in CORR_DROPS
        for k, v in row.items()
        if k.startswith("HOLDOUT") and not 0.35 <= v["median_as_FIT_quantile"] <= 0.65
    ]

    # W1-0d — throughput feasibility, N = 1, FIT (features only; free). Conjunctions too, so 2c's
    # grid is known feasible before any outcome is read.
    fit_sessions = nsess["FIT"]
    tp: dict[str, Any] = {}
    for k in (1, 2):
        for combo in combinations(sorted(PRED), k):
            for lvl in LEVELS:
                thr = {f: threshold(fit, f, lvl) for f in combo}
                n = select(fit, thr).height
                tp["&".join(combo) + ":" + lvl] = {
                    "thresholds": thr,
                    "rows_kept": stage0.run_rule(fit, predicate(thr)).height,
                    "trades_per_session": round(n / fit_sessions, 4),
                    "in_band": TP_BAND[0] <= n / fit_sessions <= TP_BAND[1],
                }
    out["W1-0d"] = tp
    out["W1-0d_dropped"] = [k for k, v in tp.items() if not v["in_band"]]
    (OUT / "w1-stage0.json").write_text(json.dumps(out, indent=2, default=str))
    print(json.dumps({k: out[k] for k in ("W1-0d", "W1-0d_dropped")}, indent=1, default=str))
    print(json.dumps(out["W1-0e"]["drift_flags"], indent=1))


# =============================================================================================
# Stage 2 — FIT only. Everything below reads outcomes, so it runs only after the Stage-1
# pre-registration and the batch comment for the stage are on the ledger (#735).
# =============================================================================================
SEED = 20260919
B_NULL = 200


class Fit:
    """FIT rows, their exit legs per family, and the recipient-priced outcome of every donor row in
    the same session — so a within-session permutation is an index lookup.

    ⚠️ Null convention (pre-registered): a permutation moves the *price path* (the exit legs, in R)
    from donor to recipient; the recipient keeps its own entry, stop, size and therefore its own
    cost. Moving net R instead would move the cost with the outcome and make S4's cost lever look
    like signal to the null. The diagonal reproduces `stage0.price_trade` exactly.
    """

    def __init__(self, rows: pl.DataFrame | None = None) -> None:
        _verify()
        df = stage0.load_panel_v1() if rows is None else rows
        self.df = df.filter(pl.col("split") == "FIT").sort(
            ["dt", "trigger_et_min", "symbol", "run"]
        )
        sess = stage0.load_sessions_v1().filter(pl.col("split") == "FIT")
        self.sessions = sorted(sess["dt"].to_list())
        self.n_sess = len(self.sessions)
        pdict = stage0.load_paths_v1()
        costs = stage0.Costs()
        d = self.df.with_row_index("i")
        self.dt = d["dt"].to_list()
        n = d.height
        groups: dict[Any, list[int]] = {}
        for i, s in enumerate(self.dt):
            groups.setdefault(s, []).append(i)
        self.groups = groups
        self.local = np.zeros(n, dtype=np.int64)
        self.gstart = np.zeros(n, dtype=np.int64)
        for idx in groups.values():
            for k, i in enumerate(idx):
                self.local[i] = k
                self.gstart[i] = idx[0]
        width = max(len(v) for v in groups.values())
        legs: dict[str, list[list[stage0.Leg]]] = {f: [] for f in EXITS}
        entry = np.zeros(n)
        stop = np.zeros(n)
        qty = np.zeros(n, dtype=np.int64)
        for i, r in enumerate(d.iter_rows(named=True)):
            path = pdict[r["key"]]
            entry[i] = max(r["entry_fill"], float(path[0, 0]))
            stop[i] = r["stop"]
            qty[i] = stage0.FULL_BP.qty(entry[i], stop[i])[0]
            for f in EXITS:
                legs[f].append(stage0.FAMILIES[f](path, r["entry_fill"], r["stop"]))
        # pair[f][i, k] = (net_r, gross_r, net_usd) for recipient i given donor = local index k
        self.net = {f: np.full((n, width), np.nan) for f in EXITS}
        self.gross = {f: np.full((n, width), np.nan) for f in EXITS}
        self.usd = {f: np.full((n, width), np.nan) for f in EXITS}
        for idx in groups.values():
            for i in idx:
                risk_ps = entry[i] - stop[i]
                if qty[i] < 1 or risk_ps <= 0:
                    continue
                risk_usd = qty[i] * risk_ps
                for k, j in enumerate(idx):
                    for f in EXITS:
                        lj = legs[f][j]
                        if not lj:
                            continue
                        re = [
                            stage0.Leg(g.frac, entry[i] + g.r * risk_ps, g.limit, g.r) for g in lj
                        ]
                        fees, slip = stage0.leg_costs(int(qty[i]), re, costs)
                        gross = sum(g.frac * g.r for g in re)
                        self.net[f][i, k] = gross - (fees + slip) / risk_usd
                        self.gross[f][i, k] = gross
                        self.usd[f][i, k] = gross * risk_usd - fees - slip
        self.stop_pct = d["stop_pct"].to_numpy()

    def identity(self) -> np.ndarray:
        return self.local.copy()

    def shuffled(self, rng: np.random.Generator) -> np.ndarray:
        p = np.zeros_like(self.local)
        for idx in self.groups.values():
            p[idx] = rng.permutation(len(idx))
        return p

    def selection(self, thr: dict[str, float], dates: set[Any] | None = None) -> np.ndarray:
        """Global row indices taken (N = 1, earliest by time) under literal thresholds, optionally
        restricted to a set of sessions (a walk-forward window)."""
        base = self.df.with_row_index("__i")
        if dates is not None:
            base = base.filter(pl.col("dt").is_in(list(dates)))
        taken = select(base, thr)
        return np.sort(taken["__i"].to_numpy().astype(np.int64))

    def J(self, sel: np.ndarray, fam: str, perm: np.ndarray, n_sess: int | None = None) -> float:
        v = self.net[fam][sel, perm[sel]]
        return float(np.nansum(v)) / (n_sess or self.n_sess)

    def report(
        self, sel: np.ndarray, fam: str, perm: np.ndarray, sessions: list[Any]
    ) -> dict[str, Any]:
        k = perm[sel]
        net, gross, usd = self.net[fam][sel, k], self.gross[fam][sel, k], self.usd[fam][sel, k]
        ok = ~np.isnan(net)
        trades = pl.DataFrame(
            {
                "dt": [self.dt[i] for i in sel[ok]],
                "net_r": net[ok],
                "gross_r": gross[ok],
                "net_usd": usd[ok],
            },
            schema={
                "dt": pl.Date,
                "net_r": pl.Float64,
                "gross_r": pl.Float64,
                "net_usd": pl.Float64,
            },
        )
        rep = stage0.session_report(trades, sessions)
        sp = self.stop_pct[sel[ok]]
        rep["unaffordable_or_invalid"] = int((~ok).sum())
        rep["stop_pct_of_taken_q10_q50_q90"] = [
            round(float(np.quantile(sp, q)), 4) for q in (0.1, 0.5, 0.9)
        ]
        rep["recon_vs_live"] = "FIT is recon-only; no live rows"
        return rep


def _thr(
    fit: Fit, sel_fams: tuple[str, ...], lvl: str, rows: pl.DataFrame | None = None
) -> dict[str, float]:
    base = fit.df if rows is None else rows
    return {f: threshold(base, f, lvl) for f in sel_fams}


def search(
    fit: Fit,
    sels: dict[tuple[tuple[str, ...], str], np.ndarray],
    perm: np.ndarray,
    upto: str = "2c",
) -> dict[str, Any]:
    """The pre-registered search, 2a → 2b → 2c, with its mechanical gates. Deterministic given the
    outcome assignment `perm`; the null re-runs exactly this per shuffled record."""
    tp_ok = {k: TP_BAND[0] <= len(v) / fit.n_sess <= TP_BAND[1] for k, v in sels.items()}
    pts: dict[str, float] = {}

    def score(sf: tuple[str, ...], lvl: str, ex: str) -> float | None:
        if not tp_ok[(sf, lvl)]:
            return None
        j = fit.J(sels[(sf, lvl)], ex, perm)
        pts[f"{'&'.join(sf)}:{lvl}:{ex}"] = j
        return j

    s2a = {f: {lvl: score((f,), lvl, "F2") for lvl in LEVELS} for f in sorted(PRED)}
    fam_score = {
        f: max((x for x in v.values() if x is not None), default=-np.inf) for f, v in s2a.items()
    }
    surv = sorted(sorted(PRED), key=lambda f: -fam_score[f])[:2]
    out: dict[str, Any] = {"2a": s2a, "family_score": fam_score, "survivors": surv}
    if upto == "2a":
        out["points"], out["best"] = pts, max(pts.values())
        return out
    s2b = {f: {lvl: {ex: score((f,), lvl, ex) for ex in EXITS} for lvl in LEVELS} for f in surv}
    ex_score = {
        ex: max(
            (s2b[f][lvl][ex] for f in surv for lvl in LEVELS if s2b[f][lvl][ex] is not None),
            default=-np.inf,
        )
        for ex in EXITS
    }
    top_ex = sorted(EXITS, key=lambda e: -ex_score[e])[:2]
    out |= {"2b": s2b, "exit_score": ex_score, "top_exits": top_ex}
    if upto == "2b":
        out["points"], out["best"] = pts, max(pts.values())
        return out
    conj = tuple(sorted(surv))
    out["2c"] = {lvl: {ex: score(conj, lvl, ex) for ex in top_ex} for lvl in LEVELS}
    out["conjunction"] = list(conj)
    # carried to 2d: the top two distinct (selection, exit) pairs over 2b + 2c, by FIT J
    best_pair: dict[tuple[str, str], tuple[float, str]] = {}
    for key, j in pts.items():
        sf, lvl, ex = key.split(":")
        in_cross = sf in surv or sf == "&".join(conj)
        if in_cross and ((sf, ex) not in best_pair or j > best_pair[(sf, ex)][0]):
            best_pair[(sf, ex)] = (j, lvl)
    ranked = sorted(best_pair.items(), key=lambda kv: -kv[1][0])[:2]
    out["carried"] = [
        {"selection": k[0], "exit": k[1], "level": v[1], "J": v[0]} for k, v in ranked
    ]
    out["points"], out["best"] = pts, max(pts.values())
    return out


def all_selections(fit: Fit) -> dict[tuple[tuple[str, ...], str], np.ndarray]:
    sels = {}
    for k in (1, 2):
        for combo in combinations(sorted(PRED), k):
            for lvl in LEVELS:
                sels[(combo, lvl)] = fit.selection(_thr(fit, combo, lvl))
    return sels


def _dump(name: str, obj: Any) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(json.dumps(obj, indent=2, default=str))


def _reports(
    fit: Fit, sels: dict[Any, np.ndarray], res: dict[str, Any], perm: np.ndarray
) -> dict[str, Any]:
    reps = {}
    for key in res["points"]:
        sf, lvl, ex = key.split(":")
        sel = sels[(tuple(sf.split("&")), lvl)]
        rep = fit.report(sel, ex, perm, fit.sessions)
        rep["thresholds"] = _thr(fit, tuple(sf.split("&")), lvl)
        reps[key] = rep
    return reps


def cmd_stage(upto: str) -> None:
    fit = Fit()
    sels = all_selections(fit)
    res = search(fit, sels, fit.identity(), upto=upto)
    # consistency: the diagonal equals the shared cost model's per-trade pricing
    chk = fit.df.head(200)
    oc = stage0.family_outcomes(chk, stage0.load_paths_v1()).filter(pl.col("family") == "F2")
    got = fit.net["F2"][np.arange(200), fit.local[:200]]
    assert np.allclose(got, oc["net_r"].to_numpy(), atol=1e-9, equal_nan=True)
    res["reports"] = _reports(fit, sels, res, fit.identity())
    _dump(f"w1-{upto}.json", res)
    print(json.dumps({k: v for k, v in res.items() if k != "reports"}, indent=1, default=str))


def cmd_2d() -> None:
    """Walk-forward refits (§7.2) for the two carried candidates, plus the ±20 % sensitivity band
    (§7.4 — a report on the frozen FIT thresholds, never used to select)."""
    fit = Fit()
    carried = json.loads((OUT / "w1-2c.json").read_text())["carried"]
    perm = fit.identity()
    out: dict[str, Any] = {"folds": [], "candidates": []}
    folds = [
        {"train": fit.sessions[:k], "test": fit.sessions[k : k + 50]} for k in (95, 145, 195)
    ]  # == stage0.folds_v1(), recomputed from the published session list
    for c in carried:
        sf, ex = tuple(c["selection"].split("&")), c["exit"]
        cand: dict[str, Any] = {"selection": c["selection"], "exit": ex, "FIT_level": c["level"]}
        rows = []
        for n, fold in enumerate(folds, 1):
            tr = set(fold["train"])
            train_rows = fit.df.filter(pl.col("dt").is_in(list(tr)))
            per_lvl = {}
            for lvl in LEVELS:
                thr = _thr(fit, sf, lvl, train_rows)
                sel = fit.selection(thr, tr)
                tp = len(sel) / len(tr)
                ok = TP_BAND[0] <= tp <= TP_BAND[1]
                per_lvl[lvl] = {
                    "thr": thr,
                    "tp": tp,
                    "J_train": fit.J(sel, ex, perm, len(tr)) if ok else None,
                }
            ok_lvls = [lvl for lvl in LEVELS if per_lvl[lvl]["J_train"] is not None]
            chosen = max(ok_lvls, key=lambda lv: per_lvl[lv]["J_train"])
            te = set(fold["test"])
            sel = fit.selection(per_lvl[chosen]["thr"], te)
            rows.append(
                {
                    "fold": n,
                    "train": [fold["train"][0], fold["train"][-1]],
                    "test": [fold["test"][0], fold["test"][-1]],
                    "chosen_level": chosen,
                    "thresholds": per_lvl[chosen]["thr"],
                    "J_train_by_level": {lv: per_lvl[lv]["J_train"] for lv in LEVELS},
                    "J_test": fit.J(sel, ex, perm, len(te)),
                    "test_trades_per_session": len(sel) / len(te),
                    "test_report": fit.report(sel, ex, perm, sorted(te)),
                }
            )
        idx = [LEVELS.index(r["chosen_level"]) for r in rows]
        cand["folds"] = rows
        cand["stable_within_one_grid_step"] = max(idx) - min(idx) <= 1
        # ±20 % on each frozen literal threshold, one at a time, on FIT
        base = _thr(fit, sf, c["level"])
        sens = {}
        for f in sf:
            for m in (0.8, 1.2):
                thr = dict(base) | {f: base[f] * m}
                sel = fit.selection(thr)
                sens[f"{PRED[f][0]}x{m}"] = {
                    "thr": thr[f],
                    "J": fit.J(sel, ex, perm),
                    "tp": len(sel) / fit.n_sess,
                }
        cand["FIT_thresholds"] = base
        cand["FIT_J"] = c["J"]
        cand["sensitivity_pm20"] = sens
        cand["J_positive_throughout_band"] = c["J"] > 0 and all(v["J"] > 0 for v in sens.values())
        out["candidates"].append(cand)
    _dump("w1-2d.json", out)
    print(json.dumps(out, indent=1, default=str))


def cmd_null() -> None:
    """§7.3 at matched intensity: the whole 2a → 2b → 2c sequence per shuffled record."""
    fit = Fit()
    sels = all_selections(fit)
    rng = np.random.default_rng(SEED)
    real = search(fit, sels, fit.identity())
    best_a, best_b, best_all = [], [], []
    for _ in range(B_NULL):
        r = search(fit, sels, fit.shuffled(rng))
        best_all.append(r["best"])
        best_a.append(max(v for k, v in r["points"].items() if k.endswith(":F2") and "&" not in k))
        best_b.append(
            max(
                v
                for k, v in r["points"].items()
                if "&" not in k and k.split(":")[0] in r["survivors"]
            )
        )
    ra = max(v for k, v in real["points"].items() if k.endswith(":F2") and "&" not in k)
    rb = max(
        v
        for k, v in real["points"].items()
        if "&" not in k and k.split(":")[0] in real["survivors"]
    )

    def p(null: list[float], x: float) -> float:
        return round((1 + sum(n >= x for n in null)) / (1 + B_NULL), 4)

    def q(null: list[float]) -> list[float]:
        return [round(float(np.quantile(null, t)), 4) for t in (0.05, 0.5, 0.95)]

    out = {
        "B": B_NULL,
        "seed": SEED,
        "2a_best_of_12": {"real": ra, "null_q05_q50_q95": q(best_a), "p": p(best_a, ra)},
        "2b_best_of_24": {"real": rb, "null_q05_q50_q95": q(best_b), "p": p(best_b, rb)},
        "sequence_best": {
            "real": real["best"],
            "null_q05_q50_q95": q(best_all),
            "p": p(best_all, real["best"]),
        },
        "per_point_p_vs_sequence_null": {k: p(best_all, v) for k, v in real["points"].items()},
        "null_best_all": best_all,
    }
    _dump("w1-null.json", out)
    print(json.dumps({k: v for k, v in out.items() if k != "null_best_all"}, indent=1))


if __name__ == "__main__":
    arg = sys.argv[1]
    if arg == "stage0":
        cmd_stage0()
    elif arg in ("2a", "2b", "2c"):
        cmd_stage(arg)
    elif arg == "2d":
        cmd_2d()
    elif arg == "null":
        cmd_null()
    else:
        raise SystemExit(f"unknown command {arg}")
