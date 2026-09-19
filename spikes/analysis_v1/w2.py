"""Analysis v1 — W2 (#738): entry mechanics, exit interiors and the cost floor.

Plan: `research/analysis-plan-2-execution.md` §3–§9; contract: `research/analysis-protocol.md`.
Pre-registered on the trials ledger (#735) before anything here was run.

    .venv/bin/python spikes/analysis_v1/w2.py e1      # fill realism                    3 trials
    .venv/bin/python spikes/analysis_v1/w2.py e2      # 3 mechanics x 4 families       12 trials
    .venv/bin/python spikes/analysis_v1/w2.py e3e4    # F3 and F4 interiors            12 trials
    .venv/bin/python spikes/analysis_v1/w2.py e5      # stop_pct selection-dependence   4 trials
    .venv/bin/python spikes/analysis_v1/w2.py null    # the matched-intensity nulls      0 trials
    .venv/bin/python spikes/analysis_v1/w2.py e6e7    # walk-forward + the CHECK score  4 trials
    .venv/bin/python spikes/analysis_v1/w2.py viability   # the §9.2 table               0 trials

**This module measures. It does not conclude** (protocol §13.4). Every command writes numbers to
`data/spikes/w2/` and prints them; the interpretation is a separate pass.

It imports `stage0.py` — the one implementation of the splits, Filter A, the four exit families,
the per-leg cost model and the §6.2 report — and does not fork it. It never reads or runs
`SHIPPED` / `baseline()` (protocol §1.1), and it never touches HOLDOUT.
"""

from __future__ import annotations

import json
import math
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from spikes.analysis_v1 import stage0  # noqa: E402
from spikes.analysis_v1.stage0 import Leg  # noqa: E402

PUB = REPO / "data/spikes/panel-v1/publish"
TAPES = PUB / "tapes"
OUT = REPO / "data/spikes/w2"
OUT.mkdir(parents=True, exist_ok=True)

TICK = 0.01
PREMARKET_END_ETMIN = 570
B_NULL = 200
SEED = 20260918

#: S0-F's fixed FIT `stop_pct` decile edges (panel-v1-spec.md §6). Frozen, never recomputed.
STOP_PCT_EDGES = [0.0181, 0.0288, 0.0382, 0.0475, 0.0568, 0.0678, 0.0824, 0.1033, 0.1426]


# =================================================================================================
# Exit families — the protocol §10 four, plus W2's parameterised interiors (E3, E4)
# =================================================================================================
def exit_runner_w2(
    path: np.ndarray, entry_fill: float, stop: float, arm_r: float = 1.0, lookback: int = 1
) -> list[Leg]:
    """F3 with W2's two interior knobs: the arming threshold and the trailing reference.

    `lookback=1` is protocol §10's "first close below the prior bar's low" and reproduces
    `stage0.exit_runner` exactly; `lookback=2` trails the lowest low of the prior two bars.
    """
    h, lo, cl = path[:, 1], path[:, 2], path[:, 3]
    entry = max(entry_fill, float(path[0, 0]))
    risk = entry - stop
    if risk <= 0:
        return []
    max_high = -math.inf
    for k in range(len(path)):
        if lo[k] <= stop:
            return [Leg(1.0, stop, False, -1.0)]
        max_high = max(max_high, float(h[k]))
        armed = (max_high - entry) / risk >= arm_r
        if armed and k >= lookback:
            ref = float(lo[k - lookback : k].min())
            if cl[k] < ref:
                return [Leg(1.0, float(cl[k]), False, (float(cl[k]) - entry) / risk)]
    return [Leg(1.0, float(cl[-1]), False, (float(cl[-1]) - entry) / risk)]


def exit_hybrid_w2(
    path: np.ndarray,
    entry_fill: float,
    stop: float,
    frac: float = 0.5,
    point_r: float = 1.0,
    arm_r: float = 1.0,
    lookback: int = 1,
) -> list[Leg]:
    """F4 with W2's two interior knobs: the scale fraction and the scale point.

    `frac=0.5, point_r=1.0` is protocol §10's hybrid and reproduces `stage0.exit_hybrid` exactly.
    Same conservative same-bar conventions: the original stop is checked before the scale target,
    and on the bar the target fills, a low at or below entry stops the remainder at break-even.
    """
    h, lo, cl = path[:, 1], path[:, 2], path[:, 3]
    entry = max(entry_fill, float(path[0, 0]))
    risk = entry - stop
    if risk <= 0:
        return []
    tgt = entry + point_r * risk
    rest = 1.0 - frac
    for k in range(len(path)):
        if lo[k] <= stop:
            return [Leg(1.0, stop, False, -1.0)]
        if h[k] >= tgt:
            scaled = Leg(frac, tgt, True, point_r)
            for m in range(k, len(path)):
                if lo[m] <= entry:
                    return [scaled, Leg(rest, entry, False, 0.0)]
                if m >= lookback and cl[m] < float(lo[m - lookback : m].min()):
                    return [scaled, Leg(rest, float(cl[m]), False, (float(cl[m]) - entry) / risk)]
            return [scaled, Leg(rest, float(cl[-1]), False, (float(cl[-1]) - entry) / risk)]
    return [Leg(1.0, float(cl[-1]), False, (float(cl[-1]) - entry) / risk)]


