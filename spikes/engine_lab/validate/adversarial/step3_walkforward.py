"""Step 3 — walk-forward on the PROCEDURE, not the rule.

`common.walk_forward()` takes a `fit`. Four fits:
  1. FIXED       — the claim's rule, never refitted. The flattering version; the control.
  2. GRID        — refit the three in-play thresholds on each training window over the
                   round-number grid. This is "would this method have found a profitable rule as
                   the record accumulated?"
  3. NARROW      — refit by greedy search over the claim's own three features (decile cuts).
  4. WIDE        — refit by greedy search over the whole feature menu.
The thresholds each window chose are printed: if they wander, the stability claim is dead.
"""

from __future__ import annotations

import itertools
from collections.abc import Callable

import lab as L
import numpy as np
import polars as pl
import search as S
from lab import C

RUNUPS = [0.05, 0.10, 0.15, 0.20, 0.25, 0.30]
RVOLS = [1.0, 1.5, 2.0, 3.0, 5.0]
SHARES = [20e6, 50e6, 100e6, 200e6, 500e6]

CHOSEN: list[dict] = []


def _stats(sel: pl.DataFrame) -> tuple[float, int]:
    bk = L.book_of(sel)
    if bk.is_empty():
        return -np.inf, 0
    v = L.fast_net_r(bk["entry_fill"].to_numpy(), bk["stop"].to_numpy(), bk["r"].to_numpy())
    v = v[~np.isnan(v)]
    return (float(v.mean()), len(v)) if len(v) else (-np.inf, 0)


def fit_fixed(_train: pl.DataFrame) -> Callable[[pl.DataFrame], pl.DataFrame]:
    CHOSEN.append({"fit": "FIXED", "thresholds": dict(L.IN_PLAY)})
    return L.claim_selector


def fit_grid(
    train: pl.DataFrame, *, min_trades: int = 15
) -> Callable[[pl.DataFrame], pl.DataFrame]:
    sh = C.SHIPPED(train)
    best, bth = -np.inf, dict(L.IN_PLAY)
    for ru, rv, s in itertools.product(RUNUPS, RVOLS, SHARES):
        v, n = _stats(L.in_play(sh, runup=ru, rvol=rv, shares=s))
        if n >= min_trades and v > best:
            best, bth = v, {"runup": ru, "rvol": rv, "shares": s}
    CHOSEN.append({"fit": "GRID", "thresholds": bth, "train_score": round(best, 4)})
    return lambda d: L.in_play(C.SHIPPED(d), **bth)


def _greedy_fit(train: pl.DataFrame, *, narrow: bool, min_trades: int = 15, tag: str):
    pop = S.Pop(train)
    base = S.shipped_mask(train)
    if narrow:
        menu = []
        for col, op in (
            ("runup_pre_appearance", "ge"),
            ("rvol_pole", "ge"),
            ("shares_outstanding", "le"),
        ):
            x = pop.feat[col][base]
            x = x[~np.isnan(x)]
            if len(x) < 10:
                continue
            for q in S.DECILES:
                menu.append(S.Clause(col, op, float(np.quantile(x, q))))
    else:
        menu = pop.menu(base)
    cls, v, n = S.greedy(pop, base, menu=menu, max_clauses=3, min_trades=min_trades)
    CHOSEN.append(
        {
            "fit": tag,
            "clauses": [str(c) for c in cls],
            "train_score": round(v, 4),
            "train_trades": n,
        }
    )

    def sel(d: pl.DataFrame) -> pl.DataFrame:
        out = C.SHIPPED(d)
        for cl in cls:
            e = (
                (pl.col(cl.col) >= cl.cut)
                if cl.op == "ge"
                else (pl.col(cl.col) <= cl.cut)
                if cl.op == "le"
                else (pl.col(cl.col).cast(pl.Float64) == cl.cut)
            )
            out = out.filter(e.fill_null(False))
        return out

    return sel


def main() -> None:
    df = L.load_panel_checked()
    out: dict = {}

    for tag, fit in (
        ("FIXED (claim rule, never refitted)", fit_fixed),
        ("GRID (refit 3 thresholds)", fit_grid),
        (
            "NARROW (greedy, claim's 3 features)",
            lambda t: _greedy_fit(t, narrow=True, tag="NARROW"),
        ),
        ("WIDE (greedy, full menu)", lambda t: _greedy_fit(t, narrow=False, tag="WIDE")),
    ):
        CHOSEN.clear()
        L.hr(f"3. WALK-FORWARD — {tag}")
        wf = C.walk_forward(df, fit, n_blocks=6, min_train_sessions=60, max_per_day=2)
        for b, ch in zip(wf["blocks"], CHOSEN, strict=False):
            pick = ch.get("thresholds") or ch.get("clauses")
            print(
                f"  {b['from']} .. {b['to']}  train={b['train_sessions']:>3}s  "
                f"trades={b['trades']:>3}  netR={b['net_r']:+7.2f}  "
                f"per={b['net_r_per_trade']:+.3f}  win={b['win_rate']:.2f}   chose {pick}"
            )
        print(
            f"  TOTAL: {wf['total_trades']} trades  net {wf['total_net_r']:+.2f}R  "
            f"({wf['net_r_per_trade']:+.4f}/trade)  blocks positive "
            f"{wf['blocks_positive']}/{wf['n_blocks']}"
        )
        out[tag] = {**wf, "chosen": list(CHOSEN)}

    # ---------------------------------------------- how much do the refitted thresholds wander?
    L.hr("3b. Threshold stability across windows (GRID fit)")
    ch = list(out["GRID (refit 3 thresholds)"]["chosen"])
    for k in ("runup", "rvol", "shares"):
        vals = [c["thresholds"][k] for c in ch]
        print(f"  {k:<8} chose {vals}  (claim uses {L.IN_PLAY[k]:g})")
    out["grid_threshold_paths"] = {
        k: [c["thresholds"][k] for c in ch] for k in ("runup", "rvol", "shares")
    }

    # ------------------------------------------------------ a walk-forward of SHIPPED, for scale
    L.hr("3c. Control: walk-forward of SHIPPED alone (no in-play filter, no fitting)")
    wf = C.walk_forward(df, lambda _t: C.SHIPPED, n_blocks=6, min_train_sessions=60)
    for b in wf["blocks"]:
        print(
            f"  {b['from']} .. {b['to']}  trades={b['trades']:>3}  netR={b['net_r']:+7.2f}  "
            f"per={b['net_r_per_trade']:+.3f}"
        )
    print(
        f"  TOTAL: {wf['total_trades']} trades  net {wf['total_net_r']:+.2f}R "
        f"({wf['net_r_per_trade']:+.4f}/trade)  positive {wf['blocks_positive']}/{wf['n_blocks']}"
    )
    out["control_shipped"] = wf

    L.write("step3_walkforward.json", out)
    print("\nwrote step3_walkforward.json")


if __name__ == "__main__":
    main()
