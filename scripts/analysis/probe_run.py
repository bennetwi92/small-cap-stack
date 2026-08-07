"""Trace the engine's verdict for one symbol/date: the setup it found, every gate, and why the
run is or isn't takeable — the numbers the review page shows. Run ON THE VPS (needs /data).

    ssh -i ~/.ssh/oracle_scs root@<host> \\
      "SYMBOL=SNDQ DATE=2026-07-02 \\
       docker exec -i -e SYMBOL -e DATE small-cap-stack-app-1 python -" \\
      < scripts/analysis/probe_run.py

Two conventions this must match or it answers a different question than the page:

- **Measure over the full day, never the run window.** ``symbol_runs`` windows bars per run for
  the analysis, but every production caller (``report._analyze_run``, ``dashboard.build_charts``,
  ``portfolio.extract``) feeds ``day_chart_bars`` to the engine. The run window ends when the
  scanner stops hitting, which truncates a live trade at a boundary it never saw and hides the
  exhaustion cycles ``detect_day`` counts across the whole day (#180, and see the note in
  ``charts.py``). Probing on ``run.bars`` reports a different cycle standing on 6 of the repo's
  25 golden fixtures, and up to a 3.5x lower Max R on a symbol that pops twice.
- **Read only the requested date's partitions.** An unscoped ``store.read("bars")`` pulls the whole
  history into the tracker's 2 GB cgroup — the shape that OOM-killed the box in #264.
"""

import os
from datetime import date, datetime

import polars as pl

from small_cap_stack.bullflag import DaySetup, detect_day_with_settings
from small_cap_stack.capture import Bar, bar_interval
from small_cap_stack.config import Settings
from small_cap_stack.report import day_chart_bars, day_opportunities, symbol_runs
from small_cap_stack.rmetrics import compute_r_metrics
from small_cap_stack.storage import Store


def _verdict(setup: DaySetup) -> str:
    """Why the engine did or didn't take this setup, in the order the engine decides it."""
    if setup.takeable:
        return "TAKEABLE"
    reasons = [g.name for g in setup.gates if not g.passed]
    out = [f"gates({','.join(reasons)})"] if reasons else []
    if setup.exhausted:
        out.append(f"exhausted(cycle {setup.cycle_num})")
    if setup.trigger_idx is None:
        # Inside this branch a null trigger is ALWAYS staleness. "Never reached the trigger" makes
        # `detect_day` return None outright (see `_no_setup_reason`); a returned DaySetup with
        # trigger_idx=None is only produced by the staleness null-out, which requires a first_hit.
        out.append("triggered too late — outside the staleness window (#130)")
    return "not takeable: " + ", ".join(out)


def _no_setup_reason(ungated: DaySetup | None) -> str:
    """``detect_day`` returns None for several reasons; don't assert the wrong one.

    No cycles, no refinable pole, no valid trigger anywhere — or every candidate's trigger bar
    opened before the scanner appearance. Only the ungated re-run can tell those apart.

    Staleness is deliberately NOT offered as a cause here: it nulls ``trigger_idx`` on a DaySetup
    that is still returned, so it can never be why this returned None.
    """
    if ungated is not None:
        return (
            "no setup UNDER THE APPEARANCE GATE — the day does form one "
            f"(cycle {ungated.cycle_num}, trigger_idx={ungated.trigger_idx}); "
            "every candidate's trigger bar opens before first_hit (#99/#122)"
        )
    return "no setup: no pole formed, or no candidate ever reached a valid trigger"


def _print_setup(setup: DaySetup, day_bars: list[Bar]) -> None:
    seg = setup.segment
    print(
        f"  setup: pole[{seg.base_idx}..{seg.peak_idx}] len={seg.pole_len} "
        f"cons len={seg.cons_len} cycle={setup.cycle_num}"
        f"/{setup.total_significant_cycles} score={setup.score:.3f}"
    )
    print(
        f"  levels: breakout={setup.breakout_level} trigger={setup.entry_trigger} "
        f"fill={setup.entry_fill} stop={setup.stop}"
    )
    for g in setup.gates:
        print(f"    {'PASS' if g.passed else 'FAIL'}  {g.name}={g.value}")
    if setup.trigger_idx is not None:
        print(f"  trigger: bar {setup.trigger_idx} @ {day_bars[setup.trigger_idx].start}")


def _probe_run(day_bars: list[Bar], first_hit: datetime | None, st: Settings, label: str) -> None:
    print(label)
    if not day_bars:
        print("  no bars for this day")
        return
    print(f"  first_hit={first_hit}  day bars={len(day_bars)}  interval={bar_interval(day_bars)}")

    # Both over the FULL DAY, matching every production caller.
    setup = detect_day_with_settings(day_bars, st, first_hit)
    ungated = detect_day_with_settings(day_bars, st, None)

    if setup is None:
        print(f"  {_no_setup_reason(ungated)}")
    else:
        _print_setup(setup, day_bars)
        print(f"  verdict: {_verdict(setup)}")

    rm = compute_r_metrics(day_bars, st, first_hit=first_hit)
    print(
        f"  gated:   triggered={rm.triggered} takeable={rm.takeable} "
        f"max_r={rm.max_r} mae_r={rm.mae_r} stopped_out={rm.stopped_out}"
    )
    # The same day with the appearance/staleness gate removed. A difference means the engine saw
    # the setup but ruled it untradable on timing rather than shape — the usual answer to "why is
    # Max R lower than the chart suggests".
    ung_rm = compute_r_metrics(day_bars, st, first_hit=None)
    print(f"  no-gate: triggered={ung_rm.triggered} max_r={ung_rm.max_r}")


def main() -> None:
    symbol, trading_date = os.environ["SYMBOL"], date.fromisoformat(os.environ["DATE"])
    store, st = Store("/data"), Settings()
    print(
        f"staleness_min={st.entry_staleness_min} reentry_gap_min={st.reentry_gap_min} "
        f"reentry_lookback_min={st.reentry_lookback_min} "
        f"caps={st.bull_flag_max_pole}/{st.bull_flag_max_cons} "
        f"exhaustion_cap={st.bull_flag_exhaustion_cap}"
    )
    opps = day_opportunities(store, trading_date)
    if opps.is_empty() or "symbol" not in opps.columns:
        print(f"no opportunities captured for {trading_date}")
        return

    # dt-scoped: one date's partitions, not the archive.
    bars = store.read("bars", dt=trading_date)
    scans = store.read("scanner_hits", dt=trading_date)

    for row in opps.filter(pl.col("symbol") == symbol).iter_rows(named=True):
        # One full-day series per symbol, shared across its runs — exactly what build_charts does.
        day_bars = day_chart_bars(bars, row["opportunity_id"], st)
        for run in symbol_runs(row, bars, scans, st):
            _probe_run(
                day_bars,
                run.first_hit,
                st,
                f"\n=== {run.seg_id} (run {run.idx}/{run.run_count}, "
                f"run window {len(run.bars)} bars) ===",
            )


if __name__ == "__main__":
    main()