#: The E2 grid's exit axis — protocol §10, unchanged.
FAMILIES: dict[str, Callable[[np.ndarray, float, float], list[Leg]]] = dict(stage0.FAMILIES)


def e3_grid() -> dict[str, Callable[..., list[Leg]]]:
    """F3 interior: arming threshold x trailing reference. 6 points."""
    return {
        f"F3[arm={a},ref={r}bar]": (lambda p, e, s, a=a, r=r: exit_runner_w2(p, e, s, a, r))
        for a in (0.5, 1.0, 1.5)
        for r in (1, 2)
    }


def e4_grid() -> dict[str, Callable[..., list[Leg]]]:
    """F4 interior: scale fraction x scale point. 6 points."""
    return {
        f"F4[frac={n}/3,at={t}R]" if n != 3 else f"F4[frac=1/2,at={t}R]": (
            lambda p, e, s, f=f, t=t: exit_hybrid_w2(p, e, s, f, t)
        )
        for n, f in ((1, 1 / 3), (3, 0.5), (2, 2 / 3))
        for t in (1.0, 1.5)
    }


# =================================================================================================
# Data — the published, hash-verified artefacts only
# =================================================================================================
class Data:
    """Everything W2 reads, loaded once. FIT + CHECK only; HOLDOUT never enters."""

    def __init__(self) -> None:
        self.panel = stage0.load_panel_v1(PUB / "panel-v1.parquet").filter(
            pl.col("split").is_in(["FIT", "CHECK"])
        )
        self.sessions = stage0.load_sessions_v1(PUB / "sessions-v1.parquet")
        paths = pl.read_parquet(PUB / "paths-v1" / "paths-v1.parquet").sort(["key", "bar"])
        self.path: dict[str, np.ndarray] = {}
        self.path_etmin: dict[str, np.ndarray] = {}
        for (k,), g in paths.group_by(["key"]):
            self.path[str(k)] = g.select("open", "high", "low", "close").to_numpy()
            self.path_etmin[str(k)] = g["etmin"].to_numpy()
        self.filter_a_constant = stage0.filter_a_constant(
            stage0.load_panel_v1(PUB / "panel-v1.parquet")
        )
        # 1-minute tape, indexed by opportunity_id, pre-market only by construction
        b1 = pl.read_parquet(TAPES / "recon_bars_1m_fitcheck.parquet").with_columns(
            (
                pl.col("bar_start_utc")
                .dt.convert_time_zone("America/New_York")
                .dt.hour()
                .cast(pl.Int32)
                * 60
                + pl.col("bar_start_utc")
                .dt.convert_time_zone("America/New_York")
                .dt.minute()
                .cast(pl.Int32)
            ).alias("etmin")
        )
        self.min_bars: dict[str, np.ndarray] = {}
        for (oid,), g in b1.sort("etmin").group_by(["opportunity_id"]):
            self.min_bars[str(oid)] = g.select("etmin", "open", "high", "low", "close").to_numpy()

    def split(self, name: str) -> pl.DataFrame:
        return self.panel.filter(pl.col("split") == name)

    def session_dates(self, name: str) -> list[Any]:
        return sorted(self.sessions.filter(pl.col("split") == name)["dt"].to_list())

    def stream(self, name: str) -> pl.DataFrame:
        """The Filter-A stream on a split: one earliest-by-time trigger per session."""
        return stage0.filter_a(self.split(name), self.filter_a_constant)


# =================================================================================================
# The three entry mechanics (plan §5.1, pre-registered)
# =================================================================================================
class Entry:
    """A mechanic's resolved entry: the fill, the path it exits on, and why it was not taken."""

    __slots__ = ("fill", "path", "reason", "trigger_min", "fill_min")

    def __init__(
        self,
        fill: float | None,
        path: np.ndarray | None,
        reason: str,
        trigger_min: int | None = None,
        fill_min: int | None = None,
    ) -> None:
        self.fill, self.path, self.reason = fill, path, reason
        self.trigger_min, self.fill_min = trigger_min, fill_min

    @property
    def taken(self) -> bool:
        return self.fill is not None and self.path is not None and len(self.path) > 0


