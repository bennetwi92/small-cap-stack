"""Agent C (exits) — bracket geometry lab.

Everything here is built on `spikes/engine_lab/common.py`; nothing in it is forked. The one thing
this module adds is **speed**: `replay_bracket()` is a Python loop over bars, and a joint
stop x target sweep needs hundreds of full passes over 3,639 paths. So the paths are packed once
into padded `(n_rows, max_bars)` numpy matrices and every bracket is resolved with two `argmax`
calls per config.

`check_equivalence()` proves the fast path reproduces `common.replay_bracket()` exactly, across
many bracket shapes; `common.verify_paths()` (unmodified) still proves the paths reproduce every
published `max_r`. Run both before trusting a number out of here.

⚠️ The panel's `max_r` is denominated in the **shipped** stop's risk. The moment a proposal moves
the stop, every published R is wrong for it, so R is always re-derived from the bars.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from engine_lab import common as C  # noqa: E402

OUT = C.REPO / "data/spikes/engine-lab/exits"
CTX_CACHE = OUT / "pretrigger.parquet"


# ---------------------------------------------------------------------------------------------
# Pre-trigger context — the only thing a target may read besides the setup's own geometry
# ---------------------------------------------------------------------------------------------
def build_pretrigger(df: pl.DataFrame, *, cache: Path = CTX_CACHE) -> pl.DataFrame:
    """Per row: the highest high printed **before** the trigger bar, and the session's first open.

    Both are known at the trigger, so a target derived from them is not lookahead. Every trigger in
    this population is pre-market, so `pre_high` is literally "the pre-market high so far".
    """
    if cache.exists():
        got = pl.read_parquet(cache)
        if set(df["key"]) <= set(got["key"]):
            return got
    rows: list[dict[str, Any]] = []
    for (source, d), grp in df.group_by(["source", "dt"], maintain_order=True):
        bars = C._day_bars(str(source), d)  # type: ignore[arg-type]
        if bars.is_empty():
            continue
        bars = bars.unique(subset=["opportunity_id", "bar_start_utc"], keep="first")
        et = bars["bar_start_utc"].dt.convert_time_zone("America/New_York")
        mins = (et.dt.hour().cast(pl.Int32) * 60 + et.dt.minute().cast(pl.Int32)).alias("etmin")
        bars = bars.with_columns(mins).filter((pl.col("etmin") >= 240) & (pl.col("etmin") < 960))
        by_oid = {
            str(k[0]): g.sort("bar_start_utc").select(["open", "high", "low"]).to_numpy()
            for k, g in bars.group_by(["opportunity_id"])
        }
        for row in grp.iter_rows(named=True):
            arr = by_oid.get(row["opportunity_id"])
            if arr is None:
                continue
            j = int(row["trigger_idx"])
            pre = arr[:j]
            rows.append(
                {
                    "key": row["key"],
                    "pre_high": float(pre[:, 1].max()) if len(pre) else float("nan"),
                    "pre_low": float(pre[:, 2].min()) if len(pre) else float("nan"),
                    "sess_open": float(arr[0, 0]),
                    "n_pre_bars": len(pre),
                }
            )
    out = pl.DataFrame(rows)
    cache.parent.mkdir(parents=True, exist_ok=True)
    out.write_parquet(cache)
    return out


# ---------------------------------------------------------------------------------------------
# Packed paths
# ---------------------------------------------------------------------------------------------
@dataclass
class Packed:
    """Padded OHLC matrices, row i == `keys[i]`, column k == bar k after (and incl.) the trigger."""

    keys: list[str]
    idx: dict[str, int]
    O0: np.ndarray  # open of the trigger bar
    H: np.ndarray  # highs, padded with -inf
    L: np.ndarray  # lows, padded with +inf
    Cend: np.ndarray  # close of the final (16:00) bar
    n: np.ndarray  # true bar count
    open_930: np.ndarray  # open of the first bar at/after 09:30 ET, NaN if none
    i930: np.ndarray  # index of that bar, -1 if none
    row_open: np.ndarray  # open of every bar, padded with nan

    def sub(self, keys: list[str]) -> Packed:
        j = np.array([self.idx[k] for k in keys])
        return Packed(
            keys=keys,
            idx={k: i for i, k in enumerate(keys)},
            O0=self.O0[j],
            H=self.H[j],
            L=self.L[j],
            Cend=self.Cend[j],
            n=self.n[j],
            open_930=self.open_930[j],
            i930=self.i930[j],
            row_open=self.row_open[j],
        )


def pack(df: pl.DataFrame, paths: dict[str, np.ndarray]) -> Packed:
    keys = [k for k in df["key"].to_list() if k in paths]
    B = max(len(paths[k]) for k in keys)
    n_rows = len(keys)
    H = np.full((n_rows, B), -np.inf)
    L = np.full((n_rows, B), np.inf)
    RO = np.full((n_rows, B), np.nan)
    O0 = np.empty(n_rows)
    Ce = np.empty(n_rows)
    nn = np.empty(n_rows, dtype=int)
    # bar index at which 09:30 arrives: trigger_et_min + 5*k >= 570
    tmin = dict(zip(df["key"].to_list(), df["trigger_et_min"].to_list(), strict=True))
    i930 = np.full(n_rows, -1, dtype=int)
    o930 = np.full(n_rows, np.nan)
    for i, k in enumerate(keys):
        a = paths[k]
        m = len(a)
        H[i, :m] = a[:, 1]
        L[i, :m] = a[:, 2]
        RO[i, :m] = a[:, 0]
        O0[i] = a[0, 0]
        Ce[i] = a[-1, 3]
        nn[i] = m
        need = int(np.ceil((C.PREMARKET_CUT - float(tmin[k])) / 5.0))
        if 0 <= need < m:
            i930[i] = need
            o930[i] = a[need, 0]
    return Packed(keys, {k: i for i, k in enumerate(keys)}, O0, H, L, Ce, nn, o930, i930, RO)


# ---------------------------------------------------------------------------------------------
# The bracket
# ---------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Bracket:
    """One stop and one target, both fixed at entry. Nothing moves for the life of the trade.

    Stop distance below the entry fill is built as::

        base   = cons_low_dist                       (the shipped stop: entry - consolidation low)
               + buf_frac * cons_low_dist            (a buffer, as a fraction of that distance)
        base   = max(base, pct_of_entry * entry)     if pct_of_entry
        base   = max(base, pole_frac  * pole_height) if pole_frac
        dist   = clip(base, floor_pct * entry, ceil_pct * entry)

    Target price is whichever of the enabled forms is chosen (exactly one, or the min of several
    when `target_mode="min"`), all decidable at the trigger bar.
    """

    # stop
    buf_frac: float = 0.0  # buffer below the consolidation low, as a fraction of its distance
    buf_ticks: float = 0.0  # ... or in absolute ticks
    stop_pct_entry: float | None = None  # a hard percentage-of-entry stop instead of the cons low
    stop_pole_frac: float | None = None  # ... or a fraction of the pole height
    floor_pct: float | None = None  # minimum stop distance, as a fraction of entry
    ceil_pct: float | None = None  # maximum stop distance, as a fraction of entry
    # target
    target_r: float | None = 2.0
    target_pct: float | None = None  # target = entry * (1 + target_pct)
    target_pole_frac: float | None = None  # target = entry + f * pole_height
    target_pre_high_frac: float | None = None  # target = entry + f * (pre_high - entry), f>=1
    target_mode: str = "r"  # "r" | "pct" | "pole" | "prehigh" | "min"
    # mechanics
    exit_at_930: bool = False  # flatten at the 09:30 open (a mechanical necessity, not a time stop)

    def label(self) -> str:
        bits = []
        if self.stop_pct_entry is not None:
            bits.append(f"stop={self.stop_pct_entry:.1%}entry")
        elif self.stop_pole_frac is not None:
            bits.append(f"stop={self.stop_pole_frac:.2f}pole")
        else:
            bits.append(f"stop=cons+{self.buf_frac:.0%}")
        if self.floor_pct:
            bits.append(f"floor{self.floor_pct:.1%}")
        if self.ceil_pct:
            bits.append(f"ceil{self.ceil_pct:.1%}")
        if self.target_mode == "r":
            bits.append(f"tgt={self.target_r}R")
        elif self.target_mode == "pct":
            bits.append(f"tgt={self.target_pct:.1%}")
        elif self.target_mode == "pole":
            bits.append(f"tgt={self.target_pole_frac:.2f}pole")
        elif self.target_mode == "prehigh":
            bits.append(f"tgt=preH x{self.target_pre_high_frac:.2f}")
        else:
            bits.append("tgt=min(...)")
        if self.exit_at_930:
            bits.append("flat@0930")
        return " ".join(bits)


@dataclass
class Geom:
    """The per-row inputs a bracket needs. All measured at or before the trigger bar."""

    entry_fill: np.ndarray
    cons_stop: np.ndarray  # the shipped stop = consolidation low
    pole_h: np.ndarray  # pole height in price
    pre_high: np.ndarray


def geometry(df: pl.DataFrame, packed: Packed, pre: pl.DataFrame) -> Geom:
    d = df.join(pre, on="key", how="left")
    d = d.with_columns(pl.col("key").replace_strict(packed.idx, default=None).alias("_i")).filter(
        pl.col("_i").is_not_null()
    )
    d = d.sort("_i")
    entry_fill = d["entry_fill"].to_numpy().astype(float)
    return Geom(
        entry_fill=entry_fill,
        cons_stop=d["stop"].to_numpy().astype(float),
        pole_h=(d["pole_pct"].to_numpy().astype(float) * entry_fill),
        pre_high=d["pre_high"].to_numpy().astype(float),
    )


def stop_price(b: Bracket, g: Geom, entry: np.ndarray) -> np.ndarray:
    """The stop price for each row. Entry is the realised fill (max(entry_fill, trigger open))."""
    cons_dist = entry - g.cons_stop
    if b.stop_pct_entry is not None:
        dist = b.stop_pct_entry * entry
    elif b.stop_pole_frac is not None:
        dist = b.stop_pole_frac * g.pole_h
    else:
        dist = cons_dist * (1.0 + b.buf_frac) + b.buf_ticks * C.TICK
    if b.floor_pct is not None:
        dist = np.maximum(dist, b.floor_pct * entry)
    if b.ceil_pct is not None:
        dist = np.minimum(dist, b.ceil_pct * entry)
    return entry - dist


def target_price(b: Bracket, g: Geom, entry: np.ndarray, risk: np.ndarray) -> np.ndarray:
    cands = []
    if b.target_mode in ("r", "min") and b.target_r is not None:
        cands.append(entry + b.target_r * risk)
    if b.target_mode in ("pct", "min") and b.target_pct is not None:
        cands.append(entry * (1.0 + b.target_pct))
    if b.target_mode in ("pole", "min") and b.target_pole_frac is not None:
        cands.append(entry + b.target_pole_frac * g.pole_h)
    if b.target_mode in ("prehigh", "min") and b.target_pre_high_frac is not None:
        # the pre-market high, extended by a factor. Rows already above it fall back to the
        # R target, so a name that has already cleared its pre-market high still has a target.
        lvl = entry + b.target_pre_high_frac * (g.pre_high - entry)
        fallback = entry + (b.target_r if b.target_r else 2.0) * risk
        cands.append(np.where(np.isfinite(lvl) & (lvl > entry), lvl, fallback))
    if not cands:
        return np.full_like(entry, np.inf)
    return np.min(np.stack(cands), axis=0) if len(cands) > 1 else cands[0]


def resolve(b: Bracket, g: Geom, p: Packed) -> dict[str, np.ndarray]:
    """Vectorised `replay_bracket` over every packed row. Same semantics, including stop-first."""
    entry = np.maximum(g.entry_fill, p.O0)
    stop = stop_price(b, g, entry)
    risk = entry - stop
    ok = risk > 0
    risk_safe = np.where(ok, risk, 1.0)
    tgt = target_price(b, g, entry, risk_safe)

    big = p.H.shape[1] + 10
    stop_hit = stop[:, None] >= p.L
    tgt_hit = tgt[:, None] <= p.H
    si = np.where(stop_hit.any(1), stop_hit.argmax(1), big)
    ti = np.where(tgt_hit.any(1), tgt_hit.argmax(1), big)

    # flatten at the 09:30 open if neither leg has fired by then
    cut = np.where(p.i930 >= 0, p.i930, big) if b.exit_at_930 else np.full(len(entry), big)

    # Priority at an equal bar index: the 09:30 flatten happens at the open, so it precedes both
    # legs; between the two legs the STOP wins, exactly as `replay_bracket` checks it first.
    first = np.minimum(np.minimum(si, ti), cut)
    cut930 = (cut <= si) & (cut <= ti) & (cut < big)
    stopped = ~cut930 & (si <= ti) & (si < big)
    won = ~cut930 & ~stopped & (ti < big)
    flat = ~stopped & ~won
    # the "flat" exit price: the 09:30 open when flattening, else the 16:00 close
    flat_px = np.where(cut930, p.open_930, p.Cend)
    exit_px = np.where(stopped, stop, np.where(won, tgt, flat_px))
    r = (exit_px - entry) / risk_safe
    r = np.where(ok, r, 0.0)

    exit_idx = np.minimum(first, p.n - 1)
    # MFE, mirroring `_measure`: a same-bar stop credits no favourable excursion
    same_bar = p.L[:, 0] <= stop
    run = np.maximum.accumulate(np.where(np.isfinite(p.H), p.H, -np.inf), axis=1)
    at = np.clip(exit_idx, 0, p.H.shape[1] - 1)
    mx = run[np.arange(len(entry)), at]
    mx = np.where(same_bar, entry, mx)
    max_r = (mx - entry) / risk_safe

    return {
        "entry": entry,
        "stop": stop,
        "target": tgt,
        "risk": risk,
        "r": r,
        "max_r": max_r,
        "stopped": stopped,
        "won": won,
        "flat": flat,
        "same_bar": same_bar & stopped,
        "bars_held": exit_idx,
        "valid": ok,
        # still open when the bell rings — the pre-market trade that becomes a regular-hours trade
        "open_at_930": (p.i930 >= 0) & (np.minimum(si, ti) > p.i930),
    }


# ---------------------------------------------------------------------------------------------
# Scoring a bracket end to end
# ---------------------------------------------------------------------------------------------
def book_with_bracket(
    df: pl.DataFrame,
    b: Bracket,
    g_all: Geom,
    p_all: Packed,
    *,
    max_per_day: int = 2,
    selector: Any = None,
) -> pl.DataFrame:
    """Select -> cap at 2/day in time order -> replay the bracket -> a scoreable trade frame.

    The **stop column is overwritten** with the bracket's stop, because `score()` sizes and prices
    costs off `entry_fill - stop`; leaving the shipped stop there would size every trade wrong.
    """
    sel = selector(df) if selector is not None else df
    book = C.build_book(sel, max_per_day=max_per_day)
    keys = [k for k in book["key"].to_list() if k in p_all.idx]
    if not keys:
        return pl.DataFrame()
    book = book.filter(pl.col("key").is_in(keys)).sort(
        pl.col("key").replace_strict(p_all.idx, default=None)
    )
    sp = p_all.sub(book["key"].to_list())
    j = np.array([p_all.idx[k] for k in book["key"].to_list()])
    g = Geom(g_all.entry_fill[j], g_all.cons_stop[j], g_all.pole_h[j], g_all.pre_high[j])
    res = resolve(b, g, sp)
    out = book.with_columns(
        pl.Series("r", res["r"]),
        pl.Series("bracket_stop", res["stop"]),
        pl.Series("bracket_target", res["target"]),
        pl.Series("bracket_max_r", res["max_r"]),
        pl.Series("bracket_stopped", res["stopped"]),
        pl.Series("bracket_won", res["won"]),
        pl.Series("bracket_same_bar", res["same_bar"]),
        pl.Series("bracket_open_930", res["open_at_930"]),
        pl.Series("bracket_bars", res["bars_held"]),
        pl.Series("bracket_valid", res["valid"]),
    ).filter(pl.col("bracket_valid"))
    # score() sizes from entry_fill - stop; point it at the bracket's stop
    return out.drop("stop").rename({"bracket_stop": "stop"})


def evaluate(
    df: pl.DataFrame,
    b: Bracket,
    g_all: Geom,
    p_all: Packed,
    *,
    selector: Any = None,
    max_per_day: int = 2,
    sessions: int | None = None,
) -> dict[str, Any]:
    trades = book_with_bracket(df, b, g_all, p_all, max_per_day=max_per_day, selector=selector)
    if trades.is_empty():
        return {"trades": 0, "net_r": 0.0, "net_r_per_trade": 0.0, "gross_r": 0.0, "win_rate": 0.0}
    s = C.score(trades, sessions=sessions or df["dt"].n_unique(), by=("split", "source"))
    tf = s["_trades"]
    s["pct_stopped"] = round(float(tf["bracket_stopped"].mean()), 4)
    s["pct_same_bar"] = round(float(tf["bracket_same_bar"].mean()), 4)
    s["pct_open_930"] = round(float(tf["bracket_open_930"].mean()), 4)
    s["pct_fill_above_high"] = round(float(tf["fill_above_entry_bar_high"].mean()), 4)
    s["mean_stop_pct"] = round(
        float(((tf["entry_fill"] - tf["stop"]) / tf["entry_fill"]).mean()), 4
    )
    return s


# ---------------------------------------------------------------------------------------------
# Equivalence check — the fast path must be the slow path
# ---------------------------------------------------------------------------------------------
def check_equivalence(
    df: pl.DataFrame, paths: dict[str, np.ndarray], pre: pl.DataFrame, *, n: int = 400
) -> dict[str, Any]:
    """Resolve a random sample of rows under several brackets both ways and compare R exactly."""
    rng = np.random.default_rng(3)
    keys = df["key"].to_list()
    pick = sorted(rng.choice(len(keys), size=min(n, len(keys)), replace=False).tolist())
    sub = df[pick]
    p = pack(sub, paths)
    g = geometry(sub, p, pre)
    brackets = [
        Bracket(),
        Bracket(target_r=1.0),
        Bracket(buf_frac=0.25, target_r=3.0),
        Bracket(buf_ticks=5, target_r=1.5),
        Bracket(stop_pct_entry=0.05, target_r=2.0),
        Bracket(stop_pole_frac=0.5, target_r=2.5),
        Bracket(buf_frac=0.5, floor_pct=0.03, ceil_pct=0.12, target_r=2.0),
        Bracket(target_mode="pct", target_pct=0.08),
        Bracket(target_mode="pole", target_pole_frac=1.0),
        Bracket(target_r=None, target_mode="none"),
    ]
    worst = 0.0
    checks = 0
    for b in brackets:
        fast = resolve(b, g, p)
        entry = np.maximum(g.entry_fill, p.O0)
        sp = stop_price(b, g, entry)
        risk = entry - sp
        tp = target_price(b, g, entry, np.where(risk > 0, risk, 1.0))
        for i, key in enumerate(p.keys):
            if risk[i] <= 0:
                continue
            slow = C.replay_bracket(
                paths[key],
                g.entry_fill[i],
                float(sp[i]),
                target_r=None,
                target_price=None if not np.isfinite(tp[i]) else float(tp[i]),
            )
            worst = max(worst, abs(slow["r"] - fast["r"][i]))
            checks += 1
    return {"brackets": len(brackets), "row_checks": checks, "max_abs_r_diff": worst}


def load_all() -> tuple[pl.DataFrame, Packed, Geom, dict[str, np.ndarray], pl.DataFrame]:
    df = C.load_panel()
    paths = C.load_paths(df)
    pre = build_pretrigger(df)
    p = pack(df, paths)
    g = geometry(df, p, pre)
    return df, p, g, paths, pre


if __name__ == "__main__":
    df, p, g, paths, pre = load_all()
    print(f"panel {df.height} rows / {df['dt'].n_unique()} sessions; packed {len(p.keys)}")
    print("verify_paths:", {k: v for k, v in C.verify_paths(df, paths).items() if k != "examples"})
    print("equivalence :", check_equivalence(df, paths, pre))
