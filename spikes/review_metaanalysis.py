"""Spike (issue #173): cross-day meta-analysis of review-page feedback.

Run ON THE VPS (needs /data). Builds one flat row per run over a set of reviewed days, joining:
  - engine R-metrics (compute_r_metrics over the run window, with the appearance/staleness gate),
  - the trader's review annotation (shipped in via REVIEWS_B64) and the *corrected* annotation
    Max R (fill anchored at consolidation.t1, per the /review-analysis entry_t fix) — NOT the
    buggy saved annotations.max_r,
  - flag-time metadata (rank persistence, float/short%, news recency) and candidate features
    (first_hit time-of-day, run volume proxies).

Emits the rows as a JSON array on STDOUT (captured locally for the mining stages); a human summary
+ the verification assertions go to STDERR.

    DATES=2026-07-01,2026-07-02
    REVIEWS_B64=<base64 of a JSON list of review objects (each carries its own opportunity_id)>

    ssh -i ~/.ssh/oracle_scs root@<host> \\
      "DATES=$DATES REVIEWS_B64=$REVIEWS_B64 \\
       docker exec -i -e DATES -e REVIEWS_B64 small-cap-stack-app-1 python -" \\
      < spikes/review_metaanalysis.py > data/spikes/review_meta.json

The reviews bundle is assembled locally (gh is authed on the Mac, the Store lives on the box).
"""

import base64
import json
import os
import statistics
import sys
import traceback
from datetime import UTC, date, datetime, timedelta, timezone

from small_cap_stack.config import Settings
from small_cap_stack.dashboard import build_charts
from small_cap_stack.report import (
    _FLOAT_PRIORITY,
    _funds_for,
    _news_for,
    _news_recent,
    _pick_by_source,
    day_opportunities,
    symbol_runs,
)
from small_cap_stack.rmetrics import compute_r_metrics
from small_cap_stack.storage import Store

ET = timezone(timedelta(hours=-4))  # EDT; fine for time-of-day bucketing on these dates


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def fixed_compute_maxr(bars, entry, stop, anchor_t):
    """Corrected annotation Max R — fill = first bar STRICTLY AFTER the anchor whose high reaches
    entry; measure stop-first from there (engine semantics). Ported from
    scripts/analysis/probe_annotation_maxr.py:fixed_compute_maxr."""
    if entry is None or stop is None:
        return None
    risk = entry - stop
    if risk <= 0:
        return None
    fill = next((i for i, b in enumerate(bars) if b["t"] > anchor_t and b["h"] >= entry), None)
    if fill is None:
        return None  # never triggered under the trader's entry
    if bars[fill]["l"] <= stop:
        return 0.0
    max_high = bars[fill]["h"]
    for b in bars[fill + 1 :]:
        if b["l"] <= stop:
            break
        max_high = max(max_high, b["h"])
    return round((max_high - entry) / risk, 3)


def load_reviews() -> dict[str, dict]:
    """seg_id -> review object, from the base64 bundle (each review carries its opportunity_id)."""
    raw = os.environ.get("REVIEWS_B64", "")
    if not raw:
        return {}
    items = json.loads(base64.b64decode(raw))
    out: dict[str, dict] = {}
    for r in items:
        oid = r.get("opportunity_id")
        if oid:
            out[str(oid)] = r
    return out


def run_volume_features(bars) -> dict:
    """Cheap run-level volume proxies from the run window bars (pole/cons magnitudes are a #TODO —
    the bull-flag detector doesn't expose its bar indices)."""
    vols = [b.volume for b in bars if b.volume is not None]
    if not vols:
        return {"peak_vol": None, "median_vol": None, "rvol_proxy": None, "total_vol": None}
    peak = max(vols)
    med = statistics.median(vols)
    return {
        "peak_vol": peak,
        "median_vol": med,
        "rvol_proxy": round(peak / med, 2) if med else None,
        "total_vol": sum(vols),
    }


def rank_features(scans, base_oid: str) -> dict:
    """Scanner rank persistence for the base opportunity (currently unused in gating/stats)."""
    sub = scans.filter(scans["opportunity_id"] == base_oid) if not scans.is_empty() else scans
    if scans.is_empty() or sub.is_empty():
        return {"n_hits": 0, "min_rank": None, "hit_span_min": None}
    ranks = [int(r) for r in sub["rank"].to_list() if r is not None]
    ts = sorted(sub["ts_utc"].to_list())
    span = (ts[-1] - ts[0]).total_seconds() / 60.0 if len(ts) >= 2 else 0.0
    return {
        "n_hits": sub.height,
        "min_rank": min(ranks) if ranks else None,
        "hit_span_min": round(span, 1),
    }


def et_time_features(first_hit: datetime | None) -> dict:
    if first_hit is None:
        return {"first_hit_et": None, "premarket": None, "min_after_open": None}
    et = first_hit.astimezone(ET)
    open_et = et.replace(hour=9, minute=30, second=0, microsecond=0)
    return {
        "first_hit_et": et.strftime("%H:%M"),
        "premarket": et.time() < open_et.time(),
        "min_after_open": round((et - open_et).total_seconds() / 60.0, 1),
    }


