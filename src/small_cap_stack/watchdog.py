"""Infra watchdog (#340): plain-Python threshold checks over the public dashboard payload.

Runs on a GitHub-HOSTED runner on a schedule (``.github/workflows/infra-watchdog.yml``) — never
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

State (per-key streaks + the last counter sample) lives in ``infra_state.json`` on the
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
from datetime import UTC, datetime, time
from pathlib import Path
from typing import Any

from .clock import ET
from .market_calendar import is_trading_day

RAW_BASE = "https://raw.githubusercontent.com/bennetwi92/small-cap-stack/dashboard-data"
TITLE_PREFIX = "[watchdog] "
ALERT_LABELS = ("alert", "infra")

# Trading days only, but the FULL day incl. the EOD jobs. Early closes are deliberately not
# clipped: the box's EOD crons stay at 16:20/16:30 ET even on a 13:00 close (decisions.md), so
# infra coverage stays full-window on half days too.
MONITOR_START = time(4, 0)
MONITOR_END = time(17, 0)


@dataclass(frozen=True)
class Thresholds:
    publish_stale_min: float = 45.0  # publish cadence is 15 min; 3 misses = pipeline down
    box_stale_min: float = 35.0  # 60s tick + up to ~20 min publish/schedule lag when healthy
    dataset_files_max: int = 2000  # read cost tracks FILE count (#318/#319); compaction is #328
    open_after: int = 2  # consecutive breaching runs before an issue opens (M)
    close_after: int = 3  # consecutive clean runs before it auto-closes (K)


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
    if pub_stale:
        box_stale: bool | None = None  # payload is old news — can't tell anything about the box
    else:
        box_stale = gen_age is None or gen_age > th.box_stale_min
    box_detail = (
        "status.json carries no readable `generated_utc` — the box has never written a status "
        "snapshot, or wrote a malformed one."
        if gen_age is None
        else f"`generated_utc` is {gen_age:.0f} min old while the publish pipeline is fresh"
        + (f" ({pub_age:.0f} min)" if pub_age is not None else "")
        + " — the app/tick loop on the box looks dead."
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


def _issue_title(c: Check) -> str:
    return f"{TITLE_PREFIX}{c.slug} — {c.title}"


def _issue_body(c: Check, th: Thresholds) -> str:
    return (
        f"{c.detail}\n\n---\nOpened by the infra watchdog (#340) — plain-Python thresholds on "
        f"the public dashboard payload, no model, no box access. Auto-closes after "
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
            *[arg for label in ALERT_LABELS for arg in ("--label", label)],
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
    path: Path, keys: dict[str, KeyState], sample: dict[str, Any], now: datetime
) -> None:
    payload = {
        "updated_utc": now.isoformat(),
        "sample": sample,
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
    state = load_state(args.state)
    th = Thresholds()
    ev = evaluate(status, published, state.get("sample") or {}, now, th)
    keys, actions = step(keys_from_state(state), ev.checks, th)

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

    breaching = [c.slug for c in ev.checks if c.breached]
    print(f"checks: {len(ev.checks)} — breaching: {', '.join(breaching) if breaching else 'none'}")
    save_state(args.state, keys, ev.sample, now)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
