"""Agent B — the experiments. `python study.py <section>` or no arg for all.

Sections: base, exclude, sizing, capacity, limits, equity, robust.
Everything runs on DEV+VAL only; HOLDOUT is never loaded.
"""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent))

import sim as S  # noqa: E402

SELS = ("shipped", "pool")
BASE = S.RiskConfig()


def hdr(t: str) -> None:
    print(f"\n{'=' * 100}\n{t}\n{'=' * 100}")


def show(work: pl.DataFrame, cfg: S.RiskConfig, name: str, sel: str = "shipped") -> dict[str, Any]:
    _, r = S.run(work, cfg, selection=S.SELECTIONS[sel])
    print(f"  {name:<34} " + S.line(r))
    return r


# ---------------------------------------------------------------------------------------------
def sec_base(work: pl.DataFrame) -> None:
    hdr("1. BASELINE — the shipped risk config on DEV+VAL, under three selections")
    for sel in ("shipped", "passed", "pool"):
        r = show(work, BASE, f"{sel} / 5% / 50% / 2-a-day", sel)
        for sp, b in r["splits"].items():
            if b["trades"]:
                print(f"    {sp:<32} " + S.line(b))


# ---------------------------------------------------------------------------------------------
def sec_exclude(work: pl.DataFrame) -> None:
    hdr("2. COST-DRAG EXCLUSION — 'skip a trade whose round-trip cost exceeds X% of its risk'")
    print(
        "   worst_case_cost_r is deterministic at entry:\n"
        "   (fees + 2-tick slip) / (qty * (entry-stop)),\n"
        "   assuming the trade loses. No outcome, no assumed win rate, one threshold."
    )
    for sel in SELS:
        print(f"\n  -- selection = {sel} --")
        show(work, BASE, "no exclusion", sel)
        for th in (0.30, 0.25, 0.20, 0.15, 0.12, 0.10, 0.08, 0.06, 0.05, 0.04):
            show(work, replace(BASE, max_cost_r=th), f"max_cost_r <= {th:.2f}", sel)

    hdr("2b. Is it cost or is it selection? gross R of the KEPT vs the EXCLUDED population")
    for sel in SELS:
        sub = S.SELECTIONS[sel](work)
        x = S.simulate(sub, replace(BASE, max_per_day=99))
        x = x.with_columns(
            pl.struct(["entry_fill", "stop", "qty"])
            .map_elements(
                lambda d: BASE.worst_case_cost_r(d["entry_fill"], d["stop"], d["qty"]),
                return_dtype=pl.Float64,
            )
            .alias("wc_cost_r")
        )
        print(f"\n  -- selection = {sel} (capacity off, so every candidate is measured) --")
        print(
            x.with_columns(
                pl.when(pl.col("wc_cost_r") <= 0.10)
                .then(pl.lit("a <=10%"))
                .when(pl.col("wc_cost_r") <= 0.15)
                .then(pl.lit("b 10-15%"))
                .when(pl.col("wc_cost_r") <= 0.25)
                .then(pl.lit("c 15-25%"))
                .when(pl.col("wc_cost_r") <= 0.40)
                .then(pl.lit("d 25-40%"))
                .otherwise(pl.lit("e >40%"))
                .alias("cost_band")
            )
            .group_by("cost_band")
            .agg(
                pl.len().alias("n"),
                pl.col("r").mean().round(3).alias("gross_r"),
                pl.col("net_r").mean().round(3).alias("net_r"),
                pl.col("cost_r").mean().round(3).alias("realised_drag"),
                pl.col("net_usd").mean().round(2).alias("net_usd_tr"),
                (pl.col("r") > 0).mean().round(3).alias("win"),
            )
            .sort("cost_band")
        )


# ---------------------------------------------------------------------------------------------
def sec_sizing(work: pl.DataFrame) -> None:
    hdr("3. SIZING — risk fraction x notional cap. Cash-account invariant: cap x trades/day <= 1.0")
    for sel in SELS:
        print(f"\n  -- selection = {sel}, 2 trades/day (so cap <= 0.50 is the legal range) --")
        for rf in (0.03, 0.05, 0.08, 0.10, 0.15, 0.20):
            for pf in (0.25, 0.50):
                show(
                    work,
                    replace(BASE, risk_fraction=rf, position_fraction=pf),
                    f"risk {rf:.0%} / cap {pf:.0%}",
                    sel,
                )
        print("   [illegal on a cash account at 2/day, shown only to isolate the cap's effect]")
        for pf in (0.75, 1.00):
            show(work, replace(BASE, position_fraction=pf), f"risk 5% / cap {pf:.0%}", sel)

    hdr("3b. How much of the intended risk actually gets deployed")
    for sel in SELS:
        x = S.simulate(S.SELECTIONS[sel](work), BASE)
        print(
            f"  {sel:<8} mean deployed risk ${x['risk_usd'].mean():.2f} of the $25.00 intended "
            f"({x['risk_usd'].mean() / 25:.0%})   median ${x['risk_usd'].median():.2f}   "
            f"cap-bound {(x['sized_by'] == 'cap').mean():.0%}"
        )


