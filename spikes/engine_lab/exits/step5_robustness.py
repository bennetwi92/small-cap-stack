"""Step 5 — the anti-overfit battery for the proposed bracket, plus the three exposure questions.

Proposal (2 free parameters, both in units of the setup's own consolidation range C):

    stop   = entry - 1.30 * C      (the consolidation low, pushed 30% of the range further away)
    target = entry + 2.00 * C      (unchanged in price from the shipped 2R target)

Battery:
  * walk-forward, twice — once with the bracket held fixed (is it stable?) and once **refitting
    (m, t) on the training window of every block** (would the search have found it in real time?)
  * sensitivity, +/-20% on each of the two parameters, on its own
  * per-source, recon vs live -- and per-split, dev vs val
  * a session-block bootstrap, because the usual permutation test resamples *which rows are
    selected* and this proposal does not touch selection at all
  * same-bar exposure, entry-fill exposure, and what happens at 09:30
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import lab as X
import numpy as np
import polars as pl
from lab import C

M_STAR = 1.30
T_STAR = 2.00


def bracket(m: float = M_STAR, t: float = T_STAR, *, exit_930: bool = False) -> X.Bracket:
    return X.Bracket(buf_frac=m - 1.0, target_r=t / m, exit_at_930=exit_930)


# ---------------------------------------------------------------------------------------------
def wf_blocks(df: pl.DataFrame, n_blocks: int = 6, min_train_sessions: int = 60) -> list[tuple]:
    """The same block edges `common.walk_forward` uses, so the two are comparable."""
    dates = sorted(df["dt"].unique().to_list())
    edges = np.linspace(min_train_sessions, len(dates), n_blocks + 1).astype(int)
    return [
        (
            dates[a],
            dates[b - 1],
            df.filter(pl.col("dt") < dates[a]),
            df.filter(pl.col("dt").is_in(dates[a:b])),
        )
        for a, b in zip(edges[:-1], edges[1:], strict=True)
        if b > a
    ]


M_SEARCH = [1.0, 1.1, 1.15, 1.2, 1.25, 1.3, 1.4, 1.5, 1.75, 2.0]
T_SEARCH = [1.0, 1.25, 1.5, 1.75, 2.0, 2.25, 2.5, 3.0]


def fit_bracket(
    train: pl.DataFrame, g: X.Geom, p: X.Packed, sel: Callable | None
) -> tuple[float, float]:
    """Pick (m, t) by net R per trade on the training window only. No peeking at the block."""
    best, arg = -1e9, (1.0, 2.0)
    for m in M_SEARCH:
        for t in T_SEARCH:
            r = X.evaluate(train, bracket(m, t), g, p, selector=sel)
            if r["trades"] >= 20 and r["net_r_per_trade"] > best:
                best, arg = r["net_r_per_trade"], (m, t)
    return arg


def walk_forward(
    dv: pl.DataFrame, g: X.Geom, p: X.Packed, sel: Callable | None, *, refit: bool
) -> dict[str, Any]:
    rows = []
    for a, b, train, test in wf_blocks(dv):
        m, t = fit_bracket(train, g, p, sel) if refit else (M_STAR, T_STAR)
        r = X.evaluate(test, bracket(m, t), g, p, selector=sel)
        rows.append(
            {
                "from": str(a),
                "to": str(b),
                "train_sessions": train["dt"].n_unique(),
                "m": m,
                "t": t,
                "trades": r["trades"],
                "net_r": r["net_r"],
                "net_r_per_trade": r["net_r_per_trade"],
                "win": r["win_rate"],
            }
        )
    tot = sum(r["net_r"] for r in rows)
    n = sum(r["trades"] for r in rows)
    return {
        "blocks": rows,
        "total_net_r": round(tot, 2),
        "total_trades": n,
        "net_r_per_trade": round(tot / n, 4) if n else 0.0,
        "blocks_positive": sum(1 for r in rows if r["net_r"] > 0),
        "n_blocks": len(rows),
    }


# ---------------------------------------------------------------------------------------------
def sensitivity(dv: pl.DataFrame, g: X.Geom, p: X.Packed, sel: Callable | None) -> list[dict]:
    out = []
    for name, base in (("stop m", M_STAR), ("target t", T_STAR)):
        for mult in (0.8, 0.9, 1.0, 1.1, 1.2):
            m, t = (base * mult, T_STAR) if name == "stop m" else (M_STAR, base * mult)
            r = X.evaluate(dv, bracket(m, t), g, p, selector=sel)
            sp = r.get("split", {})
            out.append(
                {
                    "param": name,
                    "mult": mult,
                    "value": round(base * mult, 3),
                    "trades": r["trades"],
                    "net_r_per_trade": r["net_r_per_trade"],
                    "dev": sp.get("dev", {}).get("net_r_per_trade", 0),
                    "val": sp.get("val", {}).get("net_r_per_trade", 0),
                    "net_r": r["net_r"],
                }
            )
    return out


def block_bootstrap(
    dv: pl.DataFrame, g: X.Geom, p: X.Packed, sel: Callable | None, *, n: int = 2000
) -> dict[str, Any]:
    """Resample whole sessions with replacement. Sessions, not trades, because a day's two trades
    are not independent of each other."""
    tr = X.book_with_bracket(dv, bracket(), g, p, selector=sel)
    s = C.score(tr, sessions=dv["dt"].n_unique())
    t = s["_trades"]
    per_day = {str(k[0]): grp["net_r"].to_numpy() for k, grp in t.group_by(["dt"])}
    days = list(per_day)
    rng = np.random.default_rng(11)
    means = np.empty(n)
    for i in range(n):
        pick = rng.choice(len(days), size=len(days), replace=True)
        vals = np.concatenate([per_day[days[j]] for j in pick])
        means[i] = vals.mean()
    return {
        "observed_net_r_per_trade": round(float(t["net_r"].mean()), 4),
        "sessions": len(days),
        "p05": round(float(np.percentile(means, 5)), 4),
        "p50": round(float(np.percentile(means, 50)), 4),
        "p95": round(float(np.percentile(means, 95)), 4),
        "frac_positive": round(float((means > 0).mean()), 4),
    }


# ---------------------------------------------------------------------------------------------
def exposures(dv: pl.DataFrame, g: X.Geom, p: X.Packed, sel: Callable | None) -> dict[str, Any]:
    """How much of the answer rests on assumptions rather than on measurements?"""
    tr = X.book_with_bracket(dv, bracket(), g, p, selector=sel)
    s = C.score(tr, sessions=dv["dt"].n_unique())
    t = s["_trades"]
    base = float(t["net_r"].mean())

    # 1. same-bar: the entry bar contained both the fill and the stop. #583 found the conservative
    #    reading wrong 38% of the time. Rebook that fraction of them as if they had survived.
    sb = t["bracket_same_bar"].to_numpy()
    out: dict[str, Any] = {
        "trades": t.height,
        "net_r_per_trade": round(base, 4),
        "same_bar_n": int(sb.sum()),
        "same_bar_pct": round(float(sb.mean()), 4),
    }
    # what if every same-bar trade had instead run to its target? (an absolute upper bound)
    alt = t["net_r"].to_numpy().copy()
    tgt_r = (t["bracket_target"] - t["entry_fill"]) / (t["entry_fill"] - t["stop"])
    out["same_bar_all_won_net"] = round(float(np.where(sb, tgt_r.to_numpy() * 0.9, alt).mean()), 4)
    out["same_bar_38pct_won_net"] = round(
        float(
            base + 0.38 * sb.mean() * (float(np.mean(tgt_r.to_numpy()[sb] * 0.9)) - (-1.1))
            if sb.sum()
            else base
        ),
        4,
    )

    # 2. the conservative fill: +3 ticks above the trigger, sometimes above the entry bar's high
    fab = t["fill_above_entry_bar_high"].to_numpy()
    out["fill_above_bar_high_n"] = int(fab.sum())
    out["fill_above_bar_high_pct"] = round(float(fab.mean()), 4)
    out["net_excl_fill_above_high"] = round(float(t["net_r"].to_numpy()[~fab].mean()), 4)

    # 3. still open when the bell rings
    op = t["bracket_open_930"].to_numpy()
    out["open_at_930_n"] = int(op.sum())
    out["open_at_930_pct"] = round(float(op.mean()), 4)
    out["net_of_the_open_at_930"] = round(
        float(t["net_r"].to_numpy()[op].mean()) if op.sum() else 0.0, 4
    )
    out["net_of_the_rest"] = round(float(t["net_r"].to_numpy()[~op].mean()), 4)
    # what a hard flatten at the 09:30 open would have cost
    r930 = X.evaluate(dv, bracket(exit_930=True), g, p, selector=sel)
    out["flatten_at_0930_net_r_per_trade"] = r930["net_r_per_trade"]
    out["flatten_at_0930_net_r"] = r930["net_r"]
    out["flatten_at_0930_win"] = r930["win_rate"]
    return out


def upper_bounds(dv: pl.DataFrame, g: X.Geom, p: X.Packed, sel: Callable | None) -> dict[str, Any]:
    """What the FORBIDDEN exits would have been worth. Context only — not proposable."""
    tr = X.book_with_bracket(
        dv, X.Bracket(buf_frac=M_STAR - 1.0, target_r=None, target_mode="none"), g, p, selector=sel
    )
    mfe = tr["bracket_max_r"].to_numpy()
    res = {}
    # a perfect-foresight exit at the MFE: the absolute ceiling on any exit rule
    res["perfect_foresight_gross_r_per_trade"] = round(float(np.maximum(mfe, -1.0).mean()), 4)
    # breakeven-move-after-1R, as a clearly-labelled upper bound (NOT a simple bracket)
    for trig in (1.0, 1.5):
        r = np.where(mfe >= T_STAR / M_STAR, T_STAR / M_STAR, np.where(mfe >= trig, 0.0, -1.0))
        res[f"be_at_{trig}R_gross_r_per_trade"] = round(float(r.mean()), 4)
    return res


def main() -> None:
    df, p, g, paths, pre = X.load_all()
    dv = df.filter(pl.col("split") != "holdout")
    report: dict[str, Any] = {"m": M_STAR, "t": T_STAR}

    for name, sel in (("SHIPPED", C.SHIPPED), ("RAW POOL", None)):
        print(
            f"\n{'=' * 96}\n== {name}: stop = entry - {M_STAR}C, target = entry + {T_STAR}C "
            f"({T_STAR / M_STAR:.2f}R)\n{'=' * 96}"
        )
        r = X.evaluate(dv, bracket(), g, p, selector=sel)
        print(C.summarise(r, "proposal, DEV+VAL"))
        print(
            f"    stopped {r['pct_stopped']:.1%}  same-bar {r['pct_same_bar']:.1%}  "
            f"open@09:30 {r['pct_open_930']:.1%}  mean stop {r['mean_stop_pct']:.2%} of entry  "
            f"cap-bound {r['cap_bound']}/{r['trades']}  cost {r['cost_r_per_trade']:.3f}R"
        )
        base = X.evaluate(dv, X.Bracket(target_r=2.0), g, p, selector=sel)
        print(C.summarise(base, "shipped bracket, same book, DEV+VAL"))

        wf_fixed = walk_forward(dv, g, p, sel, refit=False)
        wf_refit = walk_forward(dv, g, p, sel, refit=True)
        print(f"\n  walk-forward, bracket FIXED at ({M_STAR}, {T_STAR}):")
        for b in wf_fixed["blocks"]:
            print(
                f"    {b['from']}..{b['to']}  n={b['trades']:<3} net {b['net_r']:+6.2f}R "
                f"({b['net_r_per_trade']:+.3f}/trade) win {b['win']:.0%}"
            )
        print(
            f"    -> {wf_fixed['blocks_positive']}/{wf_fixed['n_blocks']} blocks positive, "
            f"{wf_fixed['total_net_r']:+.2f}R total ({wf_fixed['net_r_per_trade']:+.3f}/trade)"
        )
        print("\n  walk-forward, bracket REFIT on each training window:")
        for b in wf_refit["blocks"]:
            print(
                f"    {b['from']}..{b['to']}  fit m={b['m']} t={b['t']}  n={b['trades']:<3} "
                f"net {b['net_r']:+6.2f}R ({b['net_r_per_trade']:+.3f}/trade)"
            )
        print(
            f"    -> {wf_refit['blocks_positive']}/{wf_refit['n_blocks']} blocks positive, "
            f"{wf_refit['total_net_r']:+.2f}R total ({wf_refit['net_r_per_trade']:+.3f}/trade)"
        )

        sens = sensitivity(dv, g, p, sel)
        print("\n  sensitivity (+/-20%, one parameter at a time):")
        for s in sens:
            print(
                f"    {s['param']:<10} x{s['mult']:.1f} = {s['value']:<6} n={s['trades']:<4} "
                f"net/tr {s['net_r_per_trade']:+.3f} (dev {s['dev']:+.3f} / val {s['val']:+.3f})"
            )

        boot = block_bootstrap(dv, g, p, sel)
        print(
            f"\n  session bootstrap (2000 resamples of {boot['sessions']} sessions): "
            f"observed {boot['observed_net_r_per_trade']:+.3f}, "
            f"90% band [{boot['p05']:+.3f}, {boot['p95']:+.3f}], "
            f"positive in {boot['frac_positive']:.0%} of resamples"
        )

        exp = exposures(dv, g, p, sel)
        print("\n  exposures:")
        print(
            f"    same-bar stops              {exp['same_bar_n']}/{exp['trades']} "
            f"({exp['same_bar_pct']:.1%})  -> net if ALL of them had won: "
            f"{exp['same_bar_all_won_net']:+.3f} (vs {exp['net_r_per_trade']:+.3f})"
        )
        print(
            f"    fill above the entry bar    {exp['fill_above_bar_high_n']}/{exp['trades']} "
            f"({exp['fill_above_bar_high_pct']:.1%})  -> net excluding them: "
            f"{exp['net_excl_fill_above_high']:+.3f}"
        )
        print(
            f"    still open at 09:30         {exp['open_at_930_n']}/{exp['trades']} "
            f"({exp['open_at_930_pct']:.1%})  those trades net "
            f"{exp['net_of_the_open_at_930']:+.3f} vs {exp['net_of_the_rest']:+.3f} for the rest"
        )
        print(
            f"    hard flatten at 09:30 open  net/trade "
            f"{exp['flatten_at_0930_net_r_per_trade']:+.3f} "
            f"({exp['flatten_at_0930_net_r']:+.1f}R, win {exp['flatten_at_0930_win']:.1%})"
        )

        ub = upper_bounds(dv, g, p, sel)
        print("\n  upper bounds from exits this user will NOT run (context only):")
        for label, k in (
            ("perfect-foresight exit at the MFE", "perfect_foresight_gross_r_per_trade"),
            ("breakeven move after +1.0R", "be_at_1.0R_gross_r_per_trade"),
            ("breakeven move after +1.5R", "be_at_1.5R_gross_r_per_trade"),
        ):
            print(f"    {label:<34}: {ub[k]:+.3f} gross/trade")
        print(f"    (the proposal itself, gross)      : {r['r_per_trade']:+.3f} gross/trade")

        report[name] = {
            "proposal": {k: v for k, v in r.items() if k != "_trades"},
            "shipped_bracket": {k: v for k, v in base.items() if k != "_trades"},
            "walk_forward_fixed": wf_fixed,
            "walk_forward_refit": wf_refit,
            "sensitivity": sens,
            "bootstrap": boot,
            "exposures": exp,
            "upper_bounds": ub,
        }
    with (X.OUT / "robustness.json").open("w") as fh:
        json.dump(report, fh, indent=1, default=str)
    print(f"\nwrote {X.OUT / 'robustness.json'}")


if __name__ == "__main__":
    main()