def _rebuild_from_minute(data: Data, row: dict[str, Any], fill_idx: int) -> Entry:
    """The exit path for a 1-minute mechanic: the 5-minute grid from the fill minute onward.

    The bucket holding the fill is rebuilt from the 1-minute bars at or after the fill minute —
    open = the fill, high/low over the remaining minutes, close = the bucket's close — so the
    trade is never stopped out by price action that happened before it was in. Every later
    5-minute bar comes from the stored path unchanged.
    """
    mb = data.min_bars[row["opportunity_id"]]
    fill = float(mb[fill_idx, 1])  # the fill minute's open
    fm = int(mb[fill_idx, 0])
    bucket = (fm // 5) * 5
    etm = data.path_etmin[row["key"]]
    where = np.nonzero(etm == bucket)[0]
    if not len(where):
        return Entry(None, None, "fill bucket absent from the stored path")
    bi = int(where[0])
    if fill <= float(row["stop"]):
        # The next minute opened at or through the consolidation low: there is no risk to measure
        # R against, so the setup is not a trade. Labelled rather than folded into "unaffordable".
        return Entry(None, None, "the 1-minute fill printed at or below the stop")
    same = mb[(mb[:, 0] >= fm) & (mb[:, 0] < bucket + 5)]
    head = np.array([[fill, float(same[:, 2].max()), float(same[:, 3].min()), float(same[-1, 4])]])
    path = np.vstack([head, data.path[row["key"]][bi + 1 :]])
    return Entry(fill, path, "", trigger_min=fm, fill_min=fm)


def entry_m1(data: Data, row: dict[str, Any]) -> Entry:
    """The incumbent-shaped baseline: the mechanical trigger, R against the 3-tick fill."""
    return Entry(float(row["entry_fill"]), data.path[row["key"]], "")


def entry_m2(data: Data, row: dict[str, Any]) -> Entry:
    """1-minute confirmation: the first minute to CLOSE at or above the trigger; fill next open."""
    mb = data.min_bars.get(row["opportunity_id"])
    if mb is None:
        return Entry(None, None, "no 1-minute tape")
    trig = float(row["entry_trigger"])
    window = np.nonzero((mb[:, 0] >= row["trigger_et_min"]) & (mb[:, 0] < PREMARKET_END_ETMIN))[0]
    for i in window:
        if mb[i, 4] >= trig:
            if i + 1 >= len(mb) or mb[i + 1, 0] >= PREMARKET_END_ETMIN:
                return Entry(None, None, "confirmed on the last available minute")
            e = _rebuild_from_minute(data, row, int(i) + 1)
            e.trigger_min = int(mb[i, 0])
            return e
    return Entry(None, None, "no 1-minute close above the trigger before 09:30")


def entry_m3(data: Data, row: dict[str, Any]) -> Entry:
    """1-minute achievable fill: M1's trigger resolved to the minute; fill = next minute's open."""
    mb = data.min_bars.get(row["opportunity_id"])
    if mb is None:
        return Entry(None, None, "no 1-minute tape")
    trig = float(row["entry_trigger"])
    window = np.nonzero((mb[:, 0] >= row["trigger_et_min"]) & (mb[:, 0] < PREMARKET_END_ETMIN))[0]
    for i in window:
        if mb[i, 2] >= trig:  # the minute whose HIGH reaches the trigger
            if i + 1 >= len(mb) or mb[i + 1, 0] >= PREMARKET_END_ETMIN:
                return Entry(None, None, "triggered on the last available minute")
            e = _rebuild_from_minute(data, row, int(i) + 1)
            e.trigger_min = int(mb[i, 0])
            return e
    return Entry(None, None, "the trigger price never printed on the 1-minute tape before 09:30")


MECHANICS: dict[str, Callable[[Data, dict[str, Any]], Entry]] = {
    "M1": entry_m1,
    "M2": entry_m2,
    "M3": entry_m3,
}


# =================================================================================================
# Scoring — one (mechanic, exit) point on one block
# =================================================================================================
def trade_rows(
    data: Data,
    rows: list[dict[str, Any]],
    mech: str,
    exit_fn: Callable[..., list[Leg]],
    entries: dict[tuple[str, str], Entry] | None = None,
) -> pl.DataFrame:
    """One row per *taken* trade, priced. Rows the mechanic does not enter are simply absent —
    their session then contributes 0 to J, which is what §6 requires."""
    out = []
    for r in rows:
        e = (entries or {}).get((mech, r["key"])) or MECHANICS[mech](data, r)
        if not e.taken:
            out.append({"key": r["key"], "dt": r["dt"], "taken": False, "reason": e.reason})
            continue
        assert e.path is not None and e.fill is not None
        legs = exit_fn(e.path, e.fill, float(r["stop"]))
        pr = stage0.price_trade(e.fill, float(r["stop"]), float(e.path[0, 0]), legs)
        out.append(
            {
                "key": r["key"],
                "dt": r["dt"],
                "taken": pr["net_r"] is not None,
                "reason": "" if pr["net_r"] is not None else "unaffordable",
                "gross_r": pr["gross_r"],
                "net_r": pr["net_r"],
                "net_usd": pr.get("net_usd"),
                "cost_r": pr.get("cost_r"),
                "stop_pct": r["stop_pct"],
                "entry_fill": e.fill,
                "n_legs": len(legs),
            }
        )
    return pl.DataFrame(out, infer_schema_length=None)


def decile(stop_pct: pl.Series) -> pl.Series:
    return stop_pct.cut(STOP_PCT_EDGES, labels=[f"D{i}" for i in range(1, 11)])


def score_point(
    data: Data,
    rows: list[dict[str, Any]],
    mech: str,
    exit_fn: Callable[..., list[Leg]],
    sessions: list[Any],
    entries: dict[tuple[str, str], Entry] | None = None,
) -> dict[str, Any]:
    """The §6.2 report plus W2's two required additions, for one scored point."""
    tr = trade_rows(data, rows, mech, exit_fn, entries)
    taken = tr.filter(pl.col("taken"))
    rep = stage0.session_report(taken, sessions)
    rep["not_taken"] = int(tr.height - taken.height)
    rep["not_taken_reasons"] = (
        tr.filter(~pl.col("taken"))["reason"].value_counts(sort=True).to_dicts()
    )
    rep["setups_offered"] = tr.height
    rep["gross_minus_net_r_per_session"] = round(
        rep["gross_r_per_session"] - rep["J_net_r_per_session"], 4
    )
    rep["cost_r_mean"] = round(float(taken["cost_r"].mean()), 4) if taken.height else None
    rep["hit_rate_net"] = round(float((taken["net_r"] > 0).mean()), 4) if taken.height else None
    rep["hit_rate_gross"] = round(float((taken["gross_r"] > 0).mean()), 4) if taken.height else None
    rep["recon_rows"] = taken.height  # FIT and CHECK are recon-only (§7.1)
    rep["live_rows"] = 0
    if taken.height:
        d = taken.with_columns(decile(taken["stop_pct"]).alias("decile"))
        rep["by_stop_pct_decile"] = [
            {
                "decile": str(g["decile"][0]),
                "trades": g.height,
                "net_r_mean": round(float(g["net_r"].mean()), 4),
                "gross_r_mean": round(float(g["gross_r"].mean()), 4),
                "cost_r_mean": round(float(g["cost_r"].mean()), 4),
                "hit_rate_net": round(float((g["net_r"] > 0).mean()), 4),
            }
            for (_,), g in sorted(d.group_by(["decile"]), key=lambda kv: str(kv[0][0]))
        ]
    return rep


def precompute_entries(data: Data, rows: list[dict[str, Any]]) -> dict[tuple[str, str], Entry]:
    """Resolve every (mechanic, row) entry once — the 1-minute scans are the expensive part."""
    return {(m, r["key"]): fn(data, r) for m, fn in MECHANICS.items() for r in rows}


# =================================================================================================
# E1 — fill realism. 3 trials. Family-free: it reads post-trigger price, so it is charged.
# =================================================================================================
def cmd_e1(data: Data) -> None:
    rows = data.stream("FIT").to_dicts()
    entries = precompute_entries(data, rows)
    base = {r["key"]: r for r in rows}
    out: dict[str, Any] = {
        "trials": 3,
        "block": "FIT, Filter-A stream",
        "setups": len(rows),
        "definition": {
            "fill_ticks": "(fill - consolidation high) / tick, the consolidation high = "
            "breakout_level; M1 is 3 ticks by construction",
            "r_max": "(max path high at or after entry - entry) / (entry - stop)",
            "degradation": "r_max(mechanic) - r_max(M1), over the setups both mechanics enter",
        },
    }
    per: dict[str, dict[str, Any]] = {}
    for m in MECHANICS:
        recs = []
        for r in rows:
            e = entries[(m, r["key"])]
            if not e.taken:
                continue
            assert e.path is not None and e.fill is not None
            entry = max(e.fill, float(e.path[0, 0]))
            risk = entry - float(r["stop"])
            if risk <= 0:
                continue
            recs.append(
                {
                    "key": r["key"],
                    "fill": e.fill,
                    "fill_ticks": (e.fill - float(r["breakout_level"])) / TICK,
                    "vs_trigger_ticks": (e.fill - float(r["entry_trigger"])) / TICK,
                    "r_max": (float(e.path[:, 1].max()) - entry) / risk,
                    "stop_pct": r["stop_pct"],
                    "delay_min": (e.fill_min - int(r["trigger_et_min"]))
                    if e.fill_min is not None
                    else 0,
                }
            )
        d = pl.DataFrame(recs)
        per[m] = {
            "taken": d.height,
            "not_taken": len(rows) - d.height,
            "fill_ticks_vs_cons_high": _q(d["fill_ticks"]),
            "fill_ticks_vs_trigger": _q(d["vs_trigger_ticks"]),
            "share_fill_below_trigger": round(float((d["vs_trigger_ticks"] < 0).mean()), 4),
            "entry_delay_minutes": _q(d["delay_min"].cast(pl.Float64)),
            "r_max": _q(d["r_max"]),
        }
        per[m]["_rows"] = d
    for m in ("M2", "M3"):
        a, b = per["M1"]["_rows"], per[m]["_rows"]
        j = a.join(b, on="key", suffix="_m")
        per[m]["degradation_vs_M1"] = {
            "common_setups": j.height,
            "d_r_max_mean": round(float((j["r_max_m"] - j["r_max"]).mean()), 4),
            "d_r_max_median": round(float((j["r_max_m"] - j["r_max"]).median()), 4),
            "d_fill_ticks_mean": round(float((j["fill_ticks_m"] - j["fill_ticks"]).mean()), 4),
            "share_worse_fill": round(float((j["fill_ticks_m"] > j["fill_ticks"]).mean()), 4),
        }
        # the plan's question: does the 3-tick assumption vary with stop_pct?
        jj = j.with_columns(decile(j["stop_pct"]).alias("decile"))
        per[m]["degradation_by_stop_pct_decile"] = [
            {
                "decile": str(g["decile"][0]),
                "n": g.height,
                "d_fill_ticks_mean": round(float((g["fill_ticks_m"] - g["fill_ticks"]).mean()), 3),
                "d_r_max_mean": round(float((g["r_max_m"] - g["r_max"]).mean()), 4),
            }
            for (_,), g in sorted(jj.group_by(["decile"]), key=lambda kv: str(kv[0][0]))
        ]
    for m in per:
        per[m].pop("_rows")
        per[m]["not_taken_reasons"] = _reasons(entries, m, base)
    out["mechanics"] = per
    _dump("w2-e1.json", out)


def _q(s: pl.Series) -> dict[str, float]:
    if not s.len():
        return {}
    return {
        "n": s.len(),
        "mean": round(float(s.mean()), 4),
        "q10": round(float(s.quantile(0.1)), 4),
        "q50": round(float(s.quantile(0.5)), 4),
        "q90": round(float(s.quantile(0.9)), 4),
    }


def _reasons(
    entries: dict[tuple[str, str], Entry], mech: str, base: dict[str, Any]
) -> list[dict[str, Any]]:
    c: dict[str, int] = {}
    for (m, k), e in entries.items():
        if m == mech and not e.taken and k in base:
            c[e.reason] = c.get(e.reason, 0) + 1
    return [{"reason": r, "n": n} for r, n in sorted(c.items(), key=lambda kv: -kv[1])]


# =================================================================================================
# E2 / E3 / E4 / E5 — the scored grids
# =================================================================================================
def _grid_points(stage: str) -> dict[str, tuple[str, Callable[..., list[Leg]]]]:
    if stage == "e2":
        return {f"{m}x{f}": (m, fn) for m in MECHANICS for f, fn in FAMILIES.items()}
    if stage == "e3":
        return {f"M1x{n}": ("M1", fn) for n, fn in e3_grid().items()}
    if stage == "e4":
        return {f"M1x{n}": ("M1", fn) for n, fn in e4_grid().items()}
    raise KeyError(stage)


def cmd_e2(data: Data) -> None:
    rows = data.stream("FIT").to_dicts()
    entries = precompute_entries(data, rows)
    sess = data.session_dates("FIT")
    pts = _grid_points("e2")
    out: dict[str, Any] = {"trials": 12, "block": "FIT, Filter-A stream", "sessions": len(sess)}
    out["points"] = {
        name: score_point(data, rows, m, fn, sess, entries) for name, (m, fn) in pts.items()
    }
    out["J"] = {k: v["J_net_r_per_session"] for k, v in out["points"].items()}
    _dump("w2-e2.json", out)


def cmd_e3e4(data: Data) -> None:
    rows = data.stream("FIT").to_dicts()
    entries = precompute_entries(data, rows)
    sess = data.session_dates("FIT")
    out: dict[str, Any] = {"trials": 12, "block": "FIT, Filter-A stream", "sessions": len(sess)}
    for stage in ("e3", "e4"):
        pts = _grid_points(stage)
        out[stage] = {
            name: score_point(data, rows, m, fn, sess, entries) for name, (m, fn) in pts.items()
        }
    out["J"] = {k: v["J_net_r_per_session"] for s in ("e3", "e4") for k, v in out[s].items()}
    _dump("w2-e3e4.json", out)


def cmd_e5(data: Data, best: list[str]) -> None:
    """The 2 best families from E2, re-scored on the two a-priori `stop_pct` strata. 4 trials."""
    rows = data.stream("FIT").to_dicts()
    entries = precompute_entries(data, rows)
    med = float(pl.Series([r["stop_pct"] for r in rows]).median())
    lo = [r for r in rows if r["stop_pct"] < med]
    hi = [r for r in rows if r["stop_pct"] >= med]
    out: dict[str, Any] = {
        "trials": 4,
        "stratum_boundary": {
            "col": "stop_pct",
            "quantile": "FIT median of the Filter-A stream",
            "value": med,
        },
        "strata_sizes": {"below": len(lo), "at_or_above": len(hi)},
    }
    allpts = {**_grid_points("e2"), **_grid_points("e3"), **_grid_points("e4")}
    res: dict[str, Any] = {}
    for name in best:
        m, fn = allpts[name]
        for lab, sub in (("below_median", lo), ("at_or_above_median", hi)):
            sess = sorted({r["dt"] for r in sub})
            res[f"{name}|{lab}"] = score_point(data, sub, m, fn, sess, entries)
    out["points"] = res
    out["J"] = {k: v["J_net_r_per_session"] for k, v in res.items()}
    _dump("w2-e5.json", out)


# =================================================================================================
# The permutation null — block-by-session, B = 200, at matched search intensity (§7.3)
# =================================================================================================
def cmd_null(data: Data) -> None:
    """One replicate = one shuffled record: within each session, the row Filter A's slot receives
    is redrawn from that session's own pool of panel rows. The *entire* search is re-run on it.
    p = the share of replicates whose best-of-search J is >= the real best-of-search J."""
    pool_rows = data.split("FIT").to_dicts()
    stream = data.stream("FIT").to_dicts()
    sess = data.session_dates("FIT")
    entries = precompute_entries(data, pool_rows)
    pts = {**_grid_points("e2"), **_grid_points("e3"), **_grid_points("e4")}

    # net R for every panel row under every point (rows the mechanic does not enter score 0)
    keys = [r["key"] for r in pool_rows]
    kidx = {k: i for i, k in enumerate(keys)}
    mat = np.zeros((len(pts), len(keys)))
    for pi, (m, fn) in enumerate(pts.values()):
        tr = trade_rows(data, pool_rows, m, fn, entries)
        for r in tr.filter(pl.col("taken")).iter_rows(named=True):
            mat[pi, kidx[r["key"]]] = r["net_r"]

    by_session: dict[Any, list[int]] = {}
    for r in pool_rows:
        by_session.setdefault(r["dt"], []).append(kidx[r["key"]])
    real_idx = np.array([kidx[r["key"]] for r in stream])
    real_j = mat[:, real_idx].sum(axis=1) / len(sess)

    rng = np.random.default_rng(SEED)
    dates = [r["dt"] for r in stream]
    null_best = np.empty(B_NULL)
    null_per_point = np.empty((B_NULL, len(pts)))
    for b in range(B_NULL):
        draw = np.array([rng.choice(by_session[d]) for d in dates])
        j = mat[:, draw].sum(axis=1) / len(sess)
        null_per_point[b] = j
        null_best[b] = j.max()

    names = list(pts)
    out = {
        "trials": 0,
        "B": B_NULL,
        "seed": SEED,
        "intensity": f"{len(pts)} points re-run per replicate (E2 12 + E3 6 + E4 6)",
        "pool": {"panel_rows": len(pool_rows), "sessions": len(sess)},
        "real_best_of_search": {
            "point": names[int(real_j.argmax())],
            "J": round(float(real_j.max()), 4),
        },
        "sequence_null": {
            "null_best_q50_q95": [round(float(np.quantile(null_best, q)), 4) for q in (0.5, 0.95)],
            "p": round(float((1 + (null_best >= real_j.max()).sum()) / (1 + B_NULL)), 4),
        },
        "e2_only_null": _sub_null(
            names, real_j, null_per_point, lambda n: "x" in n and n.split("x")[1] in FAMILIES
        ),
        "per_point_p": {
            n: round(float((1 + (null_per_point[:, i] >= real_j[i]).sum()) / (1 + B_NULL)), 4)
            for i, n in enumerate(names)
        },
        "real_J": {n: round(float(real_j[i]), 4) for i, n in enumerate(names)},
    }
    _dump("w2-null.json", out)


def _sub_null(
    names: list[str], real_j: np.ndarray, null_per_point: np.ndarray, pred: Callable[[str], bool]
) -> dict[str, Any]:
    sel = [i for i, n in enumerate(names) if pred(n)]
    rb = real_j[sel].max()
    nb = null_per_point[:, sel].max(axis=1)
    return {
        "points": len(sel),
        "real_best": round(float(rb), 4),
        "null_best_q50_q95": [round(float(np.quantile(nb, q)), 4) for q in (0.5, 0.95)],
        "p": round(float((1 + (nb >= rb).sum()) / (1 + len(nb))), 4),
    }


# =================================================================================================
# E6 / E7 — walk-forward refits (3 trials) and the single CHECK score (1 trial)
# =================================================================================================
def cmd_e7(data: Data, candidate: str) -> None:
    """The single CHECK score. 1 trial.

    ⚠️ Run as the **confirmation of a null**, not the validation of a candidate: every FIT point
    was negative, so §9.1 condition 2 fails for all of them and nothing can be frozen. Ledgered as
    amendment W2-A3 before it was run.
    """
    allpts = {**_grid_points("e2"), **_grid_points("e3"), **_grid_points("e4")}
    mech, fn = allpts[candidate]
    rows = data.stream("CHECK").to_dicts()
    sess = data.session_dates("CHECK")
    out = {
        "trials": 1,
        "scored": candidate,
        "role": "confirmation of the null; not a carried candidate",
        "block": "CHECK, Filter-A stream",
        "sessions": len(sess),
        "report": score_point(data, rows, mech, fn, sess, precompute_entries(data, rows)),
    }
    _dump("w2-e7.json", out)


def cmd_e6e7(data: Data, candidate: str) -> None:
    allpts = {**_grid_points("e2"), **_grid_points("e3"), **_grid_points("e4")}
    fit_rows = data.stream("FIT").to_dicts()
    entries_fit = precompute_entries(data, fit_rows)
    out: dict[str, Any] = {"trials": 4, "candidate": candidate}

    # --- E6: refit the candidate's family interior on each fold's train, score on its test ------
    fit_sess = data.session_dates("FIT")
    folds = [{"train": fit_sess[:k], "test": fit_sess[k : k + 50]} for k in (95, 145, 195)]
    mech = allpts[candidate][0]
    fam = candidate.split("x", 1)[1]
    refit_space = (
        e3_grid()
        if fam.startswith("F3")
        else e4_grid()
        if fam.startswith("F4")
        else {fam: FAMILIES[fam]}
    )
    out["e6_refit_space"] = list(refit_space)
    e6 = []
    for i, f in enumerate(folds, 1):
        tr_rows = [r for r in fit_rows if r["dt"] in set(f["train"])]
        te_rows = [r for r in fit_rows if r["dt"] in set(f["test"])]
        js = {
            n: score_point(data, tr_rows, mech, fn, f["train"], entries_fit)["J_net_r_per_session"]
            for n, fn in refit_space.items()
        }
        pick = max(js, key=lambda n: js[n])
        test = score_point(data, te_rows, mech, refit_space[pick], f["test"], entries_fit)
        e6.append(
            {
                "fold": i,
                "train_sessions": len(f["train"]),
                "test_sessions": len(f["test"]),
                "train_J_by_point": {k: round(v, 4) for k, v in js.items()},
                "refit_pick": pick,
                "test_report": test,
            }
        )
    out["e6"] = e6
    out["e6_parameter_stability"] = [f["refit_pick"] for f in e6]

    # --- sensitivity: +/-20 % on the frozen interior. Not a trial; never used to select ---------
    out["sensitivity_note"] = (
        "reported from the already-charged E3/E4 grid where a +/-20 % neighbour exists on it; "
        "protocol §5.2 forbids using a sensitivity band to select a value"
    )

    # --- E7: the single CHECK score -------------------------------------------------------------
    ch_rows = data.stream("CHECK").to_dicts()
    ch_sess = data.session_dates("CHECK")
    out["e7"] = score_point(
        data, ch_rows, mech, allpts[candidate][1], ch_sess, precompute_entries(data, ch_rows)
    )
    _dump("w2-e6e7.json", out)


# =================================================================================================
# §9.2 — the per-family viability table. Free: `c` comes from S0-F, the hit rate from E2.
# =================================================================================================
def cmd_viability(data: Data) -> None:
    """Measured cost by `stop_pct` decile, the implied net break-even, and the realised hit rate
    on the Filter-A stream. Numbers only — the viable/marginal/dead verdict is interpretation."""
    e2 = json.loads((OUT / "w2-e2.json").read_text())
    rows = data.stream("FIT").to_dicts()
    entries = precompute_entries(data, rows)
    out: dict[str, Any] = {
        "trials": 0,
        "source": "cost c from S0-F (panel-v1-spec.md §6); hit rates re-read off E2's M1 column",
        "note": "F3/F4 have no fixed target, so a target break-even is undefined; their floor is "
        "the per-trade cost c subtracted from every trade regardless of outcome",
    }
    fam_rows = {}
    for fam, fn in FAMILIES.items():
        tr = trade_rows(data, rows, "M1", fn, entries).filter(pl.col("taken"))
        d = tr.with_columns(decile(tr["stop_pct"]).alias("decile"))
        fam_rows[fam] = {
            "trades": tr.height,
            "hit_rate_net": round(float((tr["net_r"] > 0).mean()), 4),
            "hit_rate_gross": round(float((tr["gross_r"] > 0).mean()), 4),
            "cost_r_mean": round(float(tr["cost_r"].mean()), 4),
            "net_r_per_trade": round(float(tr["net_r"].mean()), 4),
            "gross_r_per_trade": round(float(tr["gross_r"].mean()), 4),
            "J": e2["points"][f"M1x{fam}"]["J_net_r_per_session"],
            "by_decile": [
                {
                    "decile": str(g["decile"][0]),
                    "trades": g.height,
                    "stop_pct_median": round(float(g["stop_pct"].median()), 4),
                    "cost_r_mean": round(float(g["cost_r"].mean()), 4),
                    "hit_rate_net": round(float((g["net_r"] > 0).mean()), 4),
                    "hit_rate_gross": round(float((g["gross_r"] > 0).mean()), 4),
                    "net_r_per_trade": round(float(g["net_r"].mean()), 4),
                }
                for (_,), g in sorted(d.group_by(["decile"]), key=lambda kv: str(kv[0][0]))
            ],
        }
    out["families"] = fam_rows
    # what account size would move a family's cost floor? c ∝ 1 / (BP × stop_pct) on the fee term
    out["capital_sweep"] = _capital_sweep(data, rows)
    _dump("w2-viability.json", out)


def _capital_sweep(data: Data, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """The §9.4 question: what account size moves a family from dead to viable? Free — it reads
    `entry_fill` and `stop` only, no outcome and no post-trigger price."""
    from spikes.engine_lab.common import Costs, Sizing

    costs = Costs()
    res = {}
    for eq in (500, 1000, 2500, 5000, 10000, 25000):
        sizing = Sizing(equity=float(eq), risk_fraction=1.0e9, position_fraction=1.0)
        cl, cw, fee, slp, n = [], [], [], [], 0
        for r in rows:
            e, s = float(r["entry_fill"]), float(r["stop"])
            qty, _ = sizing.qty(e, s)
            if qty < 1:
                continue
            risk = qty * (e - s)
            f1, s1 = stage0.leg_costs(qty, [Leg(1.0, s, False, -1.0)], costs, 2.0)
            f2, s2 = stage0.leg_costs(qty, [Leg(1.0, e + 2 * (e - s), True, 2.0)], costs, 2.0)
            cl.append((f1 + s1) / risk)
            cw.append((f2 + s2) / risk)
            # §11.1's two terms, separated: the fee term shrinks with buying power (the per-order
            # minimum stops binding); the slippage term is ticks per share and does not.
            fee.append(f1 / risk)
            slp.append(s1 / risk)
            n += 1
        m_l, m_w = float(np.mean(cl)), float(np.mean(cw))
        res[f"${eq}"] = {
            "affordable_trades": n,
            "c_loss_mean": round(m_l, 4),
            "c_win_mean": round(m_w, 4),
            "c_loss_fee_term": round(float(np.mean(fee)), 4),
            "c_loss_slip_term": round(float(np.mean(slp)), 4),
            "F1_breakeven": round(stage0._breakeven(m_l, m_w, 0.5) * 100, 1),
            "F2_breakeven": round(stage0._breakeven(m_l, m_w, 2.0) * 100, 1),
            "F1_breakeven_slip0": round(
                stage0._breakeven(float(np.mean(fee)), float(np.mean(fee)), 0.5) * 100, 1
            ),
        }
    return res


# =================================================================================================
def _dump(name: str, obj: Any) -> None:
    (OUT / name).write_text(json.dumps(obj, indent=2, default=str))
    print(f"wrote {OUT / name}")
    print(json.dumps(obj.get("J", obj.get("mechanics", {})), indent=2, default=str)[:4000])


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "e1"
    data = Data()
    if cmd == "e1":
        cmd_e1(data)
    elif cmd == "e2":
        cmd_e2(data)
    elif cmd == "e3e4":
        cmd_e3e4(data)
    elif cmd == "e5":
        cmd_e5(data, sys.argv[2].split(","))
    elif cmd == "null":
        cmd_null(data)
    elif cmd == "e7":
        cmd_e7(data, sys.argv[2])
    elif cmd == "e6e7":
        cmd_e6e7(data, sys.argv[2])
    elif cmd == "viability":
        cmd_viability(data)
    else:
        raise SystemExit(f"unknown command {cmd!r}")


if __name__ == "__main__":
    main()
