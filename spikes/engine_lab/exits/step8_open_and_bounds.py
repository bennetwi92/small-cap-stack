"""Step 8 — the 09:30 open, and what the forbidden exits would have been worth.

These are pre-market entries. A bracket left alone can still be open when the regular session
starts, and the open is the one moment where a stop can be gapped through rather than filled. That
risk is currently unexamined, so it gets measured here: how many trades are still live at the bell,
what the 09:30 open does to them, and whether a mechanical flatten-at-the-open is worth its cost.

The second half prices the exits the user has ruled out (trailing, breakeven, scale-out, time stop)
as **clearly-labelled upper bounds**. They are not proposable; they say how much is being left on
the table by insisting on one OCA order.
"""

from __future__ import annotations

import json

import lab as X
import numpy as np
import polars as pl
from lab import C

M, T = 1.30, 2.00


def main() -> None:
    df, p, g, paths, pre = X.load_all()
    dv = df.filter(pl.col("split") != "holdout")
    prop = X.Bracket(buf_frac=M - 1.0, target_r=T / M)

    # ---- 09:30 ------------------------------------------------------------------------------
    book = C.build_book(C.SHIPPED(dv), max_per_day=2)
    keys = [k for k in book["key"].to_list() if k in p.idx]
    sp = p.sub(keys)
    j = np.array([p.idx[k] for k in keys])
    gg = X.Geom(g.entry_fill[j], g.cons_stop[j], g.pole_h[j], g.pre_high[j])
    res = X.resolve(prop, gg, sp)

    open930 = res["open_at_930"]
    has930 = sp.i930 >= 0
    print("### the 09:30 open")
    print(f"  book: {len(keys)} trades, {int(has930.sum())} of them have a 09:30 bar in the path")
    print(
        f"  still open when the bell rings: {int(open930.sum())} ({open930.mean():.1%} of the book)"
    )
    held = res["bars_held"]
    print(
        f"  bars held (5-min): median {np.median(held):.0f}  "
        f"p90 {np.percentile(held, 90):.0f}  max {held.max():.0f}"
    )
    print(
        f"  resolved within 3 bars (15 min): {(held <= 3).mean():.1%}, "
        f"within 12 bars (1 h): {(held <= 12).mean():.1%}"
    )

    # of the ones still open, where does the 09:30 open print relative to the stop and target?
    if open930.any():
        o = sp.open_930[open930]
        st = res["stop"][open930]
        tg = res["target"][open930]
        en = res["entry"][open930]
        rr = res["r"][open930]
        # everything expressed in R against that trade's own entry and stop
        worse = [f"{(x - s) / (e - s):+.2f}R" for x, s, e in zip(o, st, en, strict=True)]
        at_open = [f"{(x - e) / (e - s):+.2f}R" for x, e, s in zip(o, en, st, strict=True)]
        tgt_r = [f"{(x - e) / (e - s):+.2f}R" for x, e, s in zip(tg, en, st, strict=True)]
        print(
            f"  of the {int(open930.sum())} live at the bell: "
            f"{int((o < st).sum())} opened BELOW the stop (a gap-through — the stop would have "
            f"filled worse than {'; '.join(worse)})"
        )
        print(f"    09:30 open vs entry: {', '.join(at_open)}")
        print(
            f"    eventual outcome  : {', '.join(f'{x:+.2f}R' for x in rr)}  "
            f"(target sat at {', '.join(tgt_r)})"
        )

    r_hold = X.evaluate(dv, prop, g, p, selector=C.SHIPPED)
    r_flat = X.evaluate(
        dv, X.Bracket(buf_frac=M - 1.0, target_r=T / M, exit_at_930=True), g, p, selector=C.SHIPPED
    )
    print(
        f"\n  leave it running to 16:00 : {r_hold['net_r']:+.1f}R "
        f"({r_hold['net_r_per_trade']:+.3f}/trade, win {r_hold['win_rate']:.1%})"
    )
    print(
        f"  flatten at the 09:30 open : {r_flat['net_r']:+.1f}R "
        f"({r_flat['net_r_per_trade']:+.3f}/trade, win {r_flat['win_rate']:.1%})"
    )
    print(
        f"  cost of the safety measure: {r_flat['net_r'] - r_hold['net_r']:+.1f}R "
        f"over {r_hold['trades']} trades"
    )

    # ---- upper bounds -----------------------------------------------------------------------
    print("\n### upper bounds — exits ruled out by the user, priced for context only")
    mfe_book = X.book_with_bracket(
        dv, X.Bracket(buf_frac=M - 1.0, target_r=None, target_mode="none"), g, p, selector=C.SHIPPED
    )
    mfe = mfe_book["bracket_max_r"].to_numpy()
    tR = T / M
    rows = {"proposed simple bracket (gross)": float(r_hold["r_per_trade"])}
    rows["perfect foresight: exit at the MFE"] = float(np.maximum(mfe, -1.0).mean())
    for trig in (0.5, 0.75, 1.0, 1.25, 1.5):
        r = np.where(mfe >= tR, tR, np.where(mfe >= trig, 0.0, -1.0))
        rows[f"breakeven stop once +{trig}R is seen"] = float(r.mean())
    for trig in (0.75, 1.0, 1.5):
        # half off at `trig`, the rest runs to the target or back to the stop
        r = np.where(
            mfe >= tR,
            tR,
            np.where(mfe >= trig, 0.5 * trig - 0.5, -1.0),
        )
        rows[f"scale half out at +{trig}R"] = float(r.mean())
    for nb in (3, 6, 12, 24):
        # a time stop: out at the close of bar nb if neither leg fired. Needs the bar path.
        b = X.Bracket(buf_frac=M - 1.0, target_r=tR)
        rows[f"time stop after {nb} bars ({nb * 5} min)"] = _time_stop(dv, g, p, b, nb)
    for k, v in rows.items():
        print(f"  {k:<40} {v:+.3f} gross R/trade")

    with (X.OUT / "open930_and_bounds.json").open("w") as fh:
        json.dump(
            {
                "open_at_930_n": int(open930.sum()),
                "open_at_930_pct": round(float(open930.mean()), 4),
                "hold_net_r": r_hold["net_r"],
                "flatten_net_r": r_flat["net_r"],
                "flatten_net_r_per_trade": r_flat["net_r_per_trade"],
                "upper_bounds_gross_r_per_trade": {k: round(v, 4) for k, v in rows.items()},
                "bars_held_median": float(np.median(held)),
                "resolved_within_1h": round(float((held <= 12).mean()), 4),
            },
            fh,
            indent=1,
        )


