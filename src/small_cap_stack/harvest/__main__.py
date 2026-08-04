"""CLI for the overnight harvest (#431): ``python -m small_cap_stack.harvest <command>``.

    daily    phase 1 — grouped-daily universe + previous closes for the window (~500 calls)
    run      phase 2 — minute bars per candidate, newest-first, until the night runs out
    sweep    the pre-flight measurement: candidates retained at each day-volume floor
    status   what is harvested, what is left, and what the next night would do

``run`` refuses to start outside 17:00–03:00 ET and stops itself well clear of the 03:45 ET
``eod_backfill``. Overriding that takes **two** flags (``--ignore-window --force``), on the #261
principle that a confirmation the caller can auto-answer protects nobody — and on this box the
thing being protected is the live tracker's morning.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import date, datetime

from ..clock import ET, now_et
from ..config import Settings, get_settings
from ..logging import configure_logging
from ..portfolio import collected_dates
from ..storage import Store
from .checkpoint import Checkpoint
from .guard import RunWindow
from .prefilter import candidates, sweep_floors
from .runner import (
    checkpoint_path,
    harvest_daily,
    harvest_store,
    plan_sessions,
    run_harvest,
    stored_universe,
)
from .source import HarvestError, MassiveSource


def _window(s: Settings) -> RunWindow:
    return RunWindow(start=s.harvest_start_et, stop=s.harvest_stop_et)


def _live_dates(s: Settings) -> list[date]:
    """Dates the live store already collected — never worth spending a night on (#430)."""
    return collected_dates(Store(s.data_dir))


def _print(payload: object) -> None:
    print(json.dumps(payload, indent=2, default=str))


def cmd_status(s: Settings, args: argparse.Namespace) -> int:
    store = harvest_store(s)
    cp = Checkpoint.load(checkpoint_path(s))
    today = args.today or now_et().date()
    pending = plan_sessions(s, today=today, done=sorted(cp.done), live_dates=_live_dates(s))
    daily_pending = plan_sessions(
        s, today=today, done=sorted(cp.daily_done), live_dates=_live_dates(s)
    )
    now = datetime.now(ET)
    win = _window(s)
    _print(
        {
            "store": str(store.data_dir),
            "window": win.describe(),
            "window_open_now": win.is_open(now),
            "next_hard_stop": win.deadline(now).isoformat(),
            "sessions_done": len(cp.done),
            "sessions_pending": len(pending),
            "daily_done": len(cp.daily_done),
            "daily_pending": len(daily_pending),
            "calls_spent": cp.calls,
            "harvested_span": [
                cp.oldest.isoformat() if cp.oldest else None,
                cp.newest.isoformat() if cp.newest else None,
            ],
            "next_sessions": [d.isoformat() for d in pending[:10]],
        }
    )
    return 0


def cmd_daily(s: Settings, args: argparse.Namespace) -> int:
    # Phase 1 is cheaper than phase 2 but not cheap: ~500 calls is ~2 hours at the free tier's
    # fixed sleep, which is long enough to run from the scan window into the 16:20 EOD batch.
    blocked = _window_blocks(s, args)
    if blocked is not None:
        return blocked
    store = harvest_store(s)
    cp = Checkpoint.load(checkpoint_path(s))
    today = args.today or now_et().date()
    pending = plan_sessions(s, today=today, done=sorted(cp.daily_done), live_dates=_live_dates(s))
    # plan_sessions returns newest-first (phase 2's order); phase 1 walks ascending so each
    # session's previous close is the response already in hand.
    todo = sorted(pending)[: args.limit] if args.limit else sorted(pending)
    if not todo:
        _print({"phase": "daily", "pending": 0})
        return 0
    source = MassiveSource.from_env(rate_sleep_sec=s.harvest_rate_sleep_sec)
    win = _window(s)
    deadline = None if args.ignore_window else win.deadline(datetime.now(ET))
    results = harvest_daily(source, store, s, todo, checkpoint=cp, deadline=deadline)
    _print(
        {
            "phase": "daily",
            "sessions": len(results),
            "calls": source.calls,
            "rows": sum(r.rows for r in results),
            "first": results[0].trading_date.isoformat() if results else None,
            "last": results[-1].trading_date.isoformat() if results else None,
        }
    )
    return 0


def _window_blocks(s: Settings, args: argparse.Namespace) -> int | None:
    """The window check both vendor-spending commands share. Returns an exit code, or None to go.

    It lives here rather than only in ``run_harvest`` because ``daily`` never went through that
    function: it computed a *deadline* but never asked whether the window was open at all, so a
    dispatch at 05:00 ET would have started ~500 calls and two hours of work straight through the
    scan window. A guard that covers one of two vendor-spending commands is not a guard.
    """
    if args.ignore_window and not args.force:
        print(
            "error: --ignore-window also needs --force. Running outside 17:00-03:00 ET puts a "
            "45-night job next to the tracker's own morning; two deliberate flags, not one.",
            file=sys.stderr,
        )
        return 2
    win = _window(s)
    now = datetime.now(ET)
    if not args.ignore_window and not win.is_open(now):
        print(
            f"refusing to start at {now:%H:%M} ET — the harvest window is {win.describe()}. "
            f"It next opens at {s.harvest_start_et:%H:%M} ET.",
            file=sys.stderr,
        )
        return 3
    return None


def cmd_run(s: Settings, args: argparse.Namespace) -> int:
    blocked = _window_blocks(s, args)
    if blocked is not None:
        return blocked
    store = harvest_store(s)
    cp = Checkpoint.load(checkpoint_path(s))
    today = args.today or now_et().date()
    pending = plan_sessions(s, today=today, done=sorted(cp.done), live_dates=_live_dates(s))
    source = MassiveSource.from_env(rate_sleep_sec=s.harvest_rate_sleep_sec)
    run = run_harvest(
        source,
        store,
        s,
        pending,
        checkpoint=cp,
        window=_window(s),
        ignore_window=args.ignore_window,
        max_sessions=args.limit,
        on_session=lambda r: print(r.line(), file=sys.stderr),
    )
    _print(
        {
            "phase": "run",
            "summary": run.summary(),
            "completed": [d.isoformat() for d in run.completed],
            "stopped_because": run.stopped_because,
            "calls": run.calls,
            "peak_rss_mb": round(run.peak_rss_mb, 1),
        }
    )
    return 0


def cmd_sweep(s: Settings, args: argparse.Namespace) -> int:
    """Candidate counts at several day-volume floors — no API calls, stored rows only.

    #431 asks for this *before the first full night*: the 100k floor sets the ~217 candidates/day
    that prices the harvest at ~45 nights, and halving the candidate set halves the calendar.
    """
    store = harvest_store(s)
    floors = [float(x) for x in args.floors.split(",")]
    # `--limit 0` means "every stored date", matching what it means for `daily`/`run`. Slicing
    # with a literal 0 would instead sweep nothing and report an empty table as a result.
    partitions = sorted((store.data_dir / "daily_universe").glob("dt=*"), reverse=True)
    dates = args.dates or [p.name.removeprefix("dt=") for p in partitions[: args.limit or None]]
    per_date = []
    totals: dict[float, int] = dict.fromkeys(floors, 0)
    for raw in dates:
        d = date.fromisoformat(raw) if isinstance(raw, str) else raw
        rows = stored_universe(store, d)
        if not rows:
            continue
        table = sweep_floors(rows, floors)
        for entry in table:
            totals[float(entry["floor"])] += int(entry["candidates"])
        per_date.append({"date": d.isoformat(), "universe": len(rows), "floors": table})
    n = len(per_date) or 1
    # An 8-hour night buys this many calls at the free tier's fixed sleep; a session costs one
    # grouped-daily call plus one per candidate. Sessions-per-night is the number that decides
    # whether tightening the floor is worth anything — 45 nights is 500 sessions at ~11/night.
    night_calls = 8 * 3600 / s.harvest_rate_sleep_sec
    _print(
        {
            "phase": "sweep",
            "dates": len(per_date),
            "current_floor": s.harvest_min_day_volume,
            "mean_candidates_per_day": {
                str(int(f)): round(totals[f] / n, 1) for f in sorted(totals)
            },
            "sessions_per_8h_night": {
                str(int(f)): round(night_calls / (totals[f] / n + 1.0), 1)
                for f in sorted(totals)
                if totals[f]
            },
            "per_date": per_date,
        }
    )
    return 0


def cmd_prefilter(s: Settings, args: argparse.Namespace) -> int:
    """The candidate list a session would fetch — the cheap way to see what a night will do."""
    store = harvest_store(s)
    d = args.today or now_et().date()
    rows = stored_universe(store, d)
    cands = candidates(rows, min_day_volume=s.harvest_min_day_volume)
    _print(
        {
            "date": d.isoformat(),
            "universe": len(rows),
            "candidates": len(cands),
            "estimated_minutes": round(len(cands) * s.harvest_rate_sleep_sec / 60.0, 1),
            "top": [
                {"symbol": c.symbol, "change_pct": c.day_change_pct, "day_volume": c.day_volume}
                for c in cands[: args.limit or 25]
            ],
        }
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="python -m small_cap_stack.harvest",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("command", choices=["status", "daily", "run", "sweep", "prefilter"])
    p.add_argument("--limit", type=int, default=0, help="cap sessions (daily/run) or rows (sweep)")
    p.add_argument("--today", type=date.fromisoformat, help="override 'today' (testing/backdating)")
    p.add_argument("--dates", nargs="*", help="explicit dates for sweep")
    p.add_argument(
        "--floors",
        default="100000,250000,500000,1000000,2000000",
        help="day-volume floors to sweep (comma-separated)",
    )
    p.add_argument(
        "--ignore-window",
        action="store_true",
        help="run outside 17:00-03:00 ET (needs --force for `run`)",
    )
    p.add_argument("--force", action="store_true", help="confirm --ignore-window")
    args = p.parse_args(argv)

    s = get_settings()
    configure_logging(level=s.log_level, json_logs=s.json_logs)
    handlers = {
        "status": cmd_status,
        "daily": cmd_daily,
        "run": cmd_run,
        "sweep": cmd_sweep,
        "prefilter": cmd_prefilter,
    }
    try:
        return handlers[args.command](s, args)
    except HarvestError as exc:  # a missing key / vendor outage is operator error, not a crash
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
