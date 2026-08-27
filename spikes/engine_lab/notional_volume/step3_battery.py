"""Step 3 — anti-overfitting battery on the one cell from step 2 that moved in the right
direction: SHIPPED + cum_dollar_vol_to_trigger>=12e6 (net R/trade -0.057 vs SHIPPED's -0.070,
still net-negative). Everything else in step 2 made its base worse, so there is nothing else
here worth walk-forwarding — the battery is run on the single least-bad cell to see if even that
survives. DEV+VAL only.
"""

from __future__ import annotations

import json

import lab
import polars as pl
from spikes.engine_lab import common as C

THRESH = 12_000_000.0


def make_selector(th: dict[str, float]):
    def sel(df: pl.DataFrame) -> pl.DataFrame:
        return C.SHIPPED(df).filter(pl.col("cum_dollar_vol_to_trigger") >= th["cum_dv"])

    return sel


def main() -> None:
    df = lab.no_holdout(lab.panel())

    print("== complexity budget")
    print(
        "SHIPPED (6 existing thresholds, unchanged) + 1 new threshold: cum_dollar_vol_to_trigger"
        f" >= {THRESH:,.0f}. Budget: 1 new threshold, well inside the <=5 budget."
    )

    print("\n== walk-forward, SHIPPED baseline vs SHIPPED+cum_dv>=12e6")
    print(
        "(threshold held fixed at the step-2 value in every block -- not refitted per block; "
        "there is no positive in-sample result to refit toward)"
    )
    wf_base = C.walk_forward(df, lambda _train: C.SHIPPED, n_blocks=6, min_train_sessions=60)
    wf_new = C.walk_forward(
        df, lambda _train: make_selector({"cum_dv": THRESH}), n_blocks=6, min_train_sessions=60
    )

    def show(label: str, wf: dict) -> None:
        for b in wf["blocks"]:
            npt = round(b["net_r_per_trade"], 4)
            print(label, b["from"], b["to"], b["trades"], round(b["net_r"], 2), npt)
        print(
            f"{label} positive blocks: {wf['blocks_positive']}/{wf['n_blocks']}"
            f"  total net {wf['total_net_r']}"
        )

    show("base ", wf_base)
    show("new  ", wf_new)

    print("\n== sensitivity, +/-20% on the one threshold")
    sens = C.sensitivity(df, {"cum_dv": THRESH}, make_selector, pct=0.2)
    for r in sens:
        print(r)

    print("\n== permutation p-value (same trade count, random rows, same days)")
    selected = make_selector({"cum_dv": THRESH})(df)
    selected_book = C.build_book(selected, max_per_day=2)
    p = C.permutation_pvalue(df, selected_book, n=500)
    print(f"p = {p:.4f}  (n_trades={selected_book.height})")

    out = {
        "threshold": THRESH,
        "walk_forward_base": wf_base,
        "walk_forward_new": wf_new,
        "sensitivity": sens,
        "permutation_p": p,
    }
    (lab.OUT / "step3_battery.json").write_text(json.dumps(out, indent=2, default=str))
    print(f"\nwrote {lab.OUT / 'step3_battery.json'}")


if __name__ == "__main__":
    main()
