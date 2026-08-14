"""Step 4 — where the target really is.

Step 3 says the best target sits at t = 2.0 consolidation-ranges above entry and that t = 2.5 falls
off a cliff. Two things to check:

1. Is the cliff a cliff, or an artefact of a coarse grid? Fine scan, 0.1 steps.
2. Is "2 x the consolidation range" the right *unit* at all? The consolidation range is a property
   of the setup; a move might instead be sized by the stock's own volatility (% of entry), by the
   pole that produced it, or by the pre-market high overhead. All three are known at the trigger.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import lab as X
import numpy as np
import polars as pl
from lab import C

M = 1.25  # the stop multiple step 3 settled on, held fixed while the target moves


def line(name: str, r: dict[str, Any]) -> str:
    return (
        f"  {name:<26} n={r['trades']:<4} net {r['net_r_per_trade']:+.3f} "
        f"(dev {r.get('split', {}).get('dev', {}).get('net_r_per_trade', 0):+.3f} / "
        f"val {r.get('split', {}).get('val', {}).get('net_r_per_trade', 0):+.3f})  "
        f"gross {r['r_per_trade']:+.3f}  win {r['win_rate']:.1%}  "
        f"stop {r.get('pct_stopped', 0):.1%}  totNet {r['net_r']:+.1f}R"
    )


def fine_target(dv: pl.DataFrame, g: X.Geom, p: X.Packed, sel: Callable | None, name: str) -> None:
    print(f"\n### {name}: fine target scan, stop fixed at m={M} x consolidation range")
    print(
        f"  {'t (x C)':<10}{'R mult':>8}{'n':>5}{'net/tr':>9}"
        f"{'dev':>9}{'val':>9}{'gross':>9}{'win':>8}"
    )
    for t in np.arange(1.0, 3.55, 0.1):
        b = X.Bracket(buf_frac=M - 1.0, target_r=float(t) / M)
        r = X.evaluate(dv, b, g, p, selector=sel)
        sp = r.get("split", {})
        print(
            f"  {t:<10.1f}{t / M:>8.2f}{r['trades']:>5}{r['net_r_per_trade']:>9.3f}"
            f"{sp.get('dev', {}).get('net_r_per_trade', 0):>9.3f}"
            f"{sp.get('val', {}).get('net_r_per_trade', 0):>9.3f}"
            f"{r['r_per_trade']:>9.3f}{r['win_rate']:>8.1%}"
        )


def other_units(
    dv: pl.DataFrame, g: X.Geom, p: X.Packed, sel: Callable | None, name: str
) -> list[dict[str, Any]]:
    rows = []
    print(f"\n### {name}: targets in units that are NOT the consolidation range (stop m={M})")
    print("  -- target = a fixed percentage of the entry price --")
    for pct in (0.02, 0.03, 0.04, 0.05, 0.06, 0.08, 0.10, 0.125, 0.15, 0.20, 0.30):
        b = X.Bracket(buf_frac=M - 1.0, target_mode="pct", target_pct=pct)
        r = X.evaluate(dv, b, g, p, selector=sel)
        print(line(f"{pct:.1%} of entry", r))
        rows.append({"unit": "pct_entry", "v": pct, **_pick(r)})
    print("  -- target = a multiple of the pole height --")
    for f in (0.25, 0.4, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0):
        b = X.Bracket(buf_frac=M - 1.0, target_mode="pole", target_pole_frac=f)
        r = X.evaluate(dv, b, g, p, selector=sel)
        print(line(f"{f:.2f} x pole", r))
        rows.append({"unit": "pole", "v": f, **_pick(r)})
    print("  -- target = the pre-market high so far, extended by a factor --")
    for f in (0.5, 0.75, 1.0, 1.25, 1.5, 2.0):
        b = X.Bracket(
            buf_frac=M - 1.0, target_mode="prehigh", target_pre_high_frac=f, target_r=2.0 / M
        )
        r = X.evaluate(dv, b, g, p, selector=sel)
        print(line(f"pre-high x {f:.2f}", r))
        rows.append({"unit": "prehigh", "v": f, **_pick(r)})
    print("  -- target = whichever of (2C, pre-market high) comes first --")
    for f in (1.0, 1.25, 1.5):
        b = X.Bracket(
            buf_frac=M - 1.0,
            target_mode="min",
            target_r=2.0 / M,
            target_pre_high_frac=f,
        )
        r = X.evaluate(dv, b, g, p, selector=sel)
        print(line(f"min(2C, preH x {f:.2f})", r))
        rows.append({"unit": "min_prehigh", "v": f, **_pick(r)})
    return rows


def _pick(r: dict[str, Any]) -> dict[str, Any]:
    sp = r.get("split", {})
    return {
        "n": r["trades"],
        "net": r["net_r_per_trade"],
        "dev": sp.get("dev", {}).get("net_r_per_trade", 0),
        "val": sp.get("val", {}).get("net_r_per_trade", 0),
        "gross": r["r_per_trade"],
        "win": r["win_rate"],
    }


def prehigh_context(dv: pl.DataFrame, g: X.Geom, p: X.Packed, pre: pl.DataFrame) -> None:
    """Is the pre-market high even above the entry? If it usually isn't, the idea is dead."""
    sel = C.SHIPPED(dv)
    book = C.build_book(sel, max_per_day=2)
    d = book.join(pre, on="key", how="left")
    e = d["entry_fill"].to_numpy()
    ph = d["pre_high"].to_numpy()
    cons = e - d["stop"].to_numpy()
    above = (ph - e) / np.where(cons > 0, cons, np.nan)
    print("\n### how far above entry is the pre-market high, in consolidation ranges?")
    print(f"  above entry at all: {np.nanmean(ph > e):.1%} of the shipped book's trades")
    for q in (10, 25, 50, 75, 90):
        print(f"  p{q:<3} {np.nanpercentile(above, q):+.2f} C")


def main() -> None:
    df, p, g, paths, pre = X.load_all()
    dv = df.filter(pl.col("split") != "holdout")
    out: dict[str, Any] = {}
    for name, sel in (("SHIPPED", C.SHIPPED), ("RAW POOL", None)):
        fine_target(dv, g, p, sel, name)
        out[name] = other_units(dv, g, p, sel, name)
    prehigh_context(dv, g, p, pre)
    with (X.OUT / "targets.json").open("w") as fh:
        json.dump(out, fh, indent=1, default=float)


if __name__ == "__main__":
    main()