def _time_stop(dv: pl.DataFrame, g: X.Geom, p: X.Packed, b: X.Bracket, nb: int) -> float:
    """Gross R if the position is closed at the close of bar `nb` when neither leg has fired."""
    book = C.build_book(C.SHIPPED(dv), max_per_day=2)
    keys = [k for k in book["key"].to_list() if k in p.idx]
    sp = p.sub(keys)
    j = np.array([p.idx[k] for k in keys])
    gg = X.Geom(g.entry_fill[j], g.cons_stop[j], g.pole_h[j], g.pre_high[j])
    res = X.resolve(b, gg, sp)
    r = res["r"].copy()
    late = (~res["stopped"] & ~res["won"]) | (res["bars_held"] > nb)
    # for those, exit at the close of bar min(nb, n-1) -- approximated by that bar's high/low mid
    idx = np.minimum(nb, sp.n - 1)
    px = np.where(
        np.isfinite(sp.H[np.arange(len(idx)), idx]),
        (sp.H[np.arange(len(idx)), idx] + sp.L[np.arange(len(idx)), idx]) / 2.0,
        res["entry"],
    )
    alt = (px - res["entry"]) / np.where(res["risk"] > 0, res["risk"], 1.0)
    r = np.where(late, alt, r)
    return float(r.mean())


if __name__ == "__main__":
    main()
