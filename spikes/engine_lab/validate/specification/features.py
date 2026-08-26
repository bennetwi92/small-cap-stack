"""Alternative operationalisations of "already running" and "small", built from raw bars.

⚠️ **How each feature is bounded.** The panel gives every row `trigger_idx` (index of the trigger
bar in that opportunity's own 04:00-16:00 bar series), `bars_before_pole` (= the pole's base index)
and `pole_len`. Every feature below is computed from `bars[:trigger_idx + 1]` **only** — the slice
is taken once, at the top of `_row_features`, and nothing downstream sees a later bar. Sub-windows
(`pre_pole = bars[:base]`, `pole = bars[base:peak+1]`) are all inside that slice because
`base <= peak <= trigger` holds by construction (asserted, and violations are counted and dropped).

The reference price throughout is `day_open` = the 04:00 bar's open, matching `regime_panel.py`.
`prev_close` is unavailable for the live half, so no gap feature can be built (same limitation the
panel documents).

Output: `data/spikes/engine-lab/validate/specification/prefeat.parquet`, keyed by the panel `key`.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import polars as pl
import speclab as S
from speclab import C

CACHE = S.OUT / "prefeat.parquet"


def _row_features(arr: np.ndarray, row: dict) -> dict:
    """`arr` is the FULL day series (n,5) = open, high, low, close, volume for this opportunity."""
    trig = int(row["trigger_idx"])
    base = int(row["bars_before_pole"])
    if trig >= len(arr) or trig < 0:
        return {}
    if not (0 <= base <= trig):
        return {"_bad_window": True}

    up = arr[: trig + 1]  # <-- the ONLY slice. Nothing after the trigger bar is visible below.
    o, h, lo, cl, v = up[:, 0], up[:, 1], up[:, 2], up[:, 3], up[:, 4]
    day_open = float(o[0])
    if day_open <= 0:
        return {}

    def ext(x: float) -> float:
        return (x - day_open) / day_open

    pre = up[:base]
    # `pole_len` counts strict higher-highs, not bars, so `peak_idx` is NOT derivable from it.
    # Every window below is therefore bounded by `base` and `trig` only, both of which are exact.
    post_base = up[base:]
    peak_high = float(post_base[:, 1].max()) if len(post_base) else float(h[0])

    f: dict = {}
    # --- "already running": extension measures, all relative to the 04:00 open ----------------
    f["ext_at_base"] = ext(float(o[base]))  # how far up when the pole STARTED
    f["ext_base_to_trigger"] = ext(peak_high)  # running high from pole base to the trigger bar
    f["ext_at_trigger_calc"] = ext(float(row["entry_fill"]))
    f["range_before_pole_calc"] = (
        (float(pre[:, 1].max()) - float(pre[:, 2].min())) / day_open if len(pre) else 0.0
    )
    f["hi_ext_pre_trigger"] = ext(float(h.max()))  # running high at the trigger bar
    f["lo_ext_pre_trigger"] = ext(float(lo.min()))
    f["range_pre_trigger_pct"] = (float(h.max()) - float(lo.min())) / day_open
    f["runup_to_pole"] = ext(float(pre[:, 1].max())) if len(pre) else 0.0
    # --- "already running": momentum over fixed look-backs -------------------------------------
    for n in (6, 12, 24):
        a = max(0, trig - n)
        f[f"ret_last{n}_to_trigger"] = float(o[trig]) / float(o[a]) - 1.0 if o[a] > 0 else 0.0
        b = max(0, base - n)
        f[f"ret_last{n}_to_pole"] = (
            float(o[base]) / float(o[b]) - 1.0 if base > 0 and o[b] > 0 else 0.0
        )
    # --- "already running": the pole itself and post-appearance move ---------------------------
    base_low = float(lo[base])
    f["pole_gain_calc"] = (peak_high - base_low) / base_low if base_low > 0 else 0.0
    fh = row.get("first_hit_et_min")
    if fh is not None and not (isinstance(fh, float) and np.isnan(fh)):
        k = int(max(0, min(trig, round(float(fh)) - 240)))  # bar index of the scanner appearance
        px = float(cl[k])
        f["ext_at_first_hit"] = ext(px)
        f["move_since_appearance"] = (float(row["entry_fill"]) - px) / px if px > 0 else 0.0
        f["bars_since_appearance"] = float(trig - k)
    else:
        f["ext_at_first_hit"] = None
        f["move_since_appearance"] = None
        f["bars_since_appearance"] = None
    # --- volatility / participation ------------------------------------------------------------
    with np.errstate(divide="ignore", invalid="ignore"):
        tr = np.where(cl > 0, (h - lo) / cl, 0.0)
    f["atr_pct_pre_trigger"] = float(np.nanmean(tr))
    f["atr_pct_pre_pole"] = float(np.nanmean(tr[:base])) if base > 0 else 0.0
    f["up_bar_frac_pre_pole"] = float((cl[:base] > o[:base]).mean()) if base > 0 else 0.0
    tv = float(v.sum())
    f["vwap_ext_at_trigger"] = (
        float(row["entry_fill"]) / float((cl * v).sum() / tv) - 1.0 if tv > 0 else 0.0
    )
    f["bars_to_trigger"] = float(trig)
    f["dollar_vol_pre_trigger"] = float((cl * v).sum())
    return f


def build(df: pl.DataFrame, *, cache: Path = CACHE, force: bool = False) -> pl.DataFrame:
    if cache.exists() and not force:
        got = pl.read_parquet(cache)
        if set(df["key"]) <= set(got["key"]):
            return got
    rows: list[dict] = []
    bad = 0
    for (source, d), grp in df.group_by(["source", "dt"], maintain_order=True):
        bars = C._day_bars(str(source), d)  # type: ignore[arg-type]
        if bars.is_empty():
            continue
        bars = bars.unique(subset=["opportunity_id", "bar_start_utc"], keep="first")
        et = bars["bar_start_utc"].dt.convert_time_zone("America/New_York")
        mins = (et.dt.hour().cast(pl.Int32) * 60 + et.dt.minute().cast(pl.Int32)).alias("etmin")
        bars = bars.with_columns(mins).filter((pl.col("etmin") >= 240) & (pl.col("etmin") < 960))
        by_oid = {
            str(k[0]): g.sort("bar_start_utc")
            .select(["open", "high", "low", "close", "volume"])
            .to_numpy()
            .astype(np.float64)
            for k, g in bars.group_by(["opportunity_id"])
        }
        for row in grp.iter_rows(named=True):
            arr = by_oid.get(row["opportunity_id"])
            if arr is None:
                continue
            f = _row_features(arr, row)
            if not f or f.get("_bad_window"):
                bad += 1
                continue
            rows.append({"key": row["key"], **f})
    out = pl.DataFrame(rows, infer_schema_length=None)
    print(f"prefeat: {out.height} rows built, {bad} dropped (bad window / missing bars)")
    cache.parent.mkdir(parents=True, exist_ok=True)
    out.write_parquet(cache)
    return out


def attach(df: pl.DataFrame) -> pl.DataFrame:
    """Panel + bar-derived features + the derived 'small' measures."""
    f = build(df)
    d = df.join(f, on="key", how="left")
    return d.with_columns(
        # --- alternative operationalisations of "small" ---------------------------------------
        (pl.col("shares_outstanding").cast(pl.Float64) * pl.col("entry_fill")).alias("mktcap"),
        (pl.col("shares_outstanding").cast(pl.Float64) * pl.col("day_open")).alias("mktcap_open"),
        (pl.col("float_shares").cast(pl.Float64) * pl.col("entry_fill")).alias("float_cap"),
        pl.col("entry_fill").alias("price"),
        (
            pl.col("float_shares").cast(pl.Float64) / pl.col("shares_outstanding").cast(pl.Float64)
        ).alias("float_ratio"),
    )


if __name__ == "__main__":
    df = S.panel()
    d = attach(df)
    cols = [
        "ext_at_base",
        "hi_ext_pre_trigger",
        "runup_to_pole",
        "ret_last12_to_trigger",
        "move_since_appearance",
        "vwap_ext_at_trigger",
        "mktcap",
        "price",
    ]
    print(d.select([pl.col(c).null_count().alias(c) for c in cols]))
    # sanity: the recomputed panel features must reproduce the panel's own
    for a, b in (
        ("ext_at_trigger_calc", "ext_at_trigger"),
        ("range_before_pole_calc", "range_before_pole_pct"),
    ):
        e = d.select((pl.col(a) - pl.col(b)).abs().max()).item()
        print(f"{a} vs {b}: max abs diff = {e}   <- validates the bar indexing")
