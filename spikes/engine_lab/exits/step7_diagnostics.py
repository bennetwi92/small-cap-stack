"""Step 7 — what actually changes, month by month and trade by trade.

Four questions the surface cannot answer:
  1. Where does the gain come from — the price path, or the cost/sizing model?
  2. Which trades change verdict when the stop moves out 30%?
  3. Is it a handful of trades, or the whole book? (a capped target makes tail-dependence unlikely,
     but it has to be shown, not assumed)
  4. Is it steady month to month, and does it survive a different capacity cap?
"""

from __future__ import annotations

import json

import lab as X
import numpy as np
import polars as pl
from lab import C

SHIP = X.Bracket(target_r=2.0)
PROP = X.Bracket(buf_frac=0.30, target_r=2.0 / 1.30)


def main() -> None:
    df, p, g, paths, pre = X.load_all()
    dv = df.filter(pl.col("split") != "holdout")

    a = C.score(X.book_with_bracket(dv, SHIP, g, p, selector=C.SHIPPED))["_trades"]
    b = C.score(X.book_with_bracket(dv, PROP, g, p, selector=C.SHIPPED))["_trades"]
    print(
        f"same book both ways: {a.height} vs {b.height} trades, "
        f"keys identical: {a['key'].to_list() == b['key'].to_list()}"
    )

    print("\n### 1. gross vs net — is this a price-path effect or a cost effect?")
    for name, t in (("shipped stop", a), ("stop x1.30", b)):
        cost = (t["cost_usd"] / (t["qty"] * (t["entry_fill"] - t["stop"]))).mean()
        print(
            f"  {name:<14} gross {t['r'].mean():+.3f}R/trade  net {t['net_r'].mean():+.3f}R  "
            f"cost {cost:.3f}R  cap-bound {int((t['sized_by'] == 'cap').sum())}/{t.height}  "
            f"mean qty {t['qty'].mean():.0f}  "
            f"mean risk ${(t['qty'] * (t['entry_fill'] - t['stop'])).mean():.2f}"
        )
    print(
        "  -> of the total change, the price path is worth "
        f"{b['r'].mean() - a['r'].mean():+.3f}R and lower cost drag "
        f"{(b['net_r'].mean() - b['r'].mean()) - (a['net_r'].mean() - a['r'].mean()):+.3f}R"
    )

    print("\n### 2. which trades change verdict?")
    ra, rb = a["r"].to_numpy(), b["r"].to_numpy()
    wa, wb = ra > 0, rb > 0
    print(f"  loser -> winner : {int((~wa & wb).sum())}")
    print(f"  winner -> loser : {int((wa & ~wb).sum())}")
    print(f"  stayed winner   : {int((wa & wb).sum())}")
    print(f"  stayed loser    : {int((~wa & ~wb).sum())}")
    print(
        f"  net_r of the flipped trades: {b['net_r'].to_numpy()[~wa & wb].sum():+.1f}R "
        f"(they were {a['net_r'].to_numpy()[~wa & wb].sum():+.1f}R before)"
    )

    print("\n### 3. is it a handful of trades? (a capped target bounds each winner)")
    nr = np.sort(b["net_r"].to_numpy())
    print(f"  best trade {nr[-1]:+.2f}R, worst {nr[0]:+.2f}R, total {nr.sum():+.1f}R")
    print(
        f"  total with the 3 best trades removed : {nr[:-3].sum():+.1f}R over {len(nr) - 3} trades"
    )
    print(
        f"  total with the 5 best trades removed : {nr[:-5].sum():+.1f}R over {len(nr) - 5} trades"
    )
    print(f"  total with the 3 worst removed       : {nr[3:].sum():+.1f}R")

    print("\n### 4a. month by month (net R, shipped bracket -> proposal)")
    for t, lab in ((a, "ship"), (b, "prop")):
        t2 = t.with_columns(pl.col("dt").dt.strftime("%Y-%m").alias("mo"))
        agg = t2.group_by("mo").agg(pl.len().alias("n"), pl.col("net_r").sum().round(2)).sort("mo")
        print(
            f"  {lab}: "
            + "  ".join(
                f"{r['mo'][2:]}:{r['net_r']:+.1f}({r['n']})" for r in agg.iter_rows(named=True)
            )
        )

    print("\n### 4b. a different capacity cap")
    for cap in (1, 2, 3, 4):
        rs = X.evaluate(dv, SHIP, g, p, selector=C.SHIPPED, max_per_day=cap)
        rp = X.evaluate(dv, PROP, g, p, selector=C.SHIPPED, max_per_day=cap)
        print(
            f"  {cap}/day: shipped {rs['trades']:>3} trades {rs['net_r_per_trade']:+.3f}/trade "
            f"({rs['net_r']:+.1f}R)   proposal {rp['trades']:>3} trades "
            f"{rp['net_r_per_trade']:+.3f}/trade ({rp['net_r']:+.1f}R)"
        )

    print("\n### 5. reconciling the '87% stopped out' headline")
    book = C.build_book(C.SHIPPED(dv), max_per_day=2)
    print(f"  panel `stopped_out` on the shipped DEV+VAL book : {book['stopped_out'].mean():.1%}")
    print(f"  panel `stopped_out` over the whole population   : {dv['stopped_out'].mean():.1%}")
    print(
        "  replay, shipped bracket : "
        f"{float(a['bracket_stopped'].mean()):.1%} hit the stop before a 2R target"
    )
    print(f"  replay, proposal        : {float(b['bracket_stopped'].mean()):.1%}")

    print("\n### 6. what the stop actually is, in money terms")
    for name, t in (("shipped", a), ("proposal", b)):
        sp = ((t["entry_fill"] - t["stop"]) / t["entry_fill"]).to_numpy()
        print(
            f"  {name:<9} stop distance: p25 {np.percentile(sp, 25):.2%}  "
            f"median {np.percentile(sp, 50):.2%}  p75 {np.percentile(sp, 75):.2%}  "
            f"mean qty {t['qty'].mean():.0f} shares"
        )

    with (X.OUT / "diagnostics.json").open("w") as fh:
        json.dump(
            {
                "flips_loser_to_winner": int((~wa & wb).sum()),
                "flips_winner_to_loser": int((wa & ~wb).sum()),
                "gross_delta": round(float(b["r"].mean() - a["r"].mean()), 4),
                "net_delta": round(float(b["net_r"].mean() - a["net_r"].mean()), 4),
                "net_r_total": round(float(nr.sum()), 2),
                "net_r_ex_top3": round(float(nr[:-3].sum()), 2),
            },
            fh,
            indent=1,
        )


if __name__ == "__main__":
    main()
