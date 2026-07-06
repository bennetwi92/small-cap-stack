"""Trace the engine R-metrics for one symbol/date: run segmentation, appearance/staleness gates,
and the engine Max R the review page shows. Run ON THE VPS (needs /data).

    ssh -i ~/.ssh/oracle_scs root@<host> \\
      "SYMBOL=SNDQ DATE=2026-07-02 \\
       docker exec -i -e SYMBOL -e DATE small-cap-stack-app-1 python -" \\
      < scripts/analysis/probe_run.py
"""

import os
from datetime import date, timedelta

import polars as pl

from small_cap_stack.config import Settings
from small_cap_stack.report import day_opportunities, symbol_runs
from small_cap_stack.rmetrics import (
    _first_trigger,
    _iter_setups,
    bar_interval,
    compute_r_metrics,
)
from small_cap_stack.storage import Store


def main() -> None:
    symbol, trading_date = os.environ["SYMBOL"], date.fromisoformat(os.environ["DATE"])
    s, st = Store("/data"), Settings()
    print(
        f"staleness_min={st.entry_staleness_min} reentry_gap_min={st.reentry_gap_min} "
        f"reentry_lookback_min={st.reentry_lookback_min}"
    )
    bars, scans = s.read("bars"), s.read("scanner_hits")
    for row in (
        day_opportunities(s, trading_date).filter(pl.col("symbol") == symbol).iter_rows(named=True)
    ):
        for run in symbol_runs(row, bars, scans, st):
            if not run.bars:
                print(f"\n=== {run.seg_id}: no bars ===")
                continue
            interval = bar_interval(run.bars)
            fh = run.first_hit
            stale = timedelta(minutes=st.entry_staleness_min)
            print(f"\n=== {run.seg_id} (run {run.idx}/{run.run_count}) ===")
            print(f"  first_hit={fh}  bars={len(run.bars)}  interval={interval}")
            for setup_idx, bf in _iter_setups(run.bars, st):
                if round(bf.entry_trigger - bf.stop, 6) <= 0:
                    continue
                trig = _first_trigger(run.bars, setup_idx, bf.entry_trigger)
                if trig is None:
                    print(
                        f"  setup@{setup_idx} entry={bf.entry_trigger} "
                        f"stop={bf.stop} -> never triggers"
                    )
                    continue
                tb = run.bars[trig]
                if fh is not None and tb.start + interval <= fh:
                    verdict = "closed_before_appearance"
                elif fh is not None and tb.start >= fh + stale:
                    verdict = "STALE(#130)"
                else:
                    verdict = "TAKEN"
                print(
                    f"  setup@{setup_idx} entry={bf.entry_trigger} stop={bf.stop} "
                    f"-> trig@{trig} {tb.start} [{verdict}]"
                )
            rm = compute_r_metrics(run.bars, st, first_hit=fh)
            print(f"  gated:   triggered={rm.triggered} max_r={rm.max_r}")
            print(f"  no-gate: {compute_r_metrics(run.bars, st, first_hit=None).max_r}")


if __name__ == "__main__":
    main()