def main() -> None:
    dates = [date.fromisoformat(d.strip()) for d in os.environ["DATES"].split(",") if d.strip()]
    reviews = load_reviews()
    settings = Settings()
    store = Store("/data")
    now = datetime.now(UTC).replace(microsecond=0)

    bars = store.read("bars")
    scans = store.read("scanner_hits")
    news = store.read("news")
    funds = store.read("fundamentals")

    rows: list[dict] = []
    errors: list[str] = []
    parity_fail: list[str] = []

    for d in dates:
        # Per-run full-day charts (parity with the review page): seg_id -> chart.
        charts = {c["opportunity_id"]: c for c in build_charts(store, settings, d, now)["charts"]}
        opps = day_opportunities(store, d)
        for orow in opps.iter_rows(named=True):
            for run in symbol_runs(orow, bars, scans, settings):
                try:
                    seg = run.seg_id
                    base_oid = seg.split("#")[0]
                    if not run.bars:
                        continue
                    rm = compute_r_metrics(run.bars, settings, first_hit=run.first_hit)
                    chart = charts.get(seg)
                    chart_bars = chart["bars"] if chart else []

                    # parity: engine max_r from the harness must match what the page renders.
                    if chart is not None and rm.max_r != chart["max_r"]:
                        parity_fail.append(f"{seg}: harness={rm.max_r} chart={chart['max_r']}")

                    float_shares, short_pct = _funds_for(funds, base_oid)
                    fsub = (
                        funds.filter(funds["opportunity_id"] == base_oid)
                        if not funds.is_empty()
                        else funds
                    )
                    shares_out = (
                        _pick_by_source(
                            list(fsub.iter_rows(named=True)), "shares_outstanding", _FLOAT_PRIORITY
                        )
                        if not funds.is_empty() and not fsub.is_empty()
                        else None
                    )
                    news_times, _undated = _news_for(news, base_oid)
                    ann = reviews.get(seg, {})
                    a = ann.get("annotations", {}) or {}
                    entry = a.get("entry")
                    stop = a.get("stop")
                    anchor_t = (a.get("consolidation") or {}).get("t1") or a.get("entry_t")
                    corrected = (
                        fixed_compute_maxr(chart_bars, entry, stop, anchor_t)
                        if entry is not None and stop is not None and anchor_t is not None
                        else None
                    )

                    row = {
                        "date": d.isoformat(),
                        "seg_id": seg,
                        "symbol": run.symbol,
                        "run": run.idx,
                        "run_count": run.run_count,
                        # --- outcome (engine) ---
                        "eng_triggered": rm.triggered,
                        "eng_max_r": rm.max_r,
                        "eng_mae_r": rm.mae_r,
                        "eng_stopped_out": rm.stopped_out,
                        "eng_initial_risk": rm.initial_risk,
                        "eng_entry": rm.entry_trigger,
                        "eng_stop": rm.stop,
                        "eng_bars_to_max_r": rm.bars_to_max_r,
                        # --- outcome (trader ground truth) ---
                        "reviewed": bool(ann),
                        "no_trigger": ann.get("no_trigger"),
                        "ann_entry": entry,
                        "ann_stop": stop,
                        "ann_saved_max_r": a.get("max_r"),
                        "ann_corrected_max_r": corrected,
                        "note": ann.get("note", ""),
                        # entry gap: trader vs engine levels (None when either missing)
                        "entry_delta": (
                            round(entry - rm.entry_trigger, 4)
                            if entry is not None and rm.entry_trigger is not None
                            else None
                        ),
                        "stop_delta": (
                            round(stop - rm.stop, 4)
                            if stop is not None and rm.stop is not None
                            else None
                        ),
                        # --- geometry (derived) ---
                        "setup_found": rm.setup_found,
                        "pole_len": rm.pole_len,
                        "flag_len": rm.flag_len,
                        "retracement": rm.retracement,
                        "cons_vol_reducing": rm.cons_vol_reducing,
                        "pole_has_big_green": rm.pole_has_big_green,
                        # --- flag metadata (known) ---
                        "first_rank": orow.get("first_rank"),
                        "float_shares": float_shares,
                        "shares_outstanding": (
                            int(shares_out) if isinstance(shares_out, int | float) else None
                        ),
                        "short_percent": short_pct,
                        "news_count": len(news_times) + _undated,
                        "news_recent": _news_recent(news_times, d),
                        # --- candidate features (unused today) ---
                        **et_time_features(run.first_hit),
                        **rank_features(scans, base_oid),
                        **run_volume_features(run.bars),
                    }
                    rows.append(row)
                except Exception as exc:  # keep one bad run from sinking the batch
                    errors.append(f"{run.seg_id}: {exc!r}")
                    errors.append(traceback.format_exc())

    # ---- STDOUT: the data ----
    print(json.dumps(rows))

    # ---- STDERR: summary + verification ----
    log(f"\n=== review meta-analysis: {len(rows)} runs over {[d.isoformat() for d in dates]} ===")
    reviewed = [r for r in rows if r["reviewed"]]
    log(f"reviewed runs: {len(reviewed)} / {len(rows)}")
    log(f"engine triggered: {sum(1 for r in rows if r['eng_triggered'])}")
    log(f"trader no_trigger: {sum(1 for r in reviewed if r['no_trigger'])}")
    for r in errors:
        log(f"ERROR {r}")
    for p in parity_fail:
        log(f"PARITY-FAIL {p}")
    if not parity_fail:
        log("PARITY-OK: harness engine max_r matches build_charts for all runs")

    # spot-check: SNDQ#2 corrected ~6.14R while saved is 0 (the /review-analysis fix)
    sndq = next((r for r in rows if r["seg_id"] == "2026-07-02:SNDQ#2"), None)
    if sndq:
        log(
            f"\nSPOTCHECK 2026-07-02:SNDQ#2  saved_max_r={sndq['ann_saved_max_r']}  "
            f"corrected_max_r={sndq['ann_corrected_max_r']}  (expect saved~0, corrected~6.14)"
        )


if __name__ == "__main__":
    main()