# ---------------------------------------------------------------------------------------------
def sec_capacity(work: pl.DataFrame) -> None:
    hdr("4. CAPACITY — n trades/day. Cash account: cap fraction must be 1/n to stay settled.")
    for sel in SELS:
        print(f"\n  -- selection = {sel} --")
        for n in (1, 2, 3, 4, 6, 99):
            pf = min(0.50, 1.0 / n) if n < 99 else 0.05
            show(
                work,
                replace(BASE, max_per_day=n, position_fraction=1.0 / n if n < 99 else 0.02),
                f"{n}/day, cap {1.0 / n if n < 99 else 0.02:.0%} (settled-cash legal)",
                sel,
            )
            _ = pf
        print(
            "   [same capacities holding the cap at the shipped 50% — breaks settled cash for n>2]"
        )
        for n in (1, 2, 3, 4, 6, 99):
            show(work, replace(BASE, max_per_day=n), f"{n}/day, cap 50%", sel)

    hdr("4b. one_per_symbol — the real system has no such rule")
    for sel in SELS:
        print(f"\n  -- selection = {sel} --")
        show(work, BASE, "same ticker twice allowed (shipped)", sel)
        show(work, replace(BASE, one_per_symbol=True), "one entry per symbol per day", sel)
        sub = S.SELECTIONS[sel](work)
        bk = S.simulate(sub, BASE)
        dup = bk.group_by(["dt", "symbol"]).agg(pl.len()).filter(pl.col("len") > 1)
        print(f"    days where the book took the same ticker twice: {dup.height}")


# ---------------------------------------------------------------------------------------------
def sec_limits(work: pl.DataFrame) -> None:
    hdr("5. LOSS LIMITS — daily stop and a risk ladder")
    print(
        "   A daily stop can only react to a loss that has ALREADY RESOLVED at the next trigger.\n"
        "   Exit times come from the bars, not from an assumption."
    )
    for sel in SELS:
        sub = S.SELECTIONS[sel](work)
        bk = S.simulate(sub, BASE)
        # how often is trade 1's outcome known before trade 2 triggers?
        seq = bk.with_columns(pl.int_range(pl.len()).over("dt").alias("k"))
        second = seq.filter(pl.col("k") == 1)
        first = seq.filter(pl.col("k") == 0).select(
            ["dt", pl.col("exit_et_min").alias("e1"), pl.col("r").alias("r1")]
        )
        j = second.join(first, on="dt")
        known = j.filter((pl.col("e1") <= pl.col("trigger_et_min")) & (pl.col("r1") <= 0))
        print(
            f"\n  -- selection = {sel} -- second trades: {j.height}; "
            f"of those, trade 1 had already stopped out: {known.height} "
            f"({known.height / max(j.height, 1):.0%})"
        )
        show(work, BASE, "no limit", sel)
        for n in (1, 2):
            show(work, replace(BASE, daily_loss_limit=n), f"stop after {n} resolved loser(s)", sel)
        for lad, nm in (
            (((2, 0.5),), "streak>=2 -> half risk"),
            (((3, 0.5),), "streak>=3 -> half risk"),
            (((2, 0.5), (4, 0.25)), "2->half, 4->quarter"),
            (((2, 0.0),), "streak>=2 -> stand down"),
            (((3, 0.0),), "streak>=3 -> stand down"),
        ):
            show(work, replace(BASE, ladder=lad), f"ladder: {nm}", sel)


# ---------------------------------------------------------------------------------------------
def sec_equity(work: pl.DataFrame, cfg: S.RiskConfig | None = None) -> list[dict[str, Any]]:
    hdr("6. ACCOUNT SIZE — the same rules at other equity levels")
    cfg = cfg or BASE
    rows = []
    for sel in SELS:
        print(f"\n  -- selection = {sel} --")
        for eq in (250, 500, 1000, 2000, 5000, 10000, 25000, 100000):
            r = show(work, replace(cfg, equity=float(eq)), f"equity ${eq:,}", sel)
            rows.append(
                {
                    "selection": sel,
                    "equity": eq,
                    **{
                        k: r[k]
                        for k in (
                            "trades",
                            "gross_r",
                            "net_r",
                            "net_r_per_trade",
                            "net_usd",
                            "net_usd_per_trade",
                            "cost_r_per_trade",
                            "cap_bound_pct",
                            "mean_risk_usd",
                            "max_dd_net_usd",
                        )
                    },
                }
            )
    print(
        "\n   note: gross R is identical at every equity (R is size-independent); everything that\n"
        "   moves is cost drag and the notional cap. Net USD scales with equity ONCE the fixed\n"
        "   commission stops mattering."
    )
    return rows


