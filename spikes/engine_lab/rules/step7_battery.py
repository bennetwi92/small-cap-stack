"""Step 7 — the anti-overfit battery, plus two structural checks.

A. **Is DEV+VAL double-positive worth anything?** Draw random 3-clause conjunctions from the same
   menu, keep the ones that look good on DEV, and count how many also look good on VAL. If that
   rate is near a coin flip, "positive on both splits" is not evidence and step 5's headline rules
   are not candidates.
B. **How much leverage does selection even have?** Under a 2/day earliest-first cap over ~18
   candidates a session, a filter changes the book only when it removes an *early* row. Measured
   as the overlap between the filtered book and the unfiltered one.
C. The required battery — `walk_forward`, `sensitivity`, `permutation_pvalue` — on the two best
   candidates from step 5, so the failure is documented rather than asserted.
"""

from __future__ import annotations

import json

import lab
import numpy as np
import polars as pl
import search
from lab import C

CAND_A = [
    search.Clause("planned_risk", "ge", 0.19),
    search.Clause("hits_before_trigger", "le", 2.0),
    search.Clause("cons_len", "le", 3.0),
    search.Clause("rvol_pole", "ge", 0.4737),
]
CAND_B = [
    search.Clause("planned_risk", "ge", 0.19),
    search.Clause("hits_before_trigger", "le", 2.0),
    search.Clause("score", "ge", 0.4991),
    search.Clause("pole_pct", "ge", 0.03486),
]


def book_of(p: pl.DataFrame, clauses: list[search.Clause]) -> pl.DataFrame:
    return C.build_book(search.selector(clauses)(p), max_per_day=2)


def part_a(p: pl.DataFrame, n_draws: int = 400) -> dict:
    print("=== A. random 3-clause conjunctions: does DEV-positive predict VAL-positive?")
    d, v = lab.dev(p), lab.val(p)
    menu = search.clause_menu(d)
    rng = np.random.default_rng(3)
    dev_pos, both_pos, rows = 0, 0, []
    for _ in range(n_draws):
        cl = [menu[i] for i in rng.choice(len(menu), size=3, replace=False)]
        bd = book_of(d, cl)
        if bd.height < 40:
            continue
        nd = float(bd["net_r"].mean())
        bv = book_of(v, cl)
        if bv.height < 15:
            continue
        nv = float(bv["net_r"].mean())
        rows.append((nd, nv))
        if nd > 0:
            dev_pos += 1
            both_pos += nv > 0
    arr = np.array(rows)
    rho = float(np.corrcoef(arr[:, 0], arr[:, 1])[0, 1]) if len(arr) > 3 else float("nan")
    print(
        f"  {len(arr)} usable random rules; {dev_pos} were DEV-positive, of which {both_pos} "
        f"({both_pos / max(dev_pos, 1):.0%}) were also VAL-positive"
    )
    print(f"  overall VAL-positive rate among all draws: {(arr[:, 1] > 0).mean():.0%}")
    print(f"  correlation between a rule's DEV net/trade and its VAL net/trade: {rho:+.3f}")
    return {
        "draws": len(arr),
        "dev_pos": dev_pos,
        "both_pos": both_pos,
        "val_pos_rate_all": float((arr[:, 1] > 0).mean()),
        "dev_val_corr": rho,
    }


def part_b(p: pl.DataFrame) -> dict:
    print("\n=== B. how much does a filter actually change the book? (2/day, earliest first)")
    base = set(C.build_book(p, max_per_day=2)["key"])
    rows = []
    for col, q in [
        ("stop_pct", 0.5),
        ("stop_pct", 0.9),
        ("entry_fill", 0.5),
        ("pole_pct", 0.5),
        ("pole_pct", 0.9),
        ("ext_at_peak", 0.9),
        ("score", 0.9),
    ]:
        cut = float(np.quantile(p[col].drop_nulls().cast(pl.Float64).to_numpy(), q))
        sel = p.filter(pl.col(col) >= cut)
        bk = set(C.build_book(sel, max_per_day=2)["key"])
        rows.append(
            {
                "rule": f"{col} >= p{int(q * 100)}",
                "rows_kept": f"{sel.height / p.height:.0%}",
                "book": len(bk),
                "shared_with_unfiltered": f"{len(bk & base) / max(len(bk), 1):.0%}",
            }
        )
    for name, cl in [("passed", [search.Clause("passed", "eq", 1.0)]), ("SHIPPED", None)]:
        sel = C.SHIPPED(p) if cl is None else search.selector(cl)(p)
        bk = set(C.build_book(sel, max_per_day=2)["key"])
        rows.append(
            {
                "rule": name,
                "rows_kept": f"{sel.height / p.height:.0%}",
                "book": len(bk),
                "shared_with_unfiltered": f"{len(bk & base) / max(len(bk), 1):.0%}",
            }
        )
    t = pl.DataFrame(rows)
    with pl.Config(tbl_rows=30, tbl_width_chars=140):
        print(t)
    return t.to_dicts()


def part_c(p: pl.DataFrame) -> dict:
    out = {}
    for name, cl in (("CAND_A", CAND_A), ("CAND_B", CAND_B)):
        print(f"\n=== C. battery for {name}: {[str(c) for c in cl]}")
        bk = book_of(p, cl)
        s = C.score(bk, sessions=p["dt"].n_unique())
        print("  DEV+VAL  " + C.brief(s))

        th = {str(c): c.cut for c in cl}

        def mk(t: dict, _cl: list[search.Clause] = cl) -> object:
            new = [search.Clause(c.col, c.op, t[str(c)]) for c in _cl]
            return search.selector(new)

        sens = C.sensitivity(p, th, mk)  # type: ignore[arg-type]
        with pl.Config(tbl_rows=20, tbl_width_chars=140):
            print(pl.DataFrame(sens))
        pv = C.permutation_pvalue(p, search.selector(cl)(p), n=300)
        print(f"  permutation p-value (same trade count, same days, random rows): {pv:.3f}")
        wf = C.walk_forward(p, lambda _t, _cl=cl: search.selector(_cl), n_blocks=6)
        print(
            f"  walk-forward as a FIXED rule: {wf['total_trades']} trades, "
            f"net {wf['total_net_r']:+.1f}R ({wf['net_r_per_trade']:+.4f}/trade), "
            f"{wf['blocks_positive']}/{wf['n_blocks']} blocks +ve"
        )
        for b in wf["blocks"]:
            print(f"     {b['from']} .. {b['to']}  n={b['trades']:>3}  net {b['net_r']:+7.2f}R")
        out[name] = {
            "clauses": [str(c) for c in cl],
            "sensitivity": sens,
            "perm_p": pv,
            "walk_forward_fixed": wf,
            "devval": {k: s[k] for k in ("trades", "net_r", "net_r_per_trade", "win_rate")},
        }
    return out


def main() -> None:
    p = lab.no_holdout(lab.panel())
    res = {"A_random_conjunctions": part_a(p), "B_book_leverage": part_b(p), "C_battery": part_c(p)}
    lab.OUT.mkdir(parents=True, exist_ok=True)
    (lab.OUT / "step7_battery.json").write_text(json.dumps(res, indent=1, default=str))


if __name__ == "__main__":
    main()
