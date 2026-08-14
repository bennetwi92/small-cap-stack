"""Step 10 — what the surviving rule actually IS, what it depends on, and the final scorecards.

Three things the synthesis step needs and nothing so far has produced:

1. **An interpretation.** `hits_before_trigger <= 2` is doing most of the work. What is it a proxy
   for? If it is really time-of-day or staleness in disguise, the rule is not what it looks like.
2. **Cross-agent dependence.** The exit (2R) and the cap (2/day) are held fixed here but are being
   optimised by the other two agents. If the rule only survives at 2R, or only at a 2/day cap, the
   synthesis must know that before composing.
3. **The frequency/quality curve** — net R per trade against trades per session, so the trade-off
   can be chosen rather than inherited.
"""

from __future__ import annotations

import json

import lab
import polars as pl
import search
from lab import C

RULE = [
    search.Clause("hits_before_trigger", "le", 2.0),
    search.Clause("planned_risk", "ge", 0.19),
    search.Clause("cons_len", "le", 3.0),
]
LADDER = {
    "hits<=1 & risk>=0.15 & cons<=3": [
        ("hits_before_trigger", "le", 1.0),
        ("planned_risk", "ge", 0.15),
        ("cons_len", "le", 3.0),
    ],
    "hits<=2 & risk>=0.19 & cons<=3  [PROPOSED]": [
        ("hits_before_trigger", "le", 2.0),
        ("planned_risk", "ge", 0.19),
        ("cons_len", "le", 3.0),
    ],
    "hits<=2 & risk>=0.15 & cons<=3": [
        ("hits_before_trigger", "le", 2.0),
        ("planned_risk", "ge", 0.15),
        ("cons_len", "le", 3.0),
    ],
    "hits<=2 & risk>=0.10 & cons<=3": [
        ("hits_before_trigger", "le", 2.0),
        ("planned_risk", "ge", 0.10),
        ("cons_len", "le", 3.0),
    ],
    "hits<=2 & risk>=0.15 & cons<=4": [
        ("hits_before_trigger", "le", 2.0),
        ("planned_risk", "ge", 0.15),
        ("cons_len", "le", 4.0),
    ],
    "hits<=3 & risk>=0.10 & cons<=3": [
        ("hits_before_trigger", "le", 3.0),
        ("planned_risk", "ge", 0.10),
        ("cons_len", "le", 3.0),
    ],
    "hits<=2 (alone)": [("hits_before_trigger", "le", 2.0)],
    "SHIPPED": None,
}


def cl(spec: list[tuple[str, str, float]]) -> list[search.Clause]:
    return [search.Clause(*s) for s in spec]


def apply(p: pl.DataFrame, spec: list[tuple[str, str, float]] | None) -> pl.DataFrame:
    return C.SHIPPED(p) if spec is None else search.selector(cl(spec))(p)