# ---------------------------------------------------------------------------------------------
def _fit_max_cost_r(
    train: pl.DataFrame, sel: Any, grid: tuple[float, ...], cfg: S.RiskConfig
) -> float:
    """Pick the threshold that maximises net USD on the training window. The FIT step."""
    best, best_v = 1e9, -1e18
    n = train["dt"].n_unique()
    if n < 5:
        return 1e9
    for th in grid:
        x = S.simulate(sel(train), replace(cfg, max_cost_r=th))
        v = float(x["net_usd"].sum()) if not x.is_empty() else -1e18
        if v > best_v:
            best, best_v = th, v
    return best


def walk_forward_risk(
    work: pl.DataFrame,
    sel: Any,
    *,
    n_blocks: int = 6,
    min_train: int = 60,
    fixed: float | None = None,
    cfg: S.RiskConfig | None = None,
    grid: tuple[float, ...] = (0.06, 0.08, 0.10, 0.12, 0.15, 0.20, 0.30, 1e9),
) -> dict[str, Any]:
    """Expanding window. `fixed=None` refits the threshold each block; a float pins it."""
    cfg = cfg or BASE
    dates = sorted(work["dt"].unique().to_list())
    edges = np.linspace(min_train, len(dates), n_blocks + 1).astype(int)
    blocks = []
    for a, b in zip(edges[:-1], edges[1:], strict=False):
        if b <= a:
            continue
        train = work.filter(pl.col("dt") < dates[a])
        test = work.filter(pl.col("dt").is_in(dates[a:b]))
        th = fixed if fixed is not None else _fit_max_cost_r(train, sel, grid, cfg)
        x = S.simulate(sel(test), replace(cfg, max_cost_r=th))
        r = S.report(x, sessions=test["dt"].n_unique())
        blocks.append(
            {
                "from": str(dates[a]),
                "to": str(dates[b - 1]),
                "th": round(th, 3),
                "trades": r["trades"],
                "net_r": r["net_r"],
                "net_usd": r["net_usd"],
                "net_r_per_trade": r["net_r_per_trade"],
            }
        )
    return {
        "blocks": blocks,
        "blocks_positive_usd": sum(1 for x in blocks if x["net_usd"] > 0),
        "n_blocks": len(blocks),
        "total_net_usd": round(sum(x["net_usd"] for x in blocks), 2),
        "total_net_r": round(sum(x["net_r"] for x in blocks), 2),
        "total_trades": sum(x["trades"] for x in blocks),
    }


