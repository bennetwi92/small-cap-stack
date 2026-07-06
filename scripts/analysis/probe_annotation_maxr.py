"""Replay the review page's annotation Max R for one run, and the corrected value.

Run ON THE VPS (needs /data). The trader's annotations come in via env as JSON, so this file
carries no per-run state:

    ssh -i ~/.ssh/oracle_scs root@<host> \\
      "ANN='{...}' OID='2026-07-02:SNDQ#2' \\
       docker exec -i -e ANN -e OID small-cap-stack-app-1 python -" \\
      < scripts/analysis/probe_annotation_maxr.py

Pull ANN first (locally):
  gh api "repos/<repo>/contents/reviews/<file>?ref=review-data" --jq .content | base64 -d
"""

import json
import os
from datetime import UTC, date, datetime, timedelta, timezone

from small_cap_stack.config import Settings
from small_cap_stack.dashboard import build_charts
from small_cap_stack.storage import Store

ET = timezone(timedelta(hours=-4))  # EDT; good enough for readable printouts


def js_compute_maxr(bars, entry, stop, entry_t):
    """Faithful port of docs/review.js computeMaxR() — the bar AT entry_t is the fill bar."""
    risk = entry - stop
    if risk <= 0:
        return None
    max_high = float("-inf")
    started = False
    for b in bars:
        if b["t"] < entry_t:
            continue
        if not started:
            started = True
            if b["l"] <= stop:  # same-bar stop on the entry_t bar -> credits nothing
                max_high = entry
                break
            max_high = b["h"]
            continue
        if b["l"] <= stop:
            break
        max_high = max(max_high, b["h"])
    return None if max_high == float("-inf") else (max_high - entry) / risk


def fixed_compute_maxr(bars, entry, stop, anchor_t):
    """Entry is a horizontal price level; the tap's x is meaningless. The fill is the first bar
    STRICTLY AFTER the anchor (the consolidation's end `consolidation.t1`, else `entry_t`) whose
    high reaches entry. Measure Max R (stop-first) from that fill bar (engine semantics)."""
    risk = entry - stop
    if risk <= 0:
        return None
    fill = next((i for i, b in enumerate(bars) if b["t"] > anchor_t and b["h"] >= entry), None)
    if fill is None:
        return None  # never triggered
    if bars[fill]["l"] <= stop:
        return 0.0
    max_high = bars[fill]["h"]
    for b in bars[fill + 1 :]:
        if b["l"] <= stop:
            break
        max_high = max(max_high, b["h"])
    return (max_high - entry) / risk


def main() -> None:
    ann = json.loads(os.environ["ANN"])
    oid = os.environ["OID"]
    entry, stop, entry_t = ann["entry"], ann["stop"], ann["entry_t"]
    # Prefer the consolidation's end as the fill anchor; the entry tap's x (entry_t) is unreliable.
    anchor_t = ann.get("consolidation", {}).get("t1", entry_t)
    trading_date = date.fromisoformat(oid[:10])
    now = datetime.now(UTC).replace(microsecond=0)

    payload = build_charts(Store("/data"), Settings(), trading_date, now)
    chart = next(c for c in payload["charts"] if c["opportunity_id"] == oid)
    bars = chart["bars"]

    print(
        f"{oid}: entry={entry} stop={stop} risk={entry - stop:.4f} "
        f"entry_t={datetime.fromtimestamp(entry_t, ET):%H:%M} "
        f"anchor(cons.t1)={datetime.fromtimestamp(anchor_t, ET):%H:%M} ET"
    )
    print(f"  saved max_r (page)     : {ann.get('max_r')}")
    print(f"  current computeMaxR()  : {js_compute_maxr(bars, entry, stop, entry_t)}")
    print(f"  fixed (anchor=cons.t1) : {fixed_compute_maxr(bars, entry, stop, anchor_t)}")


if __name__ == "__main__":
    main()