def main() -> None:
    p = lab.no_holdout(lab.panel())
    res: dict = {}

    print("=== 1. what is `hits_before_trigger <= 2`?")
    sel = search.selector(RULE)(p)
    print(f"  rows passing the full rule: {sel.height} of {p.height} ({sel.height / p.height:.1%})")
    hb = p.with_columns((pl.col("hits_before_trigger") <= 2).alias("early"))
    cols = [
        "trigger_et_min",
        "first_hit_et_min",
        "staleness_delay_min",
        "cycle_num",
        "cons_len",
        "planned_risk",
        "stop_pct",
        "entry_fill",
        "pole_pct",
        "ext_at_trigger",
        "score",
        "cum_dollar_vol_to_trigger",
        "n_scanner_hits",
    ]
    with pl.Config(tbl_rows=30, tbl_width_chars=190):
        print(
            hb.group_by("early")
            .agg([pl.len().alias("n")] + [pl.col(c).median().round(3).alias(c) for c in cols])
            .sort("early")
        )
    print("\n  hits_before_trigger, univariate, net R per trade at the row level:")
    with pl.Config(tbl_rows=20):
        print(
            p.with_columns(pl.min_horizontal(pl.col("hits_before_trigger"), pl.lit(8)).alias("h"))
            .group_by("h")
            .agg(
                pl.len().alias("n"),
                pl.col("r").mean().round(3).alias("gross"),
                pl.col("net_r").mean().round(3).alias("net"),
                (pl.col("r") > 0).mean().round(3).alias("win"),
            )
            .sort("h")
        )

    print("\n=== 2a. does the rule depend on the 2R target? (exits agent's variable)")
    rows = []
    for tgt in (1.0, 1.5, 2.0, 2.5, 3.0, 4.0):
        pt = lab.attach_net_r(C.fixed_target_r(lab.no_holdout(C.load_panel()), tgt))
        for label, spec in (("PROPOSED", RULE), ("pool", None)):
            s = pt if label == "pool" else search.selector(spec)(pt)  # type: ignore[arg-type]
            bk = C.build_book(s, max_per_day=2)
            rows.append(
                {
                    "target_r": tgt,
                    "rule": label,
                    "n": bk.height,
                    "net_per_trade": round(float(bk["net_r"].mean()), 3),
                    "net_total": round(float(bk["net_r"].sum()), 1),
                }
            )
    t = pl.DataFrame(rows)
    with pl.Config(tbl_rows=30, tbl_width_chars=140):
        print(t.pivot(on="rule", index="target_r", values=["n", "net_per_trade", "net_total"]))
    res["target_sensitivity"] = rows

    print("\n=== 2b. does the rule depend on the 2-a-day cap? (risk agent's variable)")
    rows = []
    for cap in (1, 2, 3, 5):
        bk = C.build_book(search.selector(RULE)(p), max_per_day=cap)
        rows.append(
            {
                "cap": cap,
                "n": bk.height,
                "tps": round(bk.height / p["dt"].n_unique(), 2),
                "net_per_trade": round(float(bk["net_r"].mean()), 3),
                "net_total": round(float(bk["net_r"].sum()), 1),
            }
        )
    with pl.Config(tbl_rows=10):
        print(pl.DataFrame(rows))
    res["cap_sensitivity"] = rows

    print("\n=== 3. the frequency / quality curve (DEV+VAL, 2R, 2-a-day)")
    curve = []
    for name, spec in LADDER.items():
        s = apply(p, spec)
        bk = C.build_book(s, max_per_day=2)
        sc = C.score(bk, sessions=p["dt"].n_unique())
        bd = C.build_book(apply(lab.dev(p), spec), max_per_day=2)
        bv = C.build_book(apply(lab.val(p), spec), max_per_day=2)
        curve.append(
            {
                "rule": name,
                "trades": sc["trades"],
                "tps": sc["trades_per_session"],
                "net_per_trade": sc["net_r_per_trade"],
                "net_r": sc["net_r"],
                "gross_per_trade": sc["r_per_trade"],
                "win": sc["win_rate"],
                "dd_net_r": sc["max_dd_net_r"],
                "dev_net_pt": round(float(bd["net_r"].mean()), 3) if bd.height else 0.0,
                "val_net_pt": round(float(bv["net_r"].mean()), 3) if bv.height else 0.0,
            }
        )
    with pl.Config(tbl_rows=20, tbl_width_chars=190):
        print(pl.DataFrame(curve).sort("tps"))
    res["curve"] = curve

    print("\n=== 4. final scorecards for the proposed rule")
    out = {
        "rule": {"hits_before_trigger_max": 2, "planned_risk_min_usd": 0.19, "cons_len_max": 3},
        "clauses": [str(c) for c in RULE],
    }
    for label, dd in (("dev", lab.dev(p)), ("val", lab.val(p))):
        bk = C.build_book(search.selector(RULE)(dd), max_per_day=2)
        s = C.score(bk, sessions=dd["dt"].n_unique())
        print(f"  {label.upper():<4} " + C.brief(s))
        out[label] = {
            k: s[k]
            for k in (
                "trades",
                "sessions_traded",
                "sessions_available",
                "trades_per_session",
                "gross_r",
                "net_r",
                "r_per_trade",
                "net_r_per_trade",
                "win_rate",
                "max_dd_net_r",
                "net_usd",
                "cap_bound",
                "mean_qty",
                "cost_r_per_trade",
            )
        }
    res["proposed"] = out

    lab.OUT.mkdir(parents=True, exist_ok=True)
    (lab.OUT / "step10_final.json").write_text(json.dumps(res, indent=1, default=str))
    print(f"\nwrote {lab.OUT / 'step10_final.json'}")


if __name__ == "__main__":
    main()