def sec_robust(
    work: pl.DataFrame, th: float = 0.10, cfg: S.RiskConfig | None = None
) -> dict[str, Any]:
    cfg = cfg or BASE
    hdr(f"7. ANTI-OVERFIT — walk-forward, sensitivity, permutation for max_cost_r <= {th}")
    print(f"   base config: {cfg.as_dict()}")
    out: dict[str, Any] = {}
    for sel in SELS:
        f = S.SELECTIONS[sel]
        print(f"\n  -- selection = {sel} --")
        wf_none = walk_forward_risk(work, f, fixed=1e9, cfg=cfg)
        wf_fix = walk_forward_risk(work, f, fixed=th, cfg=cfg)
        wf_refit = walk_forward_risk(work, f, fixed=None, cfg=cfg)
        for nm, wf in (
            ("no exclusion", wf_none),
            (f"fixed {th}", wf_fix),
            ("refit each block", wf_refit),
        ):
            print(
                f"    WF {nm:<18} {wf['blocks_positive_usd']}/{wf['n_blocks']} blocks +$  "
                f"total ${wf['total_net_usd']:+8.2f}  {wf['total_net_r']:+7.2f}R  "
                f"{wf['total_trades']:>4} trades"
            )
            for b in wf["blocks"]:
                print(
                    f"       {b['from']}..{b['to']}  th={b['th']:<6} n={b['trades']:>3}  "
                    f"${b['net_usd']:+8.2f}  {b['net_r']:+6.2f}R"
                )
        print("    sensitivity (+/-20% and +/-40% on the one threshold):")
        for m in (0.6, 0.8, 1.0, 1.2, 1.4):
            show(work, replace(cfg, max_cost_r=th * m), f"max_cost_r = {th * m:.3f} (x{m})", sel)
        print("    sensitivity on risk_fraction and position_fraction:")
        for rf in (0.04, 0.05, 0.06):
            for pf in (0.8, 1.0):
                show(
                    work,
                    replace(cfg, max_cost_r=th, risk_fraction=rf, position_fraction=pf),
                    f"risk {rf:.0%} / cap {pf:.0%}",
                    sel,
                )
        out[sel] = {"wf_none": wf_none, "wf_fixed": wf_fix, "wf_refit": wf_refit}

    hdr("7b. PERMUTATION — same trade count on the same days, drawn at random")
    for sel in SELS:
        f = S.SELECTIONS[sel]
        kept = S.simulate(f(work), replace(cfg, max_cost_r=th))
        p = _perm(work, f, kept, cfg)
        print(
            f"  {sel:<8} observed net ${kept['net_usd'].sum():+.2f} over {kept.height} trades; "
            f"random books of the same shape beat it {p:.1%} of the time"
        )
        out[sel]["perm"] = p

    hdr("7c. SOURCE SPLIT — not available outside HOLDOUT")
    print(
        "  recon covers 2025-10-30..2026-06-30 and live 2026-07-01..2026-08-13, so `source`\n"
        "  and `split` are the SAME cut: DEV+VAL is 100% recon. A recon-vs-live test cannot run\n"
        "  without opening HOLDOUT. Substituted below: DEV vs VAL, and odd vs even session index."
    )
    for sel in SELS:
        f = S.SELECTIONS[sel]
        x = S.simulate(f(work), replace(cfg, max_cost_r=th))
        dates = sorted(work["dt"].unique().to_list())
        idx = {d: i % 2 for i, d in enumerate(dates)}
        x = x.with_columns(pl.col("dt").replace_strict(idx).alias("parity"))
        for name, sub in (
            ("dev", x.filter(pl.col("split") == "dev")),
            ("val", x.filter(pl.col("split") == "val")),
            ("odd sessions", x.filter(pl.col("parity") == 1)),
            ("even sessions", x.filter(pl.col("parity") == 0)),
        ):
            if sub.is_empty():
                continue
            print(
                f"  {sel:<8} {name:<14} n={sub.height:>4}  net ${sub['net_usd'].sum():+8.2f}  "
                f"{sub['net_r'].sum():+7.2f}R  ({sub['net_r'].mean():+.3f}/trade)"
            )
    return out


def _perm(
    work: pl.DataFrame, sel: Any, kept: pl.DataFrame, cfg: S.RiskConfig, n: int = 400
) -> float:
    """Random books with the same per-day trade count, drawn from the same day's eligible pool."""
    if kept.is_empty():
        return 1.0
    rng = np.random.default_rng(11)
    obs = float(kept["net_usd"].sum())
    per_day = {r["dt"]: r["k"] for r in kept.group_by("dt").agg(pl.len().alias("k")).to_dicts()}
    pool = {k[0]: g for k, g in sel(work).group_by(["dt"])}
    hits = 0
    for _ in range(n):
        picks = []
        for d, k in per_day.items():
            g = pool.get(d)
            if g is None or g.is_empty():
                continue
            i = rng.choice(g.height, size=min(k, g.height), replace=False)
            picks.append(g[i.tolist()])
        if not picks:
            continue
        x = S.simulate(pl.concat(picks), replace(cfg, max_per_day=99, max_cost_r=1e9))
        if float(x["net_usd"].sum()) >= obs:
            hits += 1
    return (hits + 1) / (n + 1)


# ---------------------------------------------------------------------------------------------
SECTIONS = {
    "base": sec_base,
    "exclude": sec_exclude,
    "sizing": sec_sizing,
    "capacity": sec_capacity,
    "limits": sec_limits,
    "equity": sec_equity,
    "robust": sec_robust,
}


def main() -> None:
    work = S.load_work()
    print(f"DEV+VAL population: {work.height} rows, {work['dt'].n_unique()} sessions")
    want = sys.argv[1:] or list(SECTIONS)
    res: dict[str, Any] = {}
    for k in want:
        res[k] = SECTIONS[k](work)
    S.OUT.mkdir(parents=True, exist_ok=True)
    (S.OUT / "sections.json").write_text(json.dumps(res, indent=2, default=str))


if __name__ == "__main__":
    main()
