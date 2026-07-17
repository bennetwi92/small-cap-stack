"""Infra + strategy watchdogs (#340/#341): plain-Python thresholds over the public payloads.

Runs on a GitHub-HOSTED runner on a schedule (``.github/workflows/watchdog.yml``) — never
on the box and never against it: it reads only what the publish pipeline already made public
(``status.json`` / ``published.json`` on the ``dashboard-data`` branch), so a monitor run adds
zero box load and needs zero secrets beyond the workflow's own ``GITHUB_TOKEN``. **No model is
involved** — thresholds are plain Python; a model runs only after an alert exists (self-heal,
#336).

Review-driven design (#340 comments, ``research/github-automation.md`` §10):

- **Deterministic keys.** Every check has a fixed slug and a Python-generated issue title
  (``[watchdog] infra/<slug> — …``); dedup and auto-close never depend on model output.
- **Two staleness sources.** ``generated_utc`` (written by the box each tick) vs
  ``published_utc`` (written by the publish workflow) split "box down" from "publish pipeline
  down" — never restart a healthy box over a publish-only failure. A stale publish also *is* the
  runner-offline check (the publish schedule runs on the self-hosted runner), so runner state
  itself never appears on the public surface (#344).
- **Indeterminate beats wrong.** While the publish is stale, or when the payload's
  ``generated_utc`` hasn't advanced since the previous sample, box-derived checks return None:
  streaks freeze, so an old payload can neither open a false alert nor quietly close a real one.
- **Hysteresis.** Open after ``open_after`` consecutive breaching runs, close after
  ``close_after`` clean runs; a re-breach REOPENS the closed issue instead of filing a new one —
  the anti-flap/cooldown: one problem, one issue, however often it oscillates.
- **Reset-aware counters.** A cumulative counter that went *down* means the app restarted — new
  baseline, not a breach.

Strategy checks (#341) ride the same run and machinery but answer a different question — "is the
tracker doing its *job*", not "is the box alive" — and open issues labelled ``strategy`` instead
of ``infra``:

- **Scanner liveness ≠ opportunity count.** The liveness proxy is scanner-hit throughput inside
  the scan sub-window (pre-market 04:30–11:59 ET is where the setups fire); **0 opportunities is
  a valid, healthy outcome** and is never floored > 0.
- **EOD completion** is judged from ``stats.json``'s ``trading_date`` against the most recent
  session whose EOD deadline passed — calendar/half-day aware (EOD crons stay at 16:20/16:30 ET
  on a 13:00 close).
- **Opportunity-count anomaly, both directions,** against a trailing per-day distribution the
  monitor accumulates in its own state (warm-up guard + wide bands while P1 data is thin). The
  low side only fires when the scan window has closed, the trailing median is substantial, AND
  the scanner was alive all morning.
- **Feed staleness:** at P1 there is nothing intraday to measure lag-minus-15-min-baseline
  against (bars are an EOD batch by design; scanner rows carry no exchange timestamps), so
  scanner-hit liveness is the observable proxy; the lag-drift check lands with P2 streaming
  (#350).

State (per-key streaks + the last counter sample + the opportunity distribution) lives in
``infra_state.json`` on the
single-commit ``monitor-state`` branch; the workflow loads it before and force-pushes it after
each run — the same keep-the-repo-small pattern as ``dashboard-data``.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import urllib.request
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from .clock import ET
from .market_calendar import is_trading_day

RAW_BASE = "https://raw.githubusercontent.com/bennetwi92/small-cap-stack/dashboard-data"
TITLE_PREFIX = "[watchdog] "

# Trading days only, but the FULL day incl. the EOD jobs. Early closes are deliberately not
# clipped: the box's EOD crons stay at 16:20/16:30 ET even on a 13:00 close (decisions.md), so
# infra coverage stays full-window on half days too.
MONITOR_START = time(4, 0)
MONITOR_END = time(17, 0)
# The scan sub-window (#341): the app scans 04:00–11:59 ET; liveness judgement starts at 04:30
# so the first ticks of a slow pre-market never false-alarm.
SCAN_SUB_START = time(4, 30)
SCAN_SUB_END = time(11, 59)
# A session's EOD (bars 16:20 / report 16:30 ET) is "due" once this passes. Monitor runs end at
# 17:00 ET, so in practice a missed EOD is caught from the next run onward (e.g. 04:00 next day).
EOD_DEADLINE = time(17, 0)


@dataclass(frozen=True)
class Thresholds:
    publish_stale_min: float = 45.0  # publish cadence is 15 min; 3 misses = pipeline down
    # Box staleness is judged AT COPY TIME (published_utc - generated_utc), so publish/schedule
    # lag can never be misread as a dead box: with a 60s tick a healthy box is seconds behind
    # its own publish, whatever the copy's age is by the time the monitor reads it.
    box_stale_min: float = 10.0
    dataset_files_max: int = 2000  # read cost tracks FILE count (#318/#319); compaction is #328
    open_after: int = 2  # consecutive breaching runs before an issue opens (M)
    close_after: int = 3  # consecutive clean runs before it auto-closes (K)
    # Strategy thresholds (#341). Band tuning is an open question (spec §9) — start wide.
    scanner_silent_min: float = 30.0  # no stored scanner hit for this long in-window = broken
    opp_warmup_days: int = 10  # no distribution alerting until this many days are recorded
    opp_history_days: int = 20  # trailing window for the opportunity-count distribution
    opp_high_floor: int = 10  # high-side band never tighter than this absolute count
    opp_high_factor: float = 3.0  # ...or this multiple of the trailing median
    opp_low_min_median: float = 8.0  # low side only fires when the median is at least this


@dataclass(frozen=True)
class Check:
    """One evaluated check. ``breached`` None = indeterminate: freeze streaks, take no action."""

    slug: str
    breached: bool | None
    title: str  # deterministic — with the slug it forms the dedup title, so no volatile parts
    detail: str  # issue-body text, built ONLY from data that is already public


@dataclass(frozen=True)
class Evaluation:
    checks: list[Check]
    sample: dict[str, Any]  # generated_utc + counter baseline to persist for the next run


@dataclass
class KeyState:
    breach_streak: int = 0
    ok_streak: int = 0
    issue: int | None = None


@dataclass(frozen=True)
class Action:
    kind: str  # "open" | "close"
    check: Check
    issue: int | None = None  # known issue number for "close"; None = look it up (lost state)


def _age_min(stamp: object, now: datetime) -> float | None:
    if not isinstance(stamp, str) or not stamp:
        return None
    try:
        then = datetime.fromisoformat(stamp)
    except ValueError:
        return None
    if then.tzinfo is None:
        then = then.replace(tzinfo=UTC)
    return (now - then).total_seconds() / 60.0


def _counter(health: dict[str, Any], name: str) -> int | None:
    val = health.get(name)
    return val if isinstance(val, int) else None


def evaluate(
    status: dict[str, Any] | None,
    published: dict[str, Any] | None,
    prev_sample: dict[str, Any],
    now: datetime,
    th: Thresholds,
) -> Evaluation:
    """Evaluate every infra check against the fetched payloads — pure, no I/O."""
    checks: list[Check] = []

    pub_age = _age_min((published or {}).get("published_utc"), now)
    pub_stale = pub_age is None or pub_age > th.publish_stale_min
    pub_detail = (
        "`published.json` is missing or unreadable — the publish pipeline has produced nothing "
        "fetchable."
        if pub_age is None
        else f"`published_utc` is {pub_age:.0f} min old (cadence: 15 min)."
    )
    checks.append(
        Check(
            "infra/publish-stale",
            pub_stale,
            "publish pipeline stale — dashboard-data not updating",
            pub_detail
            + " The publish-dashboard schedule runs on the self-hosted runner, so this usually "
            "means the runner/service is down (post-OOM trap — deploy/RUNBOOK.md §11) or GitHub "
            "schedule lag. The box itself may be fine: box-side checks are suspended until the "
            "publish recovers — do not restart the box on this alert alone.",
        )
    )

    gen = (status or {}).get("generated_utc")
    gen_age = _age_min(gen, now)
    # Staleness AT COPY TIME: how far behind was the box when the publish snapshotted it? Using
    # `now - generated_utc` here would blame the box for mere publish/schedule lag (observed
    # live on 2026-07-17: a 35-min-late publish made a healthy box look 36 min stale).
    copy_gap = None if gen_age is None or pub_age is None else gen_age - pub_age
    if pub_stale:
        box_stale: bool | None = None  # payload is old news — can't tell anything about the box
    else:
        box_stale = copy_gap is None or copy_gap > th.box_stale_min
    box_detail = (
        "status.json carries no readable `generated_utc` — the box has never written a status "
        "snapshot, or wrote a malformed one."
        if copy_gap is None
        else f"at the last publish the box's `generated_utc` was already {copy_gap:.0f} min "
        "old (a healthy 60s tick is seconds behind its own publish) — the app/tick loop on the "
        "box looks dead."
    )
    checks.append(
        Check(
            "infra/box-stale",
            box_stale,
            "box stale — status.json not updating",
            box_detail + " Check `docker ps` and `systemctl status small-cap-stack` over SSH; a "
            "deploy interrupted by a runner restart can leave the app container stopped "
            "(CLAUDE.md). Runbook: deploy/RUNBOOK.md.",
        )
    )

    # Box-derived checks below are indeterminate ("frozen") whenever the payload can't have new
    # information: publish stale, no generated_utc, or the same snapshot as last run.
    frozen = pub_stale or gen is None or gen == prev_sample.get("generated_utc")
    health: dict[str, Any] = (status or {}).get("health") or {}
    prev_counters: dict[str, Any] = prev_sample.get("counters") or {}

    for name, slug, title, hint in (
        (
            "ticks_over_budget_total",
            "infra/tick-over-budget",
            "ticks running over budget",
            "over-budget ticks make the scheduler silently skip the next tick — scanner gaps in "
            "the Phase-1 dataset (#321). Check status.json's coarse `tick` state and the box "
            "load.",
        ),
        (
            "jobs_missed_total",
            "infra/jobs-missed",
            "scheduled jobs skipped",
            "scheduled jobs are being skipped entirely (max_instances/misfire, #321) — an EOD "
            "batch may not have run.",
        ),
    ):
        cur = _counter(health, name)
        prev_v = prev_counters.get(name)
        breached: bool | None
        if frozen or cur is None:
            breached = None
        elif not isinstance(prev_v, int) or cur < prev_v:
            breached = False  # first sample, or counter reset = app restart: new baseline
        else:
            breached = cur > prev_v
        delta = (
            f"`{name}` rose {prev_v} → {cur} between monitor samples — "
            if isinstance(prev_v, int) and cur is not None
            else f"`{name}` is {cur} — "
        )
        checks.append(Check(slug, breached, title, delta + hint))

    data: dict[str, Any] = (status or {}).get("data") or {}
    worst_name, worst = "", -1
    for ds_name, ds in data.items():
        files = ds.get("files") if isinstance(ds, dict) else None
        if isinstance(files, int) and files > worst:
            worst_name, worst = ds_name, files
    checks.append(
        Check(
            "infra/dataset-files",
            None if frozen or worst < 0 else worst > th.dataset_files_max,
            "Parquet file-count explosion",
            f"dataset `{worst_name}` is at {worst} files (threshold {th.dataset_files_max}). For "
            "this store read cost tracks FILE count, not rows (#318/#319/#321) — run the "
            "compaction (#328) before ticks start missing their budget.",
        )
    )

    for field, slug, title, hint in (
        (
            "mem_ok",
            "infra/mem",
            "host memory headroom low",
            "the box reports host memory headroom below its floor (`mem_ok: false`). Exact "
            "numbers are deliberately not public (#344) — check `free -m` over SSH. OOM history: "
            "#264/#273; do NOT start backfills or other heavy jobs until this clears.",
        ),
        (
            "disk_ok",
            "infra/disk",
            "disk usage above ceiling",
            "the box reports /data's filesystem above its usage ceiling (`disk_ok: false`). "
            "Exact numbers are deliberately not public (#344) — check `df -h` over SSH.",
        ),
    ):
        val = health.get(field)
        checks.append(
            Check(
                slug,
                None if frozen or not isinstance(val, bool) else not val,
                title,
                f"`{field}: false` in status.json — " + hint,
            )
        )

    if frozen:
        sample = dict(prev_sample)
    else:
        counters = {
            n: c
            for n in ("ticks_over_budget_total", "jobs_missed_total")
            if (c := _counter(health, n)) is not None
        }
        sample = {"generated_utc": gen, "counters": counters}
    return Evaluation(checks, sample)


def _prev_session(d: date) -> date | None:
    for back in range(1, 11):
        candidate = d - timedelta(days=back)
        if is_trading_day(candidate):
            return candidate
    return None


def _median(values: list[int]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[mid])
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def evaluate_strategy(
    status: dict[str, Any] | None,
    stats: dict[str, Any] | None,
    published: dict[str, Any] | None,
    strat_state: dict[str, Any],
    now: datetime,
    th: Thresholds,
) -> tuple[list[Check], dict[str, Any]]:
    """Evaluate the strategy checks (#341) — pure; returns (checks, next strategy state).

    Every check goes indeterminate whenever the infra side says the payload is unusable (publish
    or box stale) — one problem, one alert: the infra checks own that failure.
    """
    local = now.astimezone(ET)
    today = local.date()
    trading = is_trading_day(today)
    # Same staleness judgement as evaluate() — recomputed here so both stay independently pure.
    # Like the box check, ages inside the payload are judged AT COPY TIME (minus the publish
    # age), so publish/schedule lag never masquerades as a strategy failure.
    pub_age = _age_min((published or {}).get("published_utc"), now)
    gen_age = _age_min((status or {}).get("generated_utc"), now)
    copy_gap = None if gen_age is None or pub_age is None else gen_age - pub_age
    infra_bad = (
        pub_age is None
        or pub_age > th.publish_stale_min
        or copy_gap is None
        or copy_gap > th.box_stale_min
    )
    checks: list[Check] = []
    next_state = dict(strat_state)

    # Scanner liveness (#341): candidate throughput inside the scan sub-window is the proxy —
    # deliberately decoupled from opportunity count, which may be 0 on a healthy day.
    in_sub = trading and SCAN_SUB_START <= local.time() <= SCAN_SUB_END
    scan_age = _age_min(((status or {}).get("scanner") or {}).get("last_scan_utc"), now)
    scan_gap = None if scan_age is None or pub_age is None else scan_age - pub_age
    if infra_bad or not in_sub:
        scanner_silent: bool | None = None
    else:
        scanner_silent = scan_gap is None or scan_gap > th.scanner_silent_min
        if scanner_silent:
            next_state["scanner_silent_date"] = today.isoformat()
    checks.append(
        Check(
            "strategy/scanner-silent",
            scanner_silent,
            "scanner silent during the scan window",
            (
                "no scanner hit stored yet today"
                if scan_gap is None
                else f"the newest stored scanner hit was already {scan_gap:.0f} min old at the "
                "last publish"
            )
            + f" inside the 04:30–11:59 ET scan window (threshold {th.scanner_silent_min:.0f} "
            "min). A Warrior-style gapper scan virtually always returns rows on a live feed, so "
            "this reads as a broken feed/scanner subscription, not a quiet market. Check "
            "`scs_ibkr_connected`, the Gateway, and the data-farm status in the app logs.",
        )
    )

    # EOD completion (#341): stats.json is rewritten by the 16:30 ET report job, so its
    # trading_date must equal the most recent session whose EOD deadline has passed.
    due = today if (trading and local.time() >= EOD_DEADLINE) else _prev_session(today)
    stats_raw = (stats or {}).get("trading_date")
    try:
        stats_date = date.fromisoformat(stats_raw) if isinstance(stats_raw, str) else None
    except ValueError:
        stats_date = None
    if infra_bad or due is None:
        eod_missing: bool | None = None
    else:
        eod_missing = stats_date is None or stats_date < due
    checks.append(
        Check(
            "strategy/eod-missing",
            eod_missing,
            "EOD batch did not complete",
            f"stats.json reports trading_date `{stats_raw}` but the most recent completed "
            f"session is `{due}` — the EOD batch (bars 16:20 / report 16:30 ET) did not finish. "
            "The retry path (#100) may be exhausted; check the app logs, then re-run the EOD "
            "jobs. Do NOT run a backfill while any infra alert is open (OOM history, "
            "#264/#273).",
        )
    )

    # Opportunity-count distribution (#341): accumulate max-per-day in monitor state, then watch
    # both tails. Never floored > 0 — 0 opportunities with a live scanner is healthy.
    opp_raw = ((status or {}).get("opportunities") or {}).get("open_today")
    opp_today = opp_raw if isinstance(opp_raw, int) else None
    counts: dict[str, int] = {
        k: int(v)
        for k, v in (strat_state.get("opp_counts") or {}).items()
        if isinstance(v, int | float)
    }
    if not infra_bad and trading and opp_today is not None:
        key = today.isoformat()
        counts[key] = max(counts.get(key, 0), opp_today)
        counts = dict(sorted(counts.items())[-2 * th.opp_history_days :])
    next_state["opp_counts"] = counts
    trailing = [v for k, v in sorted(counts.items()) if k < today.isoformat()]
    trailing = trailing[-th.opp_history_days :]
    warmed = len(trailing) >= th.opp_warmup_days
    med = _median(trailing)
    high_limit = max(float(th.opp_high_floor), th.opp_high_factor * med)

    base_indeterminate = infra_bad or not trading or not warmed
    high = None if opp_today is None or base_indeterminate else opp_today > high_limit
    checks.append(
        Check(
            "strategy/opps-high",
            high,
            "opportunity count anomalously high",
            f"{opp_today} opportunities today vs a trailing median of {med:.0f} "
            f"(band: > {high_limit:.0f}). A count this far above the distribution usually means "
            "a gate regression (float source down → everything passes) or a scanner/config "
            "change — check the most recent deploy and the fundamentals source before trusting "
            "today's dataset.",
        )
    )

    # Low side: only meaningful once the scan window is over, the scanner was alive all morning,
    # and history says a blank day would be genuinely unusual.
    after_window = trading and local.time() > SCAN_SUB_END
    scanner_dead_today = next_state.get("scanner_silent_date") == today.isoformat()
    low_indeterminate = (
        base_indeterminate or not after_window or scanner_dead_today or med < th.opp_low_min_median
    )
    low = None if opp_today is None or low_indeterminate else opp_today == 0
    checks.append(
        Check(
            "strategy/opps-low",
            low,
            "zero opportunities on a live scanner",
            f"0 opportunities today against a trailing median of {med:.0f}, with the scanner "
            "alive all morning. 0 is a valid outcome, but at this median it is unusual enough "
            "to check the gates: float source coverage, news matching, and the change/volume "
            "thresholds (compute-on-read makes today's raw data replayable once fixed).",
        )
    )
    return checks, next_state


def step(
    keys: dict[str, KeyState], checks: Iterable[Check], th: Thresholds
) -> tuple[dict[str, KeyState], list[Action]]:
    """Advance the hysteresis state machine — pure; issue numbers are filled in by the caller."""
    out: dict[str, KeyState] = {}
    actions: list[Action] = []
    for c in checks:
        prev = keys.get(c.slug, KeyState())
        k = KeyState(prev.breach_streak, prev.ok_streak, prev.issue)
        if c.breached is True:
            k.breach_streak += 1
            k.ok_streak = 0
            # Re-emitted every breaching run until an issue number is recorded: open_alert() is
            # idempotent (it adopts an existing open issue), so a failed/lost open self-heals.
            if k.issue is None and k.breach_streak >= th.open_after:
                actions.append(Action("open", c))
        elif c.breached is False:
            k.ok_streak += 1
            k.breach_streak = 0
            if k.issue is not None and k.ok_streak >= th.close_after:
                actions.append(Action("close", c, issue=k.issue))
                k.issue = None
            elif k.issue is None and k.ok_streak == th.close_after:
                # Lost-state recovery (one-shot): if an open alert exists that this state file
                # doesn't know about, close it now that the check has been clean for K runs.
                actions.append(Action("close", c, issue=None))
        out[c.slug] = k
    return out, actions


# --- GitHub edge (thin, deterministic — the model never names or manages issues) ----------------


def _gh(args: list[str]) -> str:
    res = subprocess.run(["gh", *args], check=True, capture_output=True, text=True)
    return res.stdout.strip()


def _labels_for(slug: str) -> tuple[str, str]:
    return ("alert", "strategy") if slug.startswith("strategy/") else ("alert", "infra")


def _issue_title(c: Check) -> str:
    return f"{TITLE_PREFIX}{c.slug} — {c.title}"


def _issue_body(c: Check, th: Thresholds) -> str:
    return (
        f"{c.detail}\n\n---\nOpened by the watchdog (#340/#341) — plain-Python thresholds on "
        f"the public dashboard payloads, no model, no box access. Auto-closes after "
        f"{th.close_after} consecutive clean runs."
    )


def find_issue(slug: str) -> tuple[int, str] | None:
    """Existing alert issue for this slug as (number, state); an open one wins over closed."""
    raw = _gh(
        [
            "issue",
            "list",
            "--state",
            "all",
            "--label",
            "alert",
            "--search",
            f'"{slug}" in:title',
            "--json",
            "number,state,title",
            "--limit",
            "20",
        ]
    )
    items: list[dict[str, Any]] = json.loads(raw or "[]")
    matches = [i for i in items if slug in str(i.get("title", ""))]
    for i in matches:
        if i.get("state") == "OPEN":
            return int(i["number"]), "OPEN"
    if matches:
        return int(matches[0]["number"]), str(matches[0].get("state", ""))
    return None


def open_alert(c: Check, th: Thresholds) -> int:
    """Open (or adopt / reopen) the alert issue for a breaching check; returns its number."""
    existing = find_issue(c.slug)
    if existing and existing[1] == "OPEN":
        return existing[0]
    if existing:  # closed before — REOPEN instead of filing a duplicate (anti-flap)
        num = existing[0]
        _gh(["issue", "reopen", str(num)])
        _gh(["issue", "comment", str(num), "--body", f"Re-breached.\n\n{_issue_body(c, th)}"])
        return num
    url = _gh(
        [
            "issue",
            "create",
            "--title",
            _issue_title(c),
            "--body",
            _issue_body(c, th),
            *[arg for label in _labels_for(c.slug) for arg in ("--label", label)],
        ]
    )
    return int(url.rstrip("/").rsplit("/", 1)[-1])


def close_alert(number: int, c: Check, th: Thresholds) -> None:
    _gh(
        [
            "issue",
            "close",
            str(number),
            "--comment",
            f"Auto-closed by the infra watchdog: `{c.slug}` has been clean for "
            f"{th.close_after} consecutive runs.",
        ]
    )


# --- fetch / state / entry point ----------------------------------------------------------------


def fetch_json(url: str, timeout: float = 20.0) -> dict[str, Any] | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            raw = json.loads(resp.read().decode())
    except Exception:  # any fetch/parse failure reads as "nothing fetchable" — evaluate() decides
        return None
    return raw if isinstance(raw, dict) else None


def load_state(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def keys_from_state(state: dict[str, Any]) -> dict[str, KeyState]:
    out: dict[str, KeyState] = {}
    for slug, v in (state.get("keys") or {}).items():
        if isinstance(v, dict):
            issue = v.get("issue")
            out[str(slug)] = KeyState(
                int(v.get("breach_streak", 0)),
                int(v.get("ok_streak", 0)),
                issue if isinstance(issue, int) else None,
            )
    return out


def save_state(
    path: Path,
    keys: dict[str, KeyState],
    sample: dict[str, Any],
    strategy: dict[str, Any],
    now: datetime,
) -> None:
    payload = {
        "updated_utc": now.isoformat(),
        "sample": sample,
        "strategy": strategy,
        "keys": {
            slug: {"breach_streak": k.breach_streak, "ok_streak": k.ok_streak, "issue": k.issue}
            for slug, k in keys.items()
        },
    }
    path.write_text(json.dumps(payload, indent=2) + "\n")


def in_monitor_window(now_utc: datetime) -> bool:
    local = now_utc.astimezone(ET)
    return is_trading_day(local.date()) and MONITOR_START <= local.time() <= MONITOR_END


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Infra watchdog (#340) — see module docstring.")
    parser.add_argument("--state", type=Path, required=True, help="state JSON (read + rewritten)")
    parser.add_argument("--base-url", default=RAW_BASE)
    parser.add_argument("--force", action="store_true", help="run even outside the window")
    parser.add_argument("--dry-run", action="store_true", help="print actions; don't call gh")
    args = parser.parse_args(argv)

    now = datetime.now(UTC)
    if not args.force and not in_monitor_window(now):
        print("outside the monitor window (trading days 04:00-17:00 ET) — skipping")
        return 0

    status = fetch_json(f"{args.base_url}/status.json")
    published = fetch_json(f"{args.base_url}/published.json")
    stats = fetch_json(f"{args.base_url}/stats.json")
    state = load_state(args.state)
    th = Thresholds()
    ev = evaluate(status, published, state.get("sample") or {}, now, th)
    strat_checks, strat_state = evaluate_strategy(
        status, stats, published, state.get("strategy") or {}, now, th
    )
    all_checks = ev.checks + strat_checks
    keys, actions = step(keys_from_state(state), all_checks, th)

    for act in actions:
        if args.dry_run:
            print(f"[dry-run] {act.kind}: {act.check.slug}")
            continue
        if act.kind == "open":
            num = open_alert(act.check, th)
            keys[act.check.slug].issue = num
            print(f"opened #{num}: {act.check.slug}")
        elif act.issue is not None:
            close_alert(act.issue, act.check, th)
            print(f"closed #{act.issue}: {act.check.slug}")
        else:  # lost-state one-shot: close an open alert this state file didn't know about
            found = find_issue(act.check.slug)
            if found and found[1] == "OPEN":
                close_alert(found[0], act.check, th)
                print(f"closed orphan #{found[0]}: {act.check.slug}")

    breaching = [c.slug for c in all_checks if c.breached]
    print(f"checks: {len(all_checks)} — breaching: {', '.join(breaching) if breaching else 'none'}")
    save_state(args.state, keys, ev.sample, strat_state, now)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
