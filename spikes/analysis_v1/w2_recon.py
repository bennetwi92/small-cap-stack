"""W2 Stage 0 — the free reconnaissance (W2-0a/0b/0c). No outcome column, no post-trigger price.

.venv/bin/python spikes/analysis_v1/w2_recon.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import polars as pl

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

PUB = REPO / "data/spikes/panel-v1/publish"
TAPES = PUB / "tapes"
OUT = REPO / "data/spikes/w2"
OUT.mkdir(parents=True, exist_ok=True)

#: ET minutes-since-midnight from a UTC timestamp. ⚠️ `.dt.hour()` is i8 — cast before scaling.
ETMIN = pl.col("bar_start_utc").dt.convert_time_zone("America/New_York").dt.hour().cast(
    pl.Int32
) * 60 + pl.col("bar_start_utc").dt.convert_time_zone("America/New_York").dt.minute().cast(pl.Int32)


def fold_to_5m(b1: pl.DataFrame) -> pl.DataFrame:
    """first open / max high / min low / last close / sum volume on the 5-minute grid."""
    return (
        b1.with_columns(pl.col("bar_start_utc").dt.truncate("5m").alias("bucket"))
        .sort("bar_start_utc")
        .group_by(["opportunity_id", "bucket"])
        .agg(
            pl.col("open").first().alias("o1"),
            pl.col("high").max().alias("h1"),
            pl.col("low").min().alias("l1"),
            pl.col("close").last().alias("c1"),
            pl.col("volume").sum().alias("v1"),
            pl.len().alias("n_minutes"),
        )
    )


def main() -> None:
    b1 = pl.read_parquet(TAPES / "recon_bars_1m_fitcheck.parquet")
    b5 = pl.read_parquet(TAPES / "recon_bars_fitcheck.parquet")
    panel = pl.read_parquet(PUB / "panel-v1.parquet")
    out: dict[str, object] = {}

    # ---- W2-0a: coverage of bars_1m over the FIT+CHECK recon sessions in the panel -------------
    panel_fc = panel.filter(pl.col("split").is_in(["FIT", "CHECK"]))
    panel_oids = set(panel_fc["opportunity_id"])
    oids_1m = set(b1["opportunity_id"])
    oids_5m = set(b5["opportunity_id"])
    sess_panel = set(panel_fc["dt"])
    out["W2_0a"] = {
        "bars_1m_rows": b1.height,
        "bars_5m_rows": b5.height,
        "bars_1m_sessions": b1["dt"].n_unique(),
        "bars_5m_sessions": b5["dt"].n_unique(),
        "panel_fitcheck_sessions": len(sess_panel),
        "panel_fitcheck_rows": panel_fc.height,
        "panel_opportunity_ids": len(panel_oids),
        "panel_oids_with_1m": len(panel_oids & oids_1m),
        "panel_oids_with_5m": len(panel_oids & oids_5m),
        "panel_oid_1m_coverage_pct": round(100 * len(panel_oids & oids_1m) / len(panel_oids), 3),
        "panel_sessions_with_1m": len(sess_panel & set(b1["dt"])),
        "session_1m_coverage_pct": round(
            100 * len(sess_panel & set(b1["dt"])) / len(sess_panel), 3
        ),
    }

    # ---- W2-0c: recon session window on the 5-minute grid --------------------------------------
    last = (
        b5.with_columns(ETMIN.alias("etmin"))
        .group_by("dt")
        .agg(pl.col("etmin").max().alias("max_etmin"))
    )
    n_sess = last.height
    out["W2_0c"] = {
        "sessions": n_sess,
        "reach_1555_et": int(last.filter(pl.col("max_etmin") >= 955).height),
        "reach_1555_pct": round(100 * last.filter(pl.col("max_etmin") >= 955).height / n_sess, 3),
        "max_etmin_q10_q50_q90": [int(last["max_etmin"].quantile(q)) for q in (0.1, 0.5, 0.9)],
    }

    # ---- W2-0b: grid reconciliation ------------------------------------------------------------
    # bars_1m is [04:00, 09:30) raw minutes; bars is the full session aggregated to 5 min WITH
    # interior zero-volume filler. Compare on the traded buckets the 1-minute tape actually has.
    agg = fold_to_5m(b1)
    j = agg.join(
        b5.select(
            "opportunity_id",
            pl.col("bar_start_utc").alias("bucket"),
            pl.col("open").alias("o5"),
            pl.col("high").alias("h5"),
            pl.col("low").alias("l5"),
            pl.col("close").alias("c5"),
            pl.col("volume").alias("v5"),
        ),
        on=["opportunity_id", "bucket"],
        how="left",
    )
    matched = j.filter(pl.col("o5").is_not_null())
    tol = 1e-9
    diff = matched.with_columns(
        ((pl.col("o1") - pl.col("o5")).abs() > tol).alias("d_o"),
        ((pl.col("h1") - pl.col("h5")).abs() > tol).alias("d_h"),
        ((pl.col("l1") - pl.col("l5")).abs() > tol).alias("d_l"),
        ((pl.col("c1") - pl.col("c5")).abs() > tol).alias("d_c"),
        ((pl.col("v1") - pl.col("v5")).abs() > tol).alias("d_v"),
    )
    any_ohlc = diff.filter(pl.col("d_o") | pl.col("d_h") | pl.col("d_l") | pl.col("d_c"))
    out["W2_0b"] = {
        "buckets_from_1m": agg.height,
        "buckets_matched_in_5m": matched.height,
        "buckets_from_1m_missing_in_5m": int(agg.height - matched.height),
        "ohlc_mismatched_buckets": any_ohlc.height,
        "ohlc_mismatch_pct": round(100 * any_ohlc.height / matched.height, 6),
        "per_field_mismatches": {
            f: int(diff[f"d_{f[0]}"].sum()) for f in ("open", "high", "low", "close", "volume")
        },
        "note": "compared on buckets the 1-minute tape produces; the 5-minute store additionally "
        "carries the regular session and interior zero-volume filler candles",
    }
    if any_ohlc.height:
        out["W2_0b"]["examples"] = any_ohlc.head(5).to_dicts()

    # zero-volume filler inside the pre-market window that the 1-minute tape has no bucket for
    b5_pm = b5.with_columns(ETMIN.alias("etmin")).filter(
        (pl.col("etmin") >= 240) & (pl.col("etmin") < 570)
    )
    b5_pm_keys = b5_pm.select("opportunity_id", pl.col("bar_start_utc").alias("bucket"))
    extra = b5_pm_keys.join(
        agg.select("opportunity_id", "bucket"), on=["opportunity_id", "bucket"], how="anti"
    )
    extra_v = (
        b5_pm.join(
            extra.rename({"bucket": "bar_start_utc"}),
            on=["opportunity_id", "bar_start_utc"],
            how="semi",
        )
        if extra.height
        else None
    )
    out["W2_0b"]["premarket_5m_buckets"] = b5_pm.height
    out["W2_0b"]["premarket_5m_buckets_absent_from_1m"] = extra.height
    out["W2_0b"]["premarket_5m_absent_zero_volume"] = (
        int(extra_v.filter(pl.col("volume") == 0).height) if extra_v is not None else 0
    )

    # ---- entry geometry (trigger-time features only; free) -------------------------------------
    g = panel_fc.select(
        "entry_trigger", "entry_fill", "breakout_level", "stop", "stop_pct"
    ).drop_nulls()
    g = g.with_columns(
        ((pl.col("entry_trigger") - pl.col("breakout_level")) / 0.01).round(4).alias("trig_ticks"),
        ((pl.col("entry_fill") - pl.col("entry_trigger")) / 0.01).round(4).alias("fill_ticks"),
    )
    out["entry_geometry"] = {
        "trigger_minus_breakout_ticks_q10_q50_q90": [
            float(g["trig_ticks"].quantile(q)) for q in (0.1, 0.5, 0.9)
        ],
        "fill_minus_trigger_ticks_q10_q50_q90": [
            float(g["fill_ticks"].quantile(q)) for q in (0.1, 0.5, 0.9)
        ],
        "fill_ticks_value_counts": g["fill_ticks"].value_counts(sort=True).head(5).to_dicts(),
    }

    (OUT / "w2-stage0.json").write_text(json.dumps(out, indent=2, default=str))
    print(json.dumps(out, indent=2, default=str))


if __name__ == "__main__":
    main()
