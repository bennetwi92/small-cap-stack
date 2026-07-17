"""Infra watchdog (#340): the alerting logic is the product here — test it exhaustively.

Everything below drives the pure core (evaluate / step / state round-trip) plus the thin gh and
CLI edges with fakes. No network, no real gh, no box.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from small_cap_stack import watchdog as wd
from small_cap_stack.watchdog import (
    Check,
    KeyState,
    Thresholds,
    evaluate,
    in_monitor_window,
    keys_from_state,
    load_state,
    save_state,
    step,
)

# Fri 2026-07-17 10:00 ET — a trading day, inside the monitor window.
NOW = datetime(2026, 7, 17, 14, 0, tzinfo=UTC)
TH = Thresholds()


def iso(minutes_ago: float) -> str:
    return (NOW - timedelta(minutes=minutes_ago)).isoformat()


def payload(
    gen_min: float = 5,
    over_budget: int = 0,
    missed: int = 0,
    files: int = 500,
    mem_ok: bool | None = True,
    disk_ok: bool | None = True,
) -> dict[str, Any]:
    return {
        "generated_utc": iso(gen_min),
        "health": {
            "tick": "ok",
            "ticks_over_budget_total": over_budget,
            "jobs_missed_total": missed,
            "mem_ok": mem_ok,
            "disk_ok": disk_ok,
        },
        "data": {"bars": {"files": files}, "news": {"files": 300}},
    }


def published(minutes_ago: float = 5) -> dict[str, Any]:
    return {"published_utc": iso(minutes_ago)}


def by_slug(checks: list[Check]) -> dict[str, Check]:
    return {c.slug: c for c in checks}


ALL_SLUGS = {
    "infra/publish-stale",
    "infra/box-stale",
    "infra/tick-over-budget",
    "infra/jobs-missed",
    "infra/dataset-files",
    "infra/mem",
    "infra/disk",
}


# --- evaluate -----------------------------------------------------------------------------------


def test_all_healthy_is_all_clean() -> None:
    ev = evaluate(payload(), published(), {}, NOW, TH)
    assert {c.slug for c in ev.checks} == ALL_SLUGS
    assert all(c.breached is False for c in ev.checks)


def test_stale_publish_breaches_and_suspends_box_checks() -> None:
    # 'Box down' vs 'publish pipeline down' must never be conflated: an old payload says nothing
    # about the box, so every box-derived check goes indeterminate rather than clean or breaching.
    ev = evaluate(payload(), published(minutes_ago=60), {}, NOW, TH)
    checks = by_slug(ev.checks)
    assert checks["infra/publish-stale"].breached is True
    for slug in ALL_SLUGS - {"infra/publish-stale"}:
        assert checks[slug].breached is None, slug


def test_missing_publish_reads_as_stale() -> None:
    ev = evaluate(payload(), None, {}, NOW, TH)
    checks = by_slug(ev.checks)
    assert checks["infra/publish-stale"].breached is True
    assert "unreadable" in checks["infra/publish-stale"].detail


def test_stale_box_with_fresh_publish_is_box_down() -> None:
    ev = evaluate(payload(gen_min=60), published(), {}, NOW, TH)
    checks = by_slug(ev.checks)
    assert checks["infra/publish-stale"].breached is False
    assert checks["infra/box-stale"].breached is True


def test_no_status_payload_is_box_down() -> None:
    ev = evaluate(None, published(), {}, NOW, TH)
    assert by_slug(ev.checks)["infra/box-stale"].breached is True


def test_unchanged_snapshot_freezes_box_checks_and_keeps_sample() -> None:
    # The monitor runs more often than the publish cadence: re-reading the same snapshot carries
    # no new information, so counter/headroom checks freeze instead of counting as clean runs.
    prev = {"generated_utc": iso(5), "counters": {"ticks_over_budget_total": 3}}
    ev = evaluate(payload(gen_min=5, over_budget=3), published(), prev, NOW, TH)
    checks = by_slug(ev.checks)
    for slug in ("infra/tick-over-budget", "infra/jobs-missed", "infra/mem", "infra/disk"):
        assert checks[slug].breached is None, slug
    assert checks["infra/box-stale"].breached is False  # staleness still judged by age, not repeat
    assert ev.sample == prev


def test_counter_increase_between_samples_breaches() -> None:
    prev = {"generated_utc": iso(15), "counters": {"ticks_over_budget_total": 1}}
    ev = evaluate(payload(gen_min=5, over_budget=4), published(), prev, NOW, TH)
    c = by_slug(ev.checks)["infra/tick-over-budget"]
    assert c.breached is True
    assert "1 → 4" in c.detail


def test_counter_reset_means_restart_not_breach() -> None:
    prev = {"generated_utc": iso(15), "counters": {"ticks_over_budget_total": 9}}
    ev = evaluate(payload(gen_min=5, over_budget=1), published(), prev, NOW, TH)
    assert by_slug(ev.checks)["infra/tick-over-budget"].breached is False


def test_first_sample_is_baseline_not_breach() -> None:
    ev = evaluate(payload(over_budget=7, missed=2), published(), {}, NOW, TH)
    checks = by_slug(ev.checks)
    assert checks["infra/tick-over-budget"].breached is False
    assert checks["infra/jobs-missed"].breached is False


def test_jobs_missed_increase_breaches() -> None:
    prev = {"generated_utc": iso(15), "counters": {"jobs_missed_total": 0}}
    ev = evaluate(payload(gen_min=5, missed=1), published(), prev, NOW, TH)
    assert by_slug(ev.checks)["infra/jobs-missed"].breached is True


def test_file_count_explosion_breaches_and_names_dataset() -> None:
    ev = evaluate(payload(files=32000), published(), {}, NOW, TH)
    c = by_slug(ev.checks)["infra/dataset-files"]
    assert c.breached is True
    assert "`bars`" in c.detail and "32000" in c.detail


def test_headroom_booleans_breach_only_on_false() -> None:
    ev = evaluate(payload(mem_ok=False, disk_ok=None), published(), {}, NOW, TH)
    checks = by_slug(ev.checks)
    assert checks["infra/mem"].breached is True
    assert checks["infra/disk"].breached is None  # unreported (e.g. dev box) is not a breach


def test_fresh_snapshot_advances_the_sample() -> None:
    prev = {"generated_utc": iso(20), "counters": {"ticks_over_budget_total": 0}}
    ev = evaluate(payload(gen_min=5, over_budget=2, missed=1), published(), prev, NOW, TH)
    assert ev.sample == {
        "generated_utc": iso(5),
        "counters": {"ticks_over_budget_total": 2, "jobs_missed_total": 1},
    }


# --- step (hysteresis) --------------------------------------------------------------------------


def breach(slug: str = "infra/mem") -> Check:
    return Check(slug, True, "t", "d")


def clean(slug: str = "infra/mem") -> Check:
    return Check(slug, False, "t", "d")


def frozen(slug: str = "infra/mem") -> Check:
    return Check(slug, None, "t", "d")


def test_opens_only_after_m_consecutive_breaches() -> None:
    keys, actions = step({}, [breach()], TH)
    assert actions == []
    keys, actions = step(keys, [breach()], TH)
    assert [a.kind for a in actions] == ["open"]


def test_flapping_never_opens() -> None:
    keys: dict[str, KeyState] = {}
    for check in (breach(), clean(), breach(), clean()):
        keys, actions = step(keys, [check], TH)
        assert actions == []


def test_indeterminate_freezes_streaks() -> None:
    keys, _ = step({}, [breach()], TH)
    keys, actions = step(keys, [frozen()], TH)
    assert actions == []
    assert keys["infra/mem"].breach_streak == 1  # neither reset nor advanced
    keys, actions = step(keys, [breach()], TH)
    assert [a.kind for a in actions] == ["open"]


def test_open_reemitted_until_issue_number_recorded() -> None:
    # open_alert() is idempotent (adopts an existing open issue), so re-emitting is the
    # self-healing path for a failed create or a lost state file.
    keys, _ = step({}, [breach()], TH)
    keys, a1 = step(keys, [breach()], TH)
    keys, a2 = step(keys, [breach()], TH)
    assert [a.kind for a in a1] == ["open"] and [a.kind for a in a2] == ["open"]
    keys["infra/mem"].issue = 99
    keys, a3 = step(keys, [breach()], TH)
    assert a3 == []


def test_closes_after_k_consecutive_clean_runs() -> None:
    keys = {"infra/mem": KeyState(breach_streak=5, issue=42)}
    for _ in range(TH.close_after - 1):
        keys, actions = step(keys, [clean()], TH)
        assert actions == []
    keys, actions = step(keys, [clean()], TH)
    assert [(a.kind, a.issue) for a in actions] == [("close", 42)]
    assert keys["infra/mem"].issue is None


def test_orphan_close_is_one_shot() -> None:
    # Lost state + an open GitHub issue: exactly one lookup-close attempt when the streak first
    # reaches K, not one per run forever.
    keys: dict[str, KeyState] = {}
    emitted = []
    for _ in range(TH.close_after + 2):
        keys, actions = step(keys, [clean()], TH)
        emitted.extend(actions)
    assert [(a.kind, a.issue) for a in emitted] == [("close", None)]


# --- monitor window -----------------------------------------------------------------------------


def test_monitor_window_trading_day() -> None:
    assert in_monitor_window(NOW) is True  # Fri 10:00 ET
    assert in_monitor_window(datetime(2026, 7, 18, 14, 0, tzinfo=UTC)) is False  # Saturday
    assert in_monitor_window(datetime(2026, 7, 17, 7, 0, tzinfo=UTC)) is False  # 03:00 ET
    assert in_monitor_window(datetime(2026, 7, 17, 21, 30, tzinfo=UTC)) is False  # 17:30 ET


def test_monitor_window_half_day_not_clipped() -> None:
    # 2026-11-27 closes at 13:00 ET, but the box's EOD jobs still run at 16:20/16:30 ET
    # (decisions.md), so the infra window stays full-length on early-close days.
    assert in_monitor_window(datetime(2026, 11, 27, 21, 0, tzinfo=UTC)) is True  # 16:00 ET


# --- state round-trip ---------------------------------------------------------------------------


def test_state_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    keys = {"infra/mem": KeyState(breach_streak=2, ok_streak=0, issue=17)}
    sample = {"generated_utc": iso(5), "counters": {"jobs_missed_total": 1}}
    save_state(path, keys, sample, NOW)
    state = load_state(path)
    assert state["sample"] == sample
    assert keys_from_state(state) == keys


def test_state_tolerates_missing_or_garbage(tmp_path: Path) -> None:
    assert load_state(tmp_path / "absent.json") == {}
    bad = tmp_path / "bad.json"
    bad.write_text("not json")
    assert load_state(bad) == {}
    assert keys_from_state({"keys": {"x": "not-a-dict"}}) == {}


# --- gh edge (faked) ----------------------------------------------------------------------------


def test_open_alert_adopts_existing_open_issue(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def fake_gh(args: list[str]) -> str:
        calls.append(args)
        return json.dumps([{"number": 7, "state": "OPEN", "title": "[watchdog] infra/mem — t"}])

    monkeypatch.setattr(wd, "_gh", fake_gh)
    assert wd.open_alert(breach(), TH) == 7
    assert len(calls) == 1  # the list lookup only — no create, no comment spam


def test_open_alert_reopens_recently_closed_issue(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def fake_gh(args: list[str]) -> str:
        calls.append(args)
        if args[:2] == ["issue", "list"]:
            return json.dumps(
                [{"number": 9, "state": "CLOSED", "title": "[watchdog] infra/mem — t"}]
            )
        return ""

    monkeypatch.setattr(wd, "_gh", fake_gh)
    assert wd.open_alert(breach(), TH) == 9
    assert ["issue", "reopen", "9"] in calls
    assert any(c[:2] == ["issue", "comment"] for c in calls)


def test_open_alert_creates_with_deterministic_title_and_labels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def fake_gh(args: list[str]) -> str:
        calls.append(args)
        if args[:2] == ["issue", "list"]:
            return "[]"
        return "https://github.com/bennetwi92/small-cap-stack/issues/123"

    monkeypatch.setattr(wd, "_gh", fake_gh)
    assert wd.open_alert(breach(), TH) == 123
    create = next(c for c in calls if c[:2] == ["issue", "create"])
    title = create[create.index("--title") + 1]
    assert title == "[watchdog] infra/mem — t"  # Python-generated: the dedup key, never a model
    assert create.count("--label") == 2


def test_find_issue_prefers_open_over_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_gh(args: list[str]) -> str:
        return json.dumps(
            [
                {"number": 3, "state": "CLOSED", "title": "[watchdog] infra/mem — t"},
                {"number": 4, "state": "OPEN", "title": "[watchdog] infra/mem — t"},
                {"number": 5, "state": "OPEN", "title": "[watchdog] infra/disk — t"},
            ]
        )

    monkeypatch.setattr(wd, "_gh", fake_gh)
    assert wd.find_issue("infra/mem") == (4, "OPEN")


# --- CLI ----------------------------------------------------------------------------------------


def test_main_dry_run_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # main() clocks off the real now, so these payloads must be stamped relative to it too.
    real_now = datetime.now(UTC)
    fresh = payload()
    fresh["generated_utc"] = real_now.isoformat()
    responses = {
        "status.json": fresh,
        "published.json": {"published_utc": real_now.isoformat()},
    }
    monkeypatch.setattr(
        wd, "fetch_json", lambda url, timeout=20.0: responses[url.rsplit("/", 1)[-1]]
    )
    state = tmp_path / "state.json"
    assert wd.main(["--state", str(state), "--force", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "breaching: none" in out
    saved = json.loads(state.read_text())
    assert set(saved["keys"]) == ALL_SLUGS


def test_main_skips_quietly_outside_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(wd, "in_monitor_window", lambda now: False)
    state = tmp_path / "state.json"
    assert wd.main(["--state", str(state)]) == 0
    assert "skipping" in capsys.readouterr().out
    assert not state.exists()  # no run, no state churn


def test_main_opens_and_records_issue_number(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Two forced runs with a dead box (fresh publish, stale generated_utc): run 1 arms the
    # streak, run 2 opens the alert and records its number in the state file.
    real_now = datetime.now(UTC)
    stale_box = payload()
    stale_box["generated_utc"] = (real_now - timedelta(minutes=90)).isoformat()
    responses = {
        "status.json": stale_box,
        "published.json": {"published_utc": real_now.isoformat()},
    }
    monkeypatch.setattr(
        wd, "fetch_json", lambda url, timeout=20.0: responses[url.rsplit("/", 1)[-1]]
    )
    monkeypatch.setattr(wd, "open_alert", lambda check, th: 55)
    state = tmp_path / "state.json"
    assert wd.main(["--state", str(state), "--force"]) == 0
    assert wd.main(["--state", str(state), "--force"]) == 0
    saved = json.loads(state.read_text())
    assert saved["keys"]["infra/box-stale"]["issue"] == 55
