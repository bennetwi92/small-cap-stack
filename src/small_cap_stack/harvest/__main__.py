"""CLI for the overnight harvest (#431): ``python -m small_cap_stack.harvest <command>``.

    auto     what the nightly timer runs: fill phase 1 if needed, then spend the night on 2
    daily    phase 1 — grouped-daily universe + previous closes for the window (~500 calls)
    run      phase 2 — minute bars per candidate, newest-first, until the night runs out
    fundamentals  point-in-time share counts from SEC EDGAR (#563) — free, no vendor budget
    charts   publish reconstructed sessions to the dashboard's chart namespace (#488), no calls
    sweep    the pre-flight measurement: candidates retained at each day-volume floor
    status   what is harvested, what is left, and what the next night would do

``run`` refuses to start outside 12:30–03:00 ET and stops itself well clear of the 03:45 ET
``eod_backfill`` — and an afternoon run stops at the 16:10 recess so it is never mid-session
during the 16:20/16:30 EOD jobs (#455). Overriding that takes **two** flags
(``--ignore-window --force``), on the #261 principle that a confirmation the caller can
auto-answer protects nobody — and on this box the
thing being protected is the live tracker's morning.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Collection, Sequence
from datetime import date, datetime, timedelta
from typing import Any

from ..clock import ET, now_et
from ..config import Settings, get_settings
from ..dashboard_recon import publish_recon_charts
from ..logging import configure_logging
from ..portfolio import collected_dates
from ..storage import Store
from .checkpoint import Checkpoint
from .fundamentals import edgar_source, plan_fundamentals, run_fundamentals
from .guard import RunWindow
from .prefilter import candidates, sweep_floors
from .runner import (
    SessionResult,
    checkpoint_path,
    effective_deadline,
    harvest_daily,
    harvest_store,
    plan_sessions,
    run_harvest,
    stored_universe,
)
from .source import HarvestError, MassiveSource


def _now() -> datetime:
    """The CLI's clock, in one place.

    ``run_harvest`` takes a ``now_fn`` and defaults it to its own ``datetime.now(UTC)``. Letting it
    do that means the window the CLI checked and the window the runner enforces are read from two
    different call sites — fine in production, but it is exactly the kind of split that hides a
    timezone or DST bug until a night is wasted. Pass this everywhere instead.
    """
    return datetime.now(ET)


def _window(s: Settings) -> RunWindow:
    return RunWindow(start=s.harvest_start_et, stop=s.harvest_stop_et)


def _live_dates(s: Settings) -> list[date]:
    """Dates the live store already collected — never worth spending a night on (#430)."""
    return collected_dates(Store(s.data_dir))


def _plan(
    s: Settings,
    today: date,
    done: Collection[date],
    live: Sequence[date],
    cp: Checkpoint,
) -> list[date]:
    """Every planning call in one place, so none of them can forget the entitlement floor (#440).

    A command that planned without ``not_before`` would report a backlog including dates the vendor
    will not sell, and — for ``daily``/``auto`` — spend a call every night rediscovering that. The
    bug this fixes was a guard applied at one of two call sites; the fix should not reintroduce the
    same shape.
    """
    return plan_sessions(
        s,
        today=today,
        done=sorted(done),
        live_dates=live,
        not_before=cp.entitlement_floor,
    )


#: Minutes the EOD jobs themselves need after `eod_report` fires, before harvesting may resume.
#: Only used to estimate usable hours for `sweep`; the real resume is the 17:15 timer fire.
_EOD_BLOCK_MIN = 30


def _eod_gap_hours(s: Settings) -> float:
    """Hours the day loses to the EOD recess (#455) — the window minus this is usable harvesting.

    The window is one span, but the day is two working periods with the EOD jobs between them.
    Counting that gap as harvesting time would have `sweep` recommend a day-volume floor against
    ~an hour a day that does not exist — and recommending the floor is the whole point of `sweep`.
    """
    gap_start = datetime.combine(date.min, s.harvest_eod_recess_et)
    gap_end = datetime.combine(date.min, s.eod_report) + timedelta(minutes=_EOD_BLOCK_MIN)
    return max(0.0, (gap_end - gap_start).total_seconds() / 3600.0)


def _print(payload: object) -> None:
    print(json.dumps(payload, indent=2, default=str))


def _session_reporter(s: Settings) -> Callable[[SessionResult], None]:
    """Report each finished session — and publish its chart payload as it lands (#488).

    Per-session rather than per-night on purpose. A session is ~47 minutes of rate-limited waiting
    and building one date's charts is seconds of compute, so the work disappears into a budget that
    is already dominated by ``time.sleep``; batching it to the end of the night would instead put an
    archive-shaped job right where the run is trying to stop clear of the 03:45 ``eod_backfill`` or
    the 16:20 EOD batch. It also means a night killed mid-run has published everything it harvested,
    which is the same contract the checkpoint gives.

    Failures are swallowed by :func:`publish_recon_charts` itself; this wrapper catches the rest
    (an unwritable dashboard dir, say) for the same reason — a dashboard artifact must never be able
    to cost a night of vendor budget.
    """

    def report(r: SessionResult) -> None:
        print(r.line(), file=sys.stderr)
        if not r.complete:
            return  # an abandoned session wrote nothing; there is no day to chart
        try:
            publish_recon_charts(s, dates=[r.trading_date])
        except Exception as exc:  # noqa: BLE001 — never let the dashboard stop the harvest
            # Type as well as message (#511): this runs unattended under scs-harvest.timer, and
            # `str(exc)` is the empty string for a bare TimeoutError — the shape most likely here.
            print(
                f"warning: publishing charts for {r.trading_date} failed: "
                f"{type(exc).__name__}: {exc}",
                file=sys.stderr,
            )

    return report


def cmd_status(s: Settings, args: argparse.Namespace) -> int:
    store = harvest_store(s)
    cp = Checkpoint.load(checkpoint_path(s))
    today = args.today or now_et().date()
    live = _live_dates(s)
    pending = _plan(s, today, cp.done, live, cp)
    daily_pending = _plan(s, today, cp.daily_done, live, cp)
    now = _now()
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
            # The oldest date the vendor will sell, as discovered rather than as configured (#440).
            # `null` means the lookback has never been refused; a date here means the real window is
            # shorter than `harvest_lookback_days` asks for, and the plan above already reflects it.
            "entitlement_floor": (
                cp.entitlement_floor.isoformat() if cp.entitlement_floor else None
            ),
            "lookback_days": s.harvest_lookback_days,
            # Sessions whose bars are stored but whose share counts are not (#563). Read off the
            # partitions on disk, not off the checkpoint — see harvest/fundamentals.py.
            "fundamentals_pending": len(plan_fundamentals(store)),
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
    pending = _plan(s, today, cp.daily_done, _live_dates(s), cp)
    # plan_sessions returns newest-first (phase 2's order); phase 1 walks ascending so each
    # session's previous close is the response already in hand.
    todo = sorted(pending)[: args.limit] if args.limit else sorted(pending)
    if not todo:
        _print({"phase": "daily", "pending": 0})
        return 0
    source = MassiveSource.from_env(rate_sleep_sec=s.harvest_rate_sleep_sec)
    win = _window(s)
    deadline = None if args.ignore_window else effective_deadline(win, s, _now())
    results = harvest_daily(source, store, s, todo, checkpoint=cp, deadline=deadline, now_fn=_now)
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
    win = _window(s)
    if args.ignore_window and not args.force:
        print(
            f"error: --ignore-window also needs --force. Running outside {win.describe()} puts a "
            "multi-week job next to the tracker's own morning; two deliberate flags, not one.",
            file=sys.stderr,
        )
        return 2
    now = _now()
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
    pending = _plan(s, today, cp.done, _live_dates(s), cp)
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
        now_fn=_now,
        on_session=_session_reporter(s),
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


def cmd_auto(s: Settings, args: argparse.Namespace) -> int:
    """Advance the harvest by whatever it needs next — what the nightly timer runs.

    The two phases are an implementation detail of the vendor's data, not something an operator
    should have to sequence by hand at 18:00. Before this, the timer ran ``run`` unconditionally:
    on a box where phase 1 had never happened that skipped **every** session with "no universe",
    spent nothing, and reported success — a job that looks like it is working and is not.

    So: fill phase 1 first (it is a hard prerequisite — #428 measured the previous close as a
    required input, not a nicety), then spend whatever is left of the night on phase 2. On night one
    that is ~1.8 h of grouped-daily followed by ~6 h of minute bars; on every night after, phase 1
    is already complete and it goes straight to phase 2.
    """
    blocked = _window_blocks(s, args)
    if blocked is not None:
        return blocked
    today = args.today or now_et().date()
    cp = Checkpoint.load(checkpoint_path(s))
    live = _live_dates(s)
    store = harvest_store(s)
    source = MassiveSource.from_env(rate_sleep_sec=s.harvest_rate_sleep_sec)
    win = _window(s)
    deadline = effective_deadline(win, s, _now())

    daily_todo = sorted(_plan(s, today, cp.daily_done, live, cp))
    daily_results = []
    if daily_todo:
        print(f"phase 1: {len(daily_todo)} sessions need a universe", file=sys.stderr)
        daily_results = harvest_daily(
            source, store, s, daily_todo, checkpoint=cp, deadline=deadline, now_fn=_now
        )

    # Re-plan against the checkpoint phase 1 just updated, so a session whose universe landed
    # moments ago is eligible tonight rather than waiting for tomorrow.
    pending = _plan(s, today, cp.done, live, cp)
    run = run_harvest(
        source,
        store,
        s,
        pending,
        checkpoint=cp,
        window=win,
        ignore_window=args.ignore_window,
        max_sessions=args.limit,
        now_fn=_now,
        on_session=_session_reporter(s),
    )
    fundamentals = _fill_fundamentals(s, store, run.completed)
    _print(
        {
            "phase": "auto",
            "daily_sessions": len(daily_results),
            "daily_remaining": len(_plan(s, today, cp.daily_done, live, cp)),
            "harvested": [d.isoformat() for d in run.completed],
            "stopped_because": run.stopped_because,
            "calls": source.calls,
            "peak_rss_mb": round(run.peak_rss_mb, 1),
            "fundamentals": fundamentals,
        }
    )
    return 0


def _fill_fundamentals(s: Settings, store: Store, dates: Sequence[date]) -> dict[str, Any]:
    """Share counts for the sessions this night harvested (#563), best-effort.

    Scoped to tonight's dates rather than the whole backlog: this runs *after* the night's stop
    condition has already fired, so it must be bounded by something the night controls. A session
    is ~30 opportunities and EDGAR memoises per symbol, so that is seconds — the backlog is what
    ``harvest fundamentals`` is for.

    Every failure is swallowed, including an unset ``HARVEST_EDGAR_USER_AGENT``, for the reason the
    chart publish hook gives: a free enrichment must never be able to fail a night that spent real
    vendor budget. The dates stay pending and the next run picks them up.
    """
    if not dates:
        return {"dates": 0}
    try:
        results = run_fundamentals(edgar_source(s), store, s, dates)
    except Exception as exc:  # noqa: BLE001 — never let enrichment fail a harvested night
        print(f"warning: EDGAR share counts skipped: {type(exc).__name__}: {exc}", file=sys.stderr)
        return {"dates": 0, "error": f"{type(exc).__name__}: {exc}"}
    done = [r for r in results if r.complete]
    return {
        "dates": len(done),
        "with_shares": sum(r.resolved for r in done),
        "without_shares": sum(r.unresolved for r in done),
        "failed_symbols": sum(r.failed for r in results),
    }


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
    # How many calls a day's worth of window buys at the fixed sleep; a session costs one
    # grouped-daily call plus one per candidate. Sessions-per-day is the number that decides
    # whether tightening the floor is worth anything.
    #
    # Derived from the CONFIGURED window, not a hardcoded 8 hours (#455). This said `8 * 3600`
    # while the window was 17:00-03:00 — already an under-count — and widening the window to
    # 12:30-03:00 would have left `sweep` recommending a floor against a day 70% shorter than the
    # one the harvest actually gets, which is precisely the decision this command exists to inform.
    window_hours = _window(s).length_hours() - _eod_gap_hours(s)
    night_calls = window_hours * 3600 / s.harvest_rate_sleep_sec
    _print(
        {
            "phase": "sweep",
            "dates": len(per_date),
            "current_floor": s.harvest_min_day_volume,
            "mean_candidates_per_day": {
                str(int(f)): round(totals[f] / n, 1) for f in sorted(totals)
            },
            "window_hours": round(window_hours, 2),
            "sessions_per_day": {
                str(int(f)): round(night_calls / (totals[f] / n + 1.0), 1)
                for f in sorted(totals)
                if totals[f]
            },
            "per_date": per_date,
        }
    )
    return 0


def cmd_charts(s: Settings, args: argparse.Namespace) -> int:
    """Publish reconstructed sessions to the dashboard's chart namespace (#488) — no API calls.

    The catch-up path. ``run``/``auto`` publish each session as they harvest it, so this exists for
    the backlog a box accumulated before that hook landed, for a date whose payload was lost, and
    for re-pruning after ``recon_charts_max_dates`` changes. With no ``--dates`` it fills the window
    with the newest harvested sessions that have no payload, and is idempotent — a date that already
    has a payload and an index row is not rebuilt, so the ordinary call does nothing. With
    ``--dates`` it republishes exactly those, which is how an evicted session is brought back (doing
    so moves it to the front of the eviction window).

    It spends no vendor budget and touches no checkpoint, but it **does** take the ``scs-harvest``
    lock: it read-modify-writes ``recon_index.json``, and so does the per-session publish hook
    inside a running ``run``/``auto``. Interleaved, one of them writes an index built from a stale
    snapshot — a dropped row and an orphaned payload. It also does real work (DuckDB + polars + the
    detector, per date), so it runs inside the harvest's memory slice rather than the smaller,
    slice-less envelope ``status``/``sweep``/``prefilter`` get. ``--limit`` bounds one call.
    """
    dates = [date.fromisoformat(d) for d in args.dates] if args.dates else None
    res = publish_recon_charts(s, dates=dates, limit=args.limit)
    _print({"phase": "charts", **res.summary()})
    return 0


def cmd_fundamentals(s: Settings, args: argparse.Namespace) -> int:
    """Fill point-in-time share counts for harvested sessions from SEC EDGAR (#563).

    Spends no vendor budget — EDGAR is free — so it is not window-guarded like ``daily``/``run``.
    What it does spend is a few seconds of box CPU per date and one HTTPS call per *distinct
    symbol* (a company's whole filing history arrives in one response and is memoised for the run),
    so the whole backlog is minutes rather than nights.

    With no ``--dates`` it fills every harvested session that has none, newest-first. With
    ``--dates`` it rebuilds exactly those, dropping the existing partition first — which is how a
    date is refreshed after a filing is amended, or after a re-harvest changed which symbols
    appeared.
    """
    store = harvest_store(s)
    source = edgar_source(s)
    dates = [date.fromisoformat(d) for d in args.dates] if args.dates else plan_fundamentals(store)
    if args.limit:
        dates = dates[: args.limit]
    results = run_fundamentals(
        source, store, s, dates, on_result=lambda r: print(r.line(), file=sys.stderr)
    )
    done = [r for r in results if r.complete]
    _print(
        {
            "phase": "fundamentals",
            "dates": len(done),
            "abandoned": [r.trading_date.isoformat() for r in results if not r.complete],
            "opportunities": sum(r.opportunities for r in done),
            "with_shares": sum(r.resolved for r in done),
            "without_shares": sum(r.unresolved for r in done),
            "failed_symbols": sum(r.failed for r in results),
            "calls": source.calls,
            "remaining": len(plan_fundamentals(store)),
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
    p.add_argument(
        "command",
        choices=["status", "auto", "daily", "run", "fundamentals", "charts", "sweep", "prefilter"],
    )
    p.add_argument(
        "--limit",
        type=int,
        default=0,
        help="cap sessions (daily/run), dates (charts/fundamentals) or rows (sweep)",
    )
    p.add_argument("--today", type=date.fromisoformat, help="override 'today' (testing/backdating)")
    p.add_argument("--dates", nargs="*", help="explicit dates for sweep/charts/fundamentals")
    p.add_argument(
        "--floors",
        default="100000,250000,500000,1000000,2000000",
        help="day-volume floors to sweep (comma-separated)",
    )
    p.add_argument(
        "--ignore-window",
        action="store_true",
        help="run outside the harvest window (needs --force for `run`)",
    )
    p.add_argument("--force", action="store_true", help="confirm --ignore-window")
    args = p.parse_args(argv)

    s = get_settings()
    configure_logging(level=s.log_level, json_logs=s.json_logs)
    handlers = {
        "status": cmd_status,
        "auto": cmd_auto,
        "daily": cmd_daily,
        "run": cmd_run,
        "fundamentals": cmd_fundamentals,
        "charts": cmd_charts,
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
