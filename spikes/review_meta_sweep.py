"""Spike #173 Stage E: replay-backtest candidate gate/param changes over the reviewed day-set.

Re-runs compute_r_metrics per run under each candidate Settings override and scores it against the
trader's ground-truth labels (tradeable vs no_trigger, shipped via REVIEWS_B64). Pure replay — the
engine is already backcastable over the cached bars. Run ON THE VPS.

    DATES=... REVIEWS_B64=<...> docker exec -i -e DATES -e REVIEWS_B64 ... python -
"""

import base64
import json
import os
from datetime import date

from small_cap_stack.config import Settings
from small_cap_stack.report import day_opportunities, symbol_runs
from small_cap_stack.rmetrics import compute_r_metrics
from small_cap_stack.storage import Store


def labels() -> dict[str, dict]:
    raw = os.environ.get("REVIEWS_B64", "")
    out: dict[str, dict] = {}
    if raw:
        for r in json.loads(base64.b64decode(raw)):
            oid = r.get("opportunity_id")
            if oid:
                out[str(oid)] = r
    return out


def main() -> None:
    dates = [date.fromisoformat(d.strip()) for d in os.environ["DATES"].split(",") if d.strip()]
    revs = labels()
    store = Store("/data")
    bars, scans = store.read("bars"), store.read("scanner_hits")

    # Enumerate runs once (segmentation uses the base settings — geometry gates re-run per config).
    base = Settings()
    runs = []  # (seg_id, run_bars, first_hit, is_tradeable, is_notrig)
    for d in dates:
        for orow in day_opportunities(store, d).iter_rows(named=True):
            for run in symbol_runs(orow, bars, scans, base):
                if not run.bars:
                    continue
                ann = revs.get(run.seg_id)
                runs.append(
                    (
                        run.seg_id,
                        run.bars,
                        run.first_hit,
                        ann is not None and not ann.get("no_trigger"),
                        ann is not None and bool(ann.get("no_trigger")),
                    )
                )

    def score(stale: int, retr: float, wick: float) -> dict:
        s = Settings(
            entry_staleness_min=stale,
            bull_flag_max_retracement=retr,
            bull_flag_max_peak_wick=wick,
        )
        tp = fp = 0  # trader-tradeable that now trigger / trader-no_trigger that now trigger
        capt = 0.0  # engine captured R summed over trader-tradeable runs
        for _seg, rb, fh, tradeable, notrig in runs:
            rm = compute_r_metrics(rb, s, first_hit=fh)
            if rm.triggered:
                if tradeable:
                    tp += 1
                    capt += rm.max_r or 0.0
                if notrig:
                    fp += 1
        return {
            "stale": stale,
            "retr": retr,
            "wick": wick,
            "tp": tp,
            "fp": fp,
            "captured_R": round(capt, 2),
        }

    b = (base.entry_staleness_min, base.bull_flag_max_retracement, base.bull_flag_max_peak_wick)
    print(f"BASELINE stale={b[0]} retr={b[1]} wick={b[2]}:")
    print(f"  {score(*b)}")

    print("\nSTALENESS sweep (retr=0.50, wick=0.50 fixed):")
    for st in (30, 45, 60, 90, 120, 240):
        print(f"  {score(st, 0.50, 0.50)}")

    print("\nRETRACEMENT sweep (stale=30, wick=0.50 fixed):")
    for rt in (0.40, 0.45, 0.50, 0.55):
        print(f"  {score(30, rt, 0.50)}")

    print("\nPOLE-WICK sweep (stale=30, retr=0.50 fixed):")
    for wk in (0.30, 0.40, 0.50):
        print(f"  {score(30, 0.50, wk)}")

    print("\n2-D corners (staleness x retracement, wick=0.50):")
    for st in (30, 90):
        for rt in (0.40, 0.50):
            print(f"  {score(st, rt, 0.50)}")

    n_tradeable = sum(1 for r in runs if r[3])
    n_notrig = sum(1 for r in runs if r[4])
    print(f"\n(universe: {len(runs)} runs; tradeable={n_tradeable} no_trigger={n_notrig})")
    print("tp=trader-tradeable that trigger (want high); fp=trader-skip that trigger (want low)")


if __name__ == "__main__":
    main()
