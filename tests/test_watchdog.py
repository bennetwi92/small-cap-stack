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
    evaluate_canary,
    evaluate_strategy,
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

STRATEGY_SLUGS = {
    "strategy/scanner-silent",
    "strategy/eod-missing",
    "strategy/opps-high",
    "strategy/opps-low",
}

CANARY_SLUGS = {
    "strategy/canary-stale",
    "strategy/canary-float",
    "strategy/canary-news",
    "strategy/canary-bars",
}


# --- evaluate -----------------------------------------------------------------------------------


def test_all_healthy_is_all_clean() -> None:
    ev = evaluate(payload(), published(), {}, NOW, TH)
    assert {c.slug for c in ev.checks} == ALL_SLUGS
    assert all(c.breached is False for c in ev.checks)


def test_stale_publish_breaches_and_suspends_box_checks() -> None:
    # 'Box down' vs 'publish pipeline down' must never be conflated: an old payload says nothing
    # about the box, so every box-derived check goes indeterminate rather than clean or breaching.
    ev = evaluate(payload(), published(minutes_ago=200), {}, NOW, TH)
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
    assert "already 55 min old" in checks["infra/box-stale"].detail  # 60 behind now, publish 5


def test_late_publish_is_never_blamed_on_the_box() -> None:
    # Observed live 2026-07-17: publish 35 min late (under its own threshold), box healthy at
    # copy time. `now - generated_utc` would read 36 min and false-open "box down" — staleness
    # must be judged at COPY time, where the gap was 25 seconds.
    ev = evaluate(payload(gen_min=36), published(minutes_ago=35), {}, NOW, TH)
    checks = by_slug(ev.checks)
    assert checks["infra/publish-stale"].breached is False
    assert checks["infra/box-stale"].breached is False


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


# --- evaluate_strategy (#341) -------------------------------------------------------------------

# NOW is Fri 2026-07-17 10:00 ET: a trading day, inside the 04:30-11:59 scan sub-window.
# AFTERNOON is the same day at 13:30 ET: scan window closed, still inside the monitor window.
AFTERNOON = datetime(2026, 7, 17, 17, 30, tzinfo=UTC)
PREV_SESSION = "2026-07-16"  # Thursday — the most recent completed session before NOW


def strat_payload(
    now: datetime = NOW, scan_min: float | None = 5, opps: int | None = 3
) -> dict[str, Any]:
    p = payload()
    p["generated_utc"] = (now - timedelta(minutes=5)).isoformat()
    if scan_min is not None:
        p["scanner"] = {"last_scan_utc": (now - timedelta(minutes=scan_min)).isoformat()}
    if opps is not None:
        p["opportunities"] = {"open_today": opps, "symbols": []}
    return p


def history(days: int = 15, count: int = 5) -> dict[str, Any]:
    """A warmed opp-count distribution: `days` past dates each recording `count`."""
    return {"opp_counts": {f"2026-06-{d:02d}": count for d in range(1, days + 1)}}


def strat_by_slug(now: datetime = NOW, **kw: Any) -> dict[str, Check]:
    status = kw.pop("status", None)
    if status is None:
        status = strat_payload(now=now)
    stats = kw.pop("stats", {"trading_date": PREV_SESSION})
    pub = kw.pop("published", None)
    if pub is None:
        pub = {"published_utc": (now - timedelta(minutes=5)).isoformat()}
    state = kw.pop("state", history())
    checks, _ = evaluate_strategy(status, stats, pub, state, now, TH)
    return {c.slug: c for c in checks}


def test_strategy_all_healthy() -> None:
    checks = strat_by_slug()
    assert set(checks) == STRATEGY_SLUGS
    assert checks["strategy/scanner-silent"].breached is False
    assert checks["strategy/eod-missing"].breached is False
    assert checks["strategy/opps-high"].breached is False
    assert checks["strategy/opps-low"].breached is None  # scan window still open at 10:00 ET


def test_strategy_indeterminate_when_infra_stale() -> None:
    # One problem, one alert: a stale payload is the infra checks' failure to own.
    checks = strat_by_slug(published=published(minutes_ago=200))
    assert all(checks[slug].breached is None for slug in STRATEGY_SLUGS)


def test_scanner_silent_breaches_and_marks_the_day() -> None:
    status = strat_payload(scan_min=45)
    checks, state = evaluate_strategy(
        status, {"trading_date": PREV_SESSION}, published(), history(), NOW, TH
    )
    by = {c.slug: c for c in checks}
    assert by["strategy/scanner-silent"].breached is True
    # 45 min behind now, publish 5 min old: 40 min stale at copy time — publish lag never counts.
    assert "already 40 min old at the last publish" in by["strategy/scanner-silent"].detail
    assert state["scanner_silent_date"] == "2026-07-17"


