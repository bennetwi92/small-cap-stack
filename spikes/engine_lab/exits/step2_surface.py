"""Step 2 — the joint stop x target surface.

Stop and target are one decision. This prints the whole grid rather than two one-dimensional
slices, for four stop families and two selections (SHIPPED and the raw pre-market pool), and it
prints DEV and VAL side by side so a cell that only works on one is visible immediately.

⚠️ `target_r` is denominated in the **bracket's own** risk, so "2R" means a bigger price move once
the stop is widened. That is the point of sweeping them jointly.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import lab as X
import numpy as np
import polars as pl
from lab import C

TARGETS = [0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0, 6.0]


def raw_pool(df: pl.DataFrame) -> pl.DataFrame:
    return df


def surface(
    dv: pl.DataFrame,
    g: X.Geom,
    p: X.Packed,
    stops: list[tuple[str, dict[str, Any]]],
    *,
    selector: Callable[[pl.DataFrame], pl.DataFrame] | None,
    targets: list[float] = TARGETS,
) -> list[dict[str, Any]]:
    dev = dv.filter(pl.col("split") == "dev")
    val = dv.filter(pl.col("split") == "val")
    rows = []
    for sname, skw in stops:
        for t in targets:
            b = X.Bracket(target_r=t, **skw)
            rd = X.evaluate(dev, b, g, p, selector=selector)
            rv = X.evaluate(val, b, g, p, selector=selector)
            ra = X.evaluate(dv, b, g, p, selector=selector)
            rows.append(
                {
                    "stop": sname,
                    "target_r": t,
                    "dev_n": rd["trades"],
                    "dev_net": rd["net_r_per_trade"],
                    "val_n": rv["trades"],
                    "val_net": rv["net_r_per_trade"],
                    "all_n": ra["trades"],
                    "all_net": ra["net_r_per_trade"],
                    "all_net_total": ra["net_r"],
                    "all_gross": ra["r_per_trade"],
                    "win": ra["win_rate"],
                    "stopped": ra["pct_stopped"],
                    "same_bar": ra["pct_same_bar"],
                    "cost_r": ra.get("cost_r_per_trade", 0.0),
                    "cap_bound": ra.get("cap_bound", 0),
                    "mean_stop_pct": ra.get("mean_stop_pct", 0.0),
                }
            )
    return rows


def show(rows: list[dict[str, Any]], key: str, title: str) -> None:
    df = pl.DataFrame(rows)
    print(f"\n### {title} — {key}")
    piv = df.pivot(values=key, index="stop", on="target_r", aggregate_function="first")
    hdr = "  " + f"{'stop':<22}" + "".join(f"{t:>8}" for t in TARGETS)
    print(hdr)
    for r in piv.iter_rows(named=True):
        line = f"  {r['stop']:<22}"
        for t in TARGETS:
            v = r[str(t)]
            line += f"{v:>8.3f}" if isinstance(v, float) else f"{'':>8}"
        print(line)


BUFFERS: list[tuple[str, dict[str, Any]]] = [
    ("cons low (shipped)", {}),
    ("cons +10%", {"buf_frac": 0.10}),
    ("cons +25%", {"buf_frac": 0.25}),
    ("cons +50%", {"buf_frac": 0.50}),
    ("cons +75%", {"buf_frac": 0.75}),
    ("cons +100%", {"buf_frac": 1.00}),
    ("cons +2 ticks", {"buf_ticks": 2}),
    ("cons +5 ticks", {"buf_ticks": 5}),
    ("cons -25% (tighter)", {"buf_frac": -0.25}),
    ("cons -50% (tighter)", {"buf_frac": -0.50}),
]

PCT_STOPS: list[tuple[str, dict[str, Any]]] = [
    (f"{p:.0%} of entry", {"stop_pct_entry": p}) for p in (0.02, 0.03, 0.04, 0.05, 0.07, 0.10, 0.15)
]

POLE_STOPS: list[tuple[str, dict[str, Any]]] = [
    (f"{f:.2f} x pole", {"stop_pole_frac": f}) for f in (0.25, 0.4, 0.5, 0.75, 1.0)
]

CLAMPS: list[tuple[str, dict[str, Any]]] = [
    ("cons, floor 3%", {"floor_pct": 0.03}),
    ("cons, floor 4%", {"floor_pct": 0.04}),
    ("cons, floor 5%", {"floor_pct": 0.05}),
    ("cons, ceil 12%", {"ceil_pct": 0.12}),
    ("cons, ceil 10%", {"ceil_pct": 0.10}),
    ("cons, ceil 8%", {"ceil_pct": 0.08}),
    ("cons, ceil 6%", {"ceil_pct": 0.06}),
    ("cons, 4%..10%", {"floor_pct": 0.04, "ceil_pct": 0.10}),
    ("cons, 3%..8%", {"floor_pct": 0.03, "ceil_pct": 0.08}),
    ("cons+25%, ceil 10%", {"buf_frac": 0.25, "ceil_pct": 0.10}),
]


def main() -> None:
    df, p, g, paths, pre = X.load_all()
    dv = df.filter(pl.col("split") != "holdout")
    out: dict[str, Any] = {}
    for selname, sel in (("SHIPPED", C.SHIPPED), ("RAW POOL", None)):
        print(f"\n{'=' * 100}\n== SELECTION: {selname}\n{'=' * 100}")
        allrows = []
        for gname, stops in (
            ("buffer off the consolidation low", BUFFERS),
            ("a flat percentage of entry", PCT_STOPS),
            ("a fraction of the pole", POLE_STOPS),
            ("clamped consolidation low", CLAMPS),
        ):
            rows = surface(dv, g, p, stops, selector=sel)
            allrows += [{**r, "family": gname} for r in rows]
            show(rows, "all_net", f"{selname} / {gname} — NET R per trade, DEV+VAL")
            show(rows, "dev_net", f"{selname} / {gname} — NET R per trade, DEV only")
            show(rows, "val_net", f"{selname} / {gname} — NET R per trade, VAL only")
        out[selname] = allrows
        best = sorted([r for r in allrows if r["all_n"] >= 60], key=lambda r: -r["all_net"])[:12]
        print(f"\n  top cells for {selname} (>=60 trades, DEV+VAL):")
        for r in best:
            print(
                f"    {r['stop']:<22} tgt {r['target_r']:<5} n={r['all_n']:<4} "
                f"net/tr {r['all_net']:+.3f} (dev {r['dev_net']:+.3f} / val {r['val_net']:+.3f})  "
                f"gross {r['all_gross']:+.3f} win {r['win']:.1%} stop {r['stopped']:.1%} "
                f"cost {r['cost_r']:.3f}R"
            )
    X.OUT.mkdir(parents=True, exist_ok=True)
    with (X.OUT / "surface.json").open("w") as fh:
        json.dump(out, fh, indent=1, default=float)
    print(f"\nwrote {X.OUT / 'surface.json'}")
    _ = np


if __name__ == "__main__":
    main()
