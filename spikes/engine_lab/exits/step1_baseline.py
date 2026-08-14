"""Step 1 — reconcile with the published baseline, then look at the raw shape of the problem.

Two things have to be true before any sweep is worth running:

1. The bracket replay reproduces the shipped book's headline (103 trades, +11.0R gross, 36.9% win).
   `fixed_target_r()` books every non-winner at exactly -1R; the replay books the ones that never
   touched the stop at their 16:00 close. The gap between the two is itself a finding.
2. DEV and VAL are stated separately, because that is all anyone is allowed to fit on.

⚠️ The whole-population totals below are the SHIPPED bracket, which is a published number, not a
proposal being evaluated. Even so the per-split breakdown is suppressed to `dev`/`val` so a re-run
of this file cannot put a holdout figure on screen. Nothing in this study is fitted or checked on
HOLDOUT.
"""

from __future__ import annotations

import lab as X
import numpy as np
import polars as pl
from lab import C


def main() -> None:
    df, p, g, paths, pre = X.load_all()
    dv = df.filter(pl.col("split") != "holdout")
    print(f"DEV+VAL population: {dv.height} rows / {dv['dt'].n_unique()} sessions")

    def no_holdout(res: dict) -> dict:
        """Drop the holdout block before anything is printed.

        `source == "live"` is dropped too: live covers 2026-07-01..2026-08-13, which IS the
        holdout window, so a per-source breakdown is a holdout breakdown wearing a different hat.
        """
        out = dict(res)
        for key, drop in (("split", "holdout"), ("source", "live")):
            if isinstance(out.get(key), dict):
                out[key] = {k: v for k, v in out[key].items() if k != drop}
        return out

    print("\n--- the published baseline, whole-population totals (per-split: dev/val only) ---")
    print(C.summarise(no_holdout(C.baseline(df)), "SHIPPED 2/day 2R via fixed_target_r"))

    print("\n--- the same book, replayed bar by bar (this is my engine) ---")
    res = X.evaluate(df, X.Bracket(target_r=2.0), g, p, selector=C.SHIPPED)
    print(C.summarise(no_holdout(res), "SHIPPED 2/day 2R via replay_bracket"))
    print(
        f"    stopped {res['pct_stopped']:.1%}  same-bar {res['pct_same_bar']:.1%}  "
        f"open@09:30 {res['pct_open_930']:.1%}  fill>bar-high {res['pct_fill_above_high']:.1%}"
    )

    print("\n--- DEV+VAL only, which is all I may fit on ---")
    for name, sel in (("SHIPPED", C.SHIPPED), ("RAW POOL", None)):
        r = X.evaluate(dv, X.Bracket(target_r=2.0), g, p, selector=sel)
        print(f"{name:<9} " + C.brief(r) + f"  stopped {r['pct_stopped']:.1%}")

    print("\n--- how far does price actually run, in shipped-stop R? (DEV+VAL, shipped book) ---")
    book = X.book_with_bracket(
        dv, X.Bracket(target_r=None, target_mode="none"), g, p, selector=C.SHIPPED
    )
    mfe = book["bracket_max_r"].to_numpy()
    for q in (10, 25, 50, 60, 70, 75, 80, 90, 95, 99):
        print(f"    p{q:<3} MFE {np.percentile(mfe, q):+7.2f}R")
    print(
        f"    mean {mfe.mean():+.2f}R   frac >= 1R {np.mean(mfe >= 1):.1%}   "
        f">= 2R {np.mean(mfe >= 2):.1%}   >= 3R {np.mean(mfe >= 3):.1%}"
    )

    print("\n--- and where does the stop sit today? (DEV+VAL, shipped selection) ---")
    sel = C.SHIPPED(dv)
    sp = sel["stop_pct"].to_numpy()
    for q in (5, 25, 50, 75, 95):
        print(f"    p{q:<3} stop {np.percentile(sp, q):.2%} of entry")


if __name__ == "__main__":
    main()