def test_scanner_silent_when_no_hits_stored_today() -> None:
    checks = strat_by_slug(status=strat_payload(scan_min=None))
    assert checks["strategy/scanner-silent"].breached is True
    assert "no scanner hit stored yet today" in checks["strategy/scanner-silent"].detail


def test_scanner_check_sleeps_outside_the_subwindow() -> None:
    # 13:30 ET: the scan window is closed — silence is expected, not a breach.
    checks = strat_by_slug(now=AFTERNOON, status=strat_payload(now=AFTERNOON, scan_min=200))
    assert checks["strategy/scanner-silent"].breached is None


def test_eod_missing_breaches_on_old_or_absent_stats() -> None:
    assert (
        strat_by_slug(stats={"trading_date": "2026-07-14"})["strategy/eod-missing"].breached is True
    )
    assert strat_by_slug(stats=None)["strategy/eod-missing"].breached is True
    assert (
        strat_by_slug(stats={"trading_date": PREV_SESSION})["strategy/eod-missing"].breached
        is False
    )


def test_opps_high_breaches_above_band() -> None:
    # Median 5 -> band is max(10, 3x5) = 15; 40 today is anomalous.
    c = strat_by_slug(status=strat_payload(opps=40))["strategy/opps-high"]
    assert c.breached is True
    assert "40 opportunities" in c.detail and "> 15" in c.detail


def test_opps_checks_hold_fire_during_warmup() -> None:
    checks = strat_by_slug(
        now=AFTERNOON, state=history(days=5), status=strat_payload(now=AFTERNOON, opps=40)
    )
    assert checks["strategy/opps-high"].breached is None
    assert checks["strategy/opps-low"].breached is None


def test_opps_low_fires_only_after_window_with_live_scanner_and_big_median() -> None:
    zero_pm = strat_payload(now=AFTERNOON, opps=0)
    # After the window, median 10, scanner alive all morning: 0 is unusual enough to flag.
    assert (
        strat_by_slug(now=AFTERNOON, state=history(count=10), status=zero_pm)[
            "strategy/opps-low"
        ].breached
        is True
    )
    # Same picture during the morning: window still open, no verdict yet.
    assert (
        strat_by_slug(state=history(count=10), status=strat_payload(opps=0))[
            "strategy/opps-low"
        ].breached
        is None
    )
    # Modest median: 0 opportunities is a perfectly healthy day — never floored > 0.
    assert (
        strat_by_slug(now=AFTERNOON, state=history(count=5), status=zero_pm)[
            "strategy/opps-low"
        ].breached
        is None
    )


def test_opps_low_suppressed_after_a_scanner_outage() -> None:
    state = history(count=10)
    state["scanner_silent_date"] = "2026-07-17"
    checks = strat_by_slug(now=AFTERNOON, state=state, status=strat_payload(now=AFTERNOON, opps=0))
    assert checks["strategy/opps-low"].breached is None


def test_opp_counts_accumulate_daily_max() -> None:
    state = history()
    state["opp_counts"]["2026-07-17"] = 2
    _, out = evaluate_strategy(
        strat_payload(opps=5), {"trading_date": PREV_SESSION}, published(), state, NOW, TH
    )
    assert out["opp_counts"]["2026-07-17"] == 5
    _, out2 = evaluate_strategy(
        strat_payload(opps=4), {"trading_date": PREV_SESSION}, published(), out, NOW, TH
    )
    assert out2["opp_counts"]["2026-07-17"] == 5  # a later, smaller reading never shrinks the day


def test_strategy_labels_route_to_strategy() -> None:
    assert wd._labels_for("strategy/opps-high") == ("alert", "strategy")
    assert wd._labels_for("infra/box-stale") == ("alert", "infra")
    assert wd._labels_for("strategy/canary-float") == ("alert", "strategy", "data")


# --- evaluate_canary (#346) ---------------------------------------------------------------------


def canary_payload(
    now: datetime = NOW,
    age_min: float = 5,
    ok_float: bool = True,
    ok_news: bool = True,
    ok_bars: bool | None = True,
) -> dict[str, Any]:
    return {
        "generated_utc": (now - timedelta(minutes=age_min)).isoformat(),
        "trading_date": "2026-07-17",
        "assertions": {
            "float_coverage": {"ok": ok_float, "covered": 27, "total": 28, "pct": 0.964},
            "news_recent": {"ok": ok_news, "rows": 200, "newest_age_h": 0.5},
            "bars_sane": {"ok": ok_bars, "symbols": 28, "offenders": []},
        },
    }


def canary_by_slug(
    canary: dict[str, Any] | None,
    seen: bool = True,
    now: datetime = NOW,
    published_min: float = 5,
) -> tuple[dict[str, Check], bool]:
    pub = {"published_utc": (now - timedelta(minutes=published_min)).isoformat()}
    checks, seen_out = evaluate_canary(canary, strat_payload(now=now), pub, seen, now, TH)
    return {c.slug: c for c in checks}, seen_out


def test_canary_all_healthy() -> None:
    checks, seen = canary_by_slug(canary_payload(), seen=False)
    assert set(checks) == CANARY_SLUGS
    assert all(c.breached is False for c in checks.values())
    assert seen is True  # first sighting arms the presence inversion


def test_canary_absence_is_indeterminate_until_first_sighting() -> None:
    # Until the box deploys the canary writer, its absence proves nothing.
    checks, seen = canary_by_slug(None, seen=False)
    assert all(c.breached is None for c in checks.values())
    assert seen is False


def test_canary_absence_fails_once_seen() -> None:
    # The presence inversion (#346): after first sighting, silence IS a failure.
    checks, _ = canary_by_slug(None, seen=True)
    assert checks["strategy/canary-stale"].breached is True
    assert "missing or malformed" in checks["strategy/canary-stale"].detail
    for slug in CANARY_SLUGS - {"strategy/canary-stale"}:
        assert checks[slug].breached is None, slug


def test_stale_canary_fails_and_suspends_assertions() -> None:
    # Canary 60 min behind now with a 5-min-old publish: 55 min stale at copy time.
    checks, _ = canary_by_slug(canary_payload(age_min=60))
    assert checks["strategy/canary-stale"].breached is True
    assert checks["strategy/canary-float"].breached is None


def test_failed_assertion_breaches_its_check() -> None:
    checks, _ = canary_by_slug(canary_payload(ok_float=False, ok_news=False))
    assert checks["strategy/canary-float"].breached is True
    assert "float_coverage` FAILED" in checks["strategy/canary-float"].detail
    assert "covered=27" in checks["strategy/canary-float"].detail
    assert checks["strategy/canary-news"].breached is True
    assert checks["strategy/canary-bars"].breached is False


def test_pre_eod_bars_verdict_is_indeterminate() -> None:
    checks, _ = canary_by_slug(canary_payload(ok_bars=None))
    assert checks["strategy/canary-bars"].breached is None


def test_canary_checks_suspend_when_infra_is_stale() -> None:
    checks, seen = canary_by_slug(canary_payload(), published_min=200)
    assert all(c.breached is None for c in checks.values())
    assert seen is True  # the copy is old but it exists — the writer is deployed


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
    strategy = {"opp_counts": {"2026-07-16": 4}, "scanner_silent_date": "2026-07-10"}
    save_state(path, keys, sample, strategy, NOW)
    state = load_state(path)
    assert state["sample"] == sample
    assert state["strategy"] == strategy
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
        wd, "fetch_json", lambda url, timeout=20.0: responses.get(url.rsplit("/", 1)[-1])
    )
    state = tmp_path / "state.json"
    assert wd.main(["--state", str(state), "--force", "--dry-run"]) == 0
    out = capsys.readouterr().out
    # Strategy checks may or may not have a verdict depending on when the test runs (the CLI
    # clocks off the real now) — but no infra check can breach, and eod-missing is the only
    # possibly-breaching strategy check here (stats.json deliberately absent).
    assert "infra/" not in out.split("breaching:")[-1]
    saved = json.loads(state.read_text())
    assert set(saved["keys"]) == ALL_SLUGS | STRATEGY_SLUGS | CANARY_SLUGS
    assert saved["strategy"]["canary_seen"] is False  # canary.json not in the fake responses


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
        wd, "fetch_json", lambda url, timeout=20.0: responses.get(url.rsplit("/", 1)[-1])
    )
    monkeypatch.setattr(wd, "open_alert", lambda check, th: 55)
    state = tmp_path / "state.json"
    assert wd.main(["--state", str(state), "--force"]) == 0
    assert wd.main(["--state", str(state), "--force"]) == 0
    saved = json.loads(state.read_text())
    assert saved["keys"]["infra/box-stale"]["issue"] == 55
