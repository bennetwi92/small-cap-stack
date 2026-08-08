"""Tests for the application wiring (scheduler jobs, restart window, services)."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Any

import pytest

from small_cap_stack import app as appmod
from small_cap_stack.app import Application
from small_cap_stack.clock import ET
from small_cap_stack.config import Settings
from small_cap_stack.storage import Store
from tests.support import opportunity_row, settings

_DAY = date(2026, 7, 2)


def _settings(**overrides: object) -> Settings:
    return settings(**overrides)


def _seed_day(store: Store, day: date) -> None:
    """One opportunity for ``day`` — enough for a non-empty EOD report (no bars needed)."""
    ts = datetime(2026, 7, 2, 14, 0, tzinfo=UTC)
    store.append(
        "opportunities",
        [
            opportunity_row(
                f"{day}:AAA",
                "AAA",
                trading_date=day,
                first_seen=ts,
                con_id=1,
                rank=0,
            )
        ],
        partition_date=day,
    )
    store.append(
        "scanner_hits",
        [{"opportunity_id": f"{day}:AAA", "symbol": "AAA", "ts_utc": ts, "rank": 0}],
        partition_date=day,
    )


def test_scheduler_registers_jobs() -> None:
    app = Application(_settings())
    ids = {job.id for job in app.scheduler.get_jobs()}
    assert ids == {
        "tick",
        "scan_start",
        "scan_end",
        "eod_bars",
        "eod_report",
        "eod_backfill",
        # The morning rebuild (#458): without it the overnight harvest sits unpublished until the
        # 16:30 EOD — through the entire trading day it was harvested for.
        "portfolio_refresh",
    }


def test_builds_services() -> None:
    app = Application(_settings())
    assert app.supervisor is not None
    assert app.capture is not None
    assert app.transport is not None


def test_is_expected_restart_window(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(appmod, "now_et", lambda: datetime(2026, 6, 29, 23, 47, tzinfo=ET))
    inside = Application(_settings(gateway_restart=time(23, 45), gateway_restart_window_min=10))
    assert inside._is_expected_restart() is True  # 23:45–23:55 contains 23:47
    outside = Application(_settings(gateway_restart=time(10, 0), gateway_restart_window_min=10))
    assert outside._is_expected_restart() is False


def test_refresh_stats_charts_writes_todays_session_after_bars(tmp_path: Path) -> None:
    app = Application(_settings(data_dir=tmp_path))
    _seed_day(app.store, _DAY)
    # 16:25 ET is past eod_bars_fetch (16:20) — the day's session is complete, so the tick's
    # catch-up refresh advances the dashboard to today even without the 16:30 EOD job firing.
    app._refresh_stats_charts(datetime(2026, 7, 2, 16, 25, tzinfo=ET))

    stats = tmp_path / "dashboard" / "stats.json"
    assert stats.exists()
    assert json.loads(stats.read_text())["trading_date"] == "2026-07-02"
    # The undated charts.json is gone (#519) — nothing ever read it; every reader resolves
    # charts/<date>.json. Asserted absent so a re-add has to argue for itself.
    assert not (tmp_path / "dashboard" / "charts.json").exists()
    # The dated review payload + navigation index (#141).
    assert (tmp_path / "dashboard" / "charts" / "2026-07-02.json").exists()
    index = json.loads((tmp_path / "dashboard" / "index.json").read_text())
    assert [d["date"] for d in index["dates"]] == ["2026-07-02"]


def test_refresh_stats_charts_noop_before_bars(tmp_path: Path) -> None:
    app = Application(_settings(data_dir=tmp_path))
    _seed_day(app.store, _DAY)
    # Mid-session (10:00 ET): the day isn't done, so the previous session must stay put — the tick
    # leaves stats.json untouched (it's reviewable all day, #117).
    app._refresh_stats_charts(datetime(2026, 7, 2, 10, 0, tzinfo=ET))
    assert not (tmp_path / "dashboard" / "stats.json").exists()


def test_refresh_stats_charts_skips_empty_day(tmp_path: Path) -> None:
    app = Application(_settings(data_dir=tmp_path))  # store seeded with nothing (e.g. a weekend)
    app._refresh_stats_charts(datetime(2026, 7, 2, 16, 25, tzinfo=ET))
    # No opportunities -> no write, so a non-trading day never clobbers the last real session.
    assert not (tmp_path / "dashboard" / "stats.json").exists()


def test_refresh_stats_charts_disabled(tmp_path: Path) -> None:
    app = Application(_settings(data_dir=tmp_path, dashboard_enabled=False))
    _seed_day(app.store, _DAY)
    app._refresh_stats_charts(datetime(2026, 7, 2, 16, 25, tzinfo=ET))
    assert not (tmp_path / "dashboard" / "stats.json").exists()


# --- trading-calendar gate (#137) ---------------------------------------------------------------


def test_on_tick_skips_scan_on_non_trading_day(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Saturday 2026-07-04, 10:00 ET — inside the scan window, but not a session. The scan block
    # must not run; the status export still does (the dashboard stays live 24/7).
    monkeypatch.setattr(appmod, "now_et", lambda: datetime(2026, 7, 4, 10, 0, tzinfo=ET))
    app = Application(_settings(data_dir=tmp_path))
    monkeypatch.setattr(app.transport, "is_connected", lambda: True)
    # The scheduler is never started in tests, so its jobs have no next_run_time yet.
    monkeypatch.setattr(app.scheduler, "get_jobs", list)

    async def boom_scan(ib: object) -> list[object]:
        raise AssertionError("scanner must not run on a non-trading day")

    monkeypatch.setattr(app.scanner, "scan", boom_scan)
    asyncio.run(app._on_tick())
    assert (tmp_path / "dashboard" / "status.json").exists()


def test_on_tick_scans_on_a_trading_day(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Thursday 2026-07-02, 10:00 ET — the same setup must reach the scanner.
    monkeypatch.setattr(appmod, "now_et", lambda: datetime(2026, 7, 2, 10, 0, tzinfo=ET))
    app = Application(_settings(data_dir=tmp_path))
    monkeypatch.setattr(app.transport, "is_connected", lambda: True)
    scanned = []

    async def fake_scan(ib: object) -> list[object]:
        scanned.append(True)
        return []

    monkeypatch.setattr(app.scanner, "scan", fake_scan)
    asyncio.run(app._on_tick())
    assert scanned == [True]


def test_eod_jobs_noop_on_non_trading_day(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # The 2026-07-03 incident day (Independence Day observed): both EOD jobs must return early.
    monkeypatch.setattr(appmod, "now_et", lambda: datetime(2026, 7, 3, 16, 25, tzinfo=ET))
    app = Application(_settings(data_dir=tmp_path))

    async def boom_batch(trading_date: date) -> None:
        raise AssertionError("EOD batch must not run on a non-trading day")

    monkeypatch.setattr(app, "_eod_ibkr_batch", boom_batch)

    def boom_report(*a: object, **k: object) -> object:
        raise AssertionError("EOD report must not build on a non-trading day")

    monkeypatch.setattr(appmod, "build_eod_report", boom_report)
    asyncio.run(app._on_eod_bars())
    asyncio.run(app._on_eod_report())


def test_eod_backfill_filters_to_trading_days(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Monday 2026-07-06 03:45 ET with a 3-day lookback: Sun 07-05 and Sat 07-04 drop out, and the
    # job still runs (gating the whole job on a weekend would strand a failed Friday EOD).
    monkeypatch.setattr(appmod, "now_et", lambda: datetime(2026, 7, 6, 3, 45, tzinfo=ET))
    app = Application(_settings(data_dir=tmp_path, backfill_days=3))
    monkeypatch.setattr(app.transport, "is_connected", lambda: True)
    funds: list[date] = []

    async def fake_funds(d: date) -> None:
        funds.append(d)

    monkeypatch.setattr(app, "_backfill_fundamentals", fake_funds)
    seen: list[list[date]] = []

    async def fake_backfill(dates: list[date]) -> list[date]:
        seen.append(list(dates))
        return []

    monkeypatch.setattr(app.capture, "backfill_recent", fake_backfill)
    asyncio.run(app._on_eod_backfill())
    assert funds == [date(2026, 7, 6)]
    assert seen == [[date(2026, 7, 6)]]


# --- tick instrumentation (#321) ----------------------------------------------------------------


def test_status_json_carries_coarse_health_and_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The acceptance test for "reachable without SSH" (#321), scrubbed by #340/#344: the payload
    # is public, so it carries coarse verdicts and counters — never raw seconds or headroom
    # numbers (those stay in Prometheus/SSH).
    monkeypatch.setattr(appmod, "now_et", lambda: datetime(2026, 7, 2, 10, 0, tzinfo=ET))
    app = Application(_settings(data_dir=tmp_path))
    monkeypatch.setattr(app.scheduler, "get_jobs", list)
    _seed_day(app.store, _DAY)
    asyncio.run(app._on_tick())  # disconnected -> no scan, but the status export runs

    s = json.loads((tmp_path / "dashboard" / "status.json").read_text())
    assert "timings" not in s  # the #344 scrub: no timing numbers on the public surface
    h = s["health"]
    assert set(h) == {"tick", "ticks_over_budget_total", "jobs_missed_total", "mem_ok", "disk_ok"}
    assert h["tick"] in {"ok", "slow", "over_budget"}
    assert h["ticks_over_budget_total"] >= 0
    assert h["mem_ok"] in {True, False, None}  # None where /proc/meminfo is absent (macOS dev)
    assert h["disk_ok"] in {True, False}
    # File counts: the number that would have caught #318 (scanner_hits at 32k files).
    assert s["data"]["opportunities"]["files"] == 1
    assert s["data"]["scanner_hits"]["files"] == 1


def test_canary_written_and_throttled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # The canary (#346) rides the tick's status export but on its own throttle: with the clock
    # frozen, a second tick inside the interval must not recompute it.
    calls: list[int] = []

    def fake_canary(*args: object, **kwargs: object) -> dict[str, int]:
        calls.append(1)
        return {"built": len(calls)}

    monkeypatch.setattr(appmod, "build_canary", fake_canary)
    monkeypatch.setattr(appmod, "now_et", lambda: datetime(2026, 7, 2, 10, 0, tzinfo=ET))
    app = Application(_settings(data_dir=tmp_path))
    monkeypatch.setattr(app.scheduler, "get_jobs", list)
    asyncio.run(app._on_tick())
    asyncio.run(app._on_tick())
    assert calls == [1]
    assert json.loads((tmp_path / "dashboard" / "canary.json").read_text()) == {"built": 1}


def test_over_budget_tick_increments_counter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from small_cap_stack.monitoring import metric_value

    monkeypatch.setattr(appmod, "now_et", lambda: datetime(2026, 7, 2, 10, 0, tzinfo=ET))
    app = Application(_settings(data_dir=tmp_path, dashboard_enabled=False))
    # Fake clock: the tick's start/end perf_counter reads land 45s apart (> half the 60s budget).
    ticks = iter([0.0, 45.0, 90.0, 135.0])
    monkeypatch.setattr(appmod.time, "perf_counter", lambda: next(ticks))
    before = metric_value("scs_ticks_over_budget_total")
    asyncio.run(app._on_tick())
    assert metric_value("scs_ticks_over_budget_total") == before + 1
    assert metric_value("scs_tick_seconds") == 45.0


def test_heartbeat_pings_on_completion_not_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The ping moved to the END of the tick (#321): a tick that raises must NOT ping, so a
    # persistently failing (or wedged) tick goes silent and Healthchecks alerts.
    monkeypatch.setattr(appmod, "now_et", lambda: datetime(2026, 7, 2, 10, 0, tzinfo=ET))
    app = Application(_settings(data_dir=tmp_path, dashboard_enabled=False))
    monkeypatch.setattr(app.transport, "is_connected", lambda: True)
    pings: list[bool] = []

    async def fake_ping() -> None:
        pings.append(True)

    monkeypatch.setattr(app.heartbeat, "ping", fake_ping)

    async def boom_scan(ib: object) -> list[object]:
        raise RuntimeError("scanner down")

    monkeypatch.setattr(app.scanner, "scan", boom_scan)
    with pytest.raises(RuntimeError):
        asyncio.run(app._on_tick())
    assert pings == []  # no ping -> the dead-man's switch can actually fire

    async def ok_scan(ib: object) -> list[object]:
        return []

    monkeypatch.setattr(app.scanner, "scan", ok_scan)
    asyncio.run(app._on_tick())
    assert pings == [True]  # a completed tick pings


def test_a_dead_feed_inside_the_session_fails_the_heartbeat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#677. Error 1100 leaves the API socket open, so `is_connected()` goes false and the scan is
    skipped — but the tick itself completes, and pinging a completed tick told Healthchecks all was
    well while the app was seeing no prices at all.

    Survivable in Phase 1 (a gap in the record); not in Phase 2, where an app-side stop cannot fire
    on a feed that is not delivering — and the failure looks exactly like a quiet tape.
    """
    monkeypatch.setattr(appmod, "now_et", lambda: datetime(2026, 7, 2, 10, 0, tzinfo=ET))
    app = Application(_settings(data_dir=tmp_path, dashboard_enabled=False))
    monkeypatch.setattr(app.transport, "is_connected", lambda: False)  # 1100: farm down
    calls: list[str] = []

    async def fake_ping() -> None:
        calls.append("ping")

    async def fake_fail() -> None:
        calls.append("fail")

    monkeypatch.setattr(app.heartbeat, "ping", fake_ping)
    monkeypatch.setattr(app.heartbeat, "fail", fake_fail)
    asyncio.run(app._on_tick())
    assert calls == ["fail"]


def test_a_dead_feed_outside_the_session_still_pings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Scoped deliberately. The Gateway restarts daily at 23:45 ET and 1100s around it are expected;
    failing the switch for those would train the alert to be ignored, which is how a real outage
    gets missed. 20:00 ET on a trading day is outside the scan window."""
    monkeypatch.setattr(appmod, "now_et", lambda: datetime(2026, 7, 2, 20, 0, tzinfo=ET))
    app = Application(_settings(data_dir=tmp_path, dashboard_enabled=False))
    monkeypatch.setattr(app.transport, "is_connected", lambda: False)
    calls: list[str] = []

    async def fake_ping() -> None:
        calls.append("ping")

    async def fake_fail() -> None:
        calls.append("fail")

    monkeypatch.setattr(app.heartbeat, "ping", fake_ping)
    monkeypatch.setattr(app.heartbeat, "fail", fake_fail)
    asyncio.run(app._on_tick())
    assert calls == ["ping"]


def test_a_dead_feed_on_a_non_trading_day_still_pings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Saturday inside the window — there is no session to miss, so a dead feed is not a failure."""
    monkeypatch.setattr(appmod, "now_et", lambda: datetime(2026, 7, 4, 10, 0, tzinfo=ET))
    app = Application(_settings(data_dir=tmp_path, dashboard_enabled=False))
    monkeypatch.setattr(app.transport, "is_connected", lambda: False)
    calls: list[str] = []

    async def fake_ping() -> None:
        calls.append("ping")

    async def fake_fail() -> None:
        calls.append("fail")

    monkeypatch.setattr(app.heartbeat, "ping", fake_ping)
    monkeypatch.setattr(app.heartbeat, "fail", fake_fail)
    asyncio.run(app._on_tick())
    assert calls == ["ping"]


def test_the_morning_refresh_does_not_force_re_extract_today(monkeypatch: Any) -> None:
    """At 03:15 ET "today" has no bars, so the EOD's force-re-extract-today would re-extract an
    empty day. The EOD keeps it (its bars have just landed); the morning rebuild must not.

    What the morning run is actually for — the reconstructed days the harvest just landed — needs
    no naming: they are uncached by definition and picked up either way.
    """
    seen: list[object] = []

    def fake_build(*_a: Any, **kw: Any) -> dict[str, object]:
        seen.append(kw.get("force_dates"))
        return {"ok": True}

    monkeypatch.setattr("small_cap_stack.app.build_portfolio_payload", fake_build)
    monkeypatch.setattr("small_cap_stack.app.write_json", lambda *_a, **_k: None)
    app = Application.__new__(Application)
    app.settings = settings(dashboard_enabled=True)
    app.store = None  # type: ignore[assignment]  # never touched — build is stubbed

    app._export_portfolio(datetime(2026, 8, 6, 7, 15, tzinfo=UTC), force_today=False)
    app._export_portfolio(datetime(2026, 8, 6, 20, 30, tzinfo=UTC), force_today=True)

    assert seen[0] == set(), "the 03:15 rebuild must not force today"
    assert seen[1], "the 16:30 EOD must still force today — its bars have just landed"


# --- EOD failure paths (#531) ----------------------------------------------------------------
# `test_app.py` covered tick gating and status JSON well and never exercised a failure. But the
# orchestrator runs unattended on a box reachable only by SSH: the paths that matter operationally
# are the ones that run when something is already wrong, and every one of them was untested.


class _FlakyCapture:
    """A `capture` whose `capture_day_bars` fails the first `fail_times` calls.

    Counts both entry points, because the retry ladder must re-run the *whole* Gateway-dependent
    half — bars AND the news re-fetch — not resume mid-way. A retry that skipped the news fetch
    would leave the day permanently short of headlines with nothing to signal it.
    """

    def __init__(self, fail_times: int) -> None:
        self.fail_times = fail_times
        self.bars_calls = 0
        self.news_calls = 0
        self.fundamentals_calls = 0

    async def capture_day_bars(self, trading_date: date) -> None:
        self.bars_calls += 1
        if self.bars_calls <= self.fail_times:
            raise RuntimeError("ibkr timed out")

    async def capture_day_news(self, trading_date: date) -> None:
        self.news_calls += 1

    async def capture_missing_fundamentals(self, trading_date: date) -> int:
        self.fundamentals_calls += 1
        return 0


def _eod_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capture: object, *, connected: bool = True
) -> tuple[Application, list[float]]:
    """An Application wired to `capture`, with `asyncio.sleep` recorded rather than slept.

    The real ladder waits 60s between attempts; sleeping that in a test would make the suite
    unusable, and mocking it away silently would hide a ladder that never waits at all. Recording
    it means the *sleep budget* is assertable — which is the thing that decides whether a retry
    still lands before the 16:30 report job.
    """
    app = Application(_settings(data_dir=tmp_path))
    monkeypatch.setattr(app, "capture", capture)
    monkeypatch.setattr(app.transport, "is_connected", lambda: connected)
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(appmod.asyncio, "sleep", fake_sleep)
    return app, slept


def test_eod_batch_retries_and_succeeds_on_a_later_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A transient Gateway failure must not cost the day's bars. Two failures then a success: three
    attempts, two waits, and the news fetch runs once — on the attempt that got through."""
    capture = _FlakyCapture(fail_times=2)
    app, slept = _eod_app(tmp_path, monkeypatch, capture)

    asyncio.run(app._eod_ibkr_batch(_DAY))

    assert capture.bars_calls == 3
    assert capture.news_calls == 1  # only the successful attempt reaches the news re-fetch
    assert slept == [app.settings.eod_retry_delay_sec] * 2  # waited between attempts, not after


def test_eod_batch_gives_up_after_the_configured_attempts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The path the morning back-fill (#100) exists to recover. It must stop at the configured
    count rather than looping, and must NOT sleep after the final failure — a trailing wait is 60
    seconds of a job holding the box for nothing."""
    capture = _FlakyCapture(fail_times=99)
    app, slept = _eod_app(tmp_path, monkeypatch, capture)

    asyncio.run(app._eod_ibkr_batch(_DAY))  # gives up, does not raise

    attempts = app.settings.eod_retry_attempts
    assert capture.bars_calls == attempts
    assert capture.news_calls == 0
    assert len(slept) == attempts - 1  # no sleep after the last attempt


def test_eod_batch_treats_a_disconnected_gateway_as_a_retryable_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The connection check is inside the loop on purpose: at 16:20 the Gateway may still be
    coming back from its nightly restart, so a disconnect has to be retried rather than skipped.
    Capture must never be called against a connection we know is down."""
    capture = _FlakyCapture(fail_times=0)
    app, slept = _eod_app(tmp_path, monkeypatch, capture, connected=False)

    asyncio.run(app._eod_ibkr_batch(_DAY))

    assert capture.bars_calls == 0  # never attempted against a known-down Gateway
    assert len(slept) == app.settings.eod_retry_attempts - 1


def test_eod_batch_respects_a_single_attempt_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`eod_retry_attempts = 1` means try once, and — the part worth pinning — sleep zero times.
    An off-by-one in the ladder's `attempt < attempts` guard shows up here and nowhere else."""
    capture = _FlakyCapture(fail_times=99)
    app = Application(_settings(data_dir=tmp_path, eod_retry_attempts=1))
    monkeypatch.setattr(app, "capture", capture)
    monkeypatch.setattr(app.transport, "is_connected", lambda: True)
    slept: list[float] = []
    monkeypatch.setattr(appmod.asyncio, "sleep", lambda s: slept.append(s) or asyncio.sleep(0))

    asyncio.run(app._eod_ibkr_batch(_DAY))
    assert capture.bars_calls == 1 and slept == []


def test_fundamentals_backfill_failure_never_takes_down_the_eod_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fundamentals come from yfinance/FMP over plain HTTP and are deliberately outside the IBKR
    retry. A failure there must be swallowed: it is enrichment, and letting it raise would abort
    an EOD run whose bars had already been captured."""
    app = Application(_settings(data_dir=tmp_path))

    class _Boom:
        async def capture_missing_fundamentals(self, trading_date: date) -> int:
            raise RuntimeError("fmp 503")

    monkeypatch.setattr(app, "capture", _Boom())
    asyncio.run(app._backfill_fundamentals(_DAY))  # must not raise


def test_fundamentals_backfill_reports_what_it_filled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The success side, so the swallow above can't be mistaken for the only behaviour."""
    app = Application(_settings(data_dir=tmp_path))
    capture = _FlakyCapture(fail_times=0)
    monkeypatch.setattr(app, "capture", capture)
    asyncio.run(app._backfill_fundamentals(_DAY))
    assert capture.fundamentals_calls == 1


def test_eod_bars_skips_a_non_trading_day(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The calendar gate short-circuits before the Gateway is touched at all — the EOD cron fires
    every weekday and a holiday must cost zero IBKR calls, not three failed attempts."""
    capture = _FlakyCapture(fail_times=99)
    app = Application(_settings(data_dir=tmp_path))
    monkeypatch.setattr(app, "capture", capture)
    # 2026-07-03: Independence Day observed (Jul 4 is a Saturday) — NYSE closed.
    monkeypatch.setattr(appmod, "now_et", lambda: datetime(2026, 7, 3, 16, 20, tzinfo=ET))

    asyncio.run(app._on_eod_bars())

    assert capture.bars_calls == 0 and capture.fundamentals_calls == 0


def test_shutdown_stops_the_scheduler_before_the_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ordering is the whole point of the `finally` block (its comment says so): new ticks must
    stop launching *before* the connection is torn down, or a tick fires against a half-closed
    Gateway. Asserted as a recorded sequence — two separate "was called" checks would pass in
    either order, which is exactly the bug this guards."""
    app = Application(_settings(data_dir=tmp_path, metrics_enabled=False))
    order: list[str] = []

    monkeypatch.setattr(app, "_install_signal_handlers", lambda: None)
    monkeypatch.setattr(app.scheduler, "start", lambda: order.append("scheduler.start"))
    monkeypatch.setattr(
        app.scheduler, "shutdown", lambda wait=True: order.append("scheduler.shutdown")
    )
    monkeypatch.setattr(app.supervisor, "stop", lambda: order.append("supervisor.stop"))

    async def _supervisor_run() -> None:
        await asyncio.sleep(0)

    monkeypatch.setattr(app.supervisor, "run", _supervisor_run)

    async def _drive() -> None:
        app._shutdown.set()  # already signalled, so run() falls straight through to teardown
        await app.run()

    asyncio.run(_drive())

    assert order.index("scheduler.shutdown") < order.index("supervisor.stop")
    assert order[0] == "scheduler.start"


def test_run_starts_the_metrics_server_only_when_enabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The metrics port is opt-in. Starting a listener the operator disabled is a surprise on a
    box whose firewall rules are hand-maintained."""
    started: list[int] = []
    monkeypatch.setattr(appmod, "start_metrics_server", lambda port: started.append(port))

    async def _drive(app: Application) -> None:
        monkeypatch.setattr(app, "_install_signal_handlers", lambda: None)
        monkeypatch.setattr(app.scheduler, "start", lambda: None)
        monkeypatch.setattr(app.scheduler, "shutdown", lambda wait=True: None)
        monkeypatch.setattr(app.supervisor, "stop", lambda: None)

        async def _run() -> None:
            await asyncio.sleep(0)

        monkeypatch.setattr(app.supervisor, "run", _run)
        app._shutdown.set()
        await app.run()

    asyncio.run(_drive(Application(_settings(data_dir=tmp_path, metrics_enabled=False))))
    assert started == []

    asyncio.run(
        _drive(Application(_settings(data_dir=tmp_path, metrics_enabled=True, metrics_port=9123)))
    )
    assert started == [9123]


# --- the dashboard-write and backfill paths (#531) --------------------------------------------
# These are all "best-effort" by design: a dashboard write must never break the caller, and the
# morning backfill must survive a Gateway that is still down. "Best-effort" is a claim about
# behaviour under failure, and none of it was tested — so the swallow could have been swallowing
# the wrong things, or nothing at all.


def test_portfolio_export_swallows_a_failure_rather_than_breaking_the_caller(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`_export_portfolio` is called from the 16:30 EOD *after* bars and analysis have landed. If a
    payload build could raise through it, a dashboard bug would cost the day's report."""
    app = Application(_settings(data_dir=tmp_path))

    def _boom(*args: object, **kwargs: object) -> dict[str, object]:
        raise RuntimeError("polars blew up")

    monkeypatch.setattr(appmod, "build_portfolio_payload", _boom)
    app._export_portfolio(datetime(2026, 7, 2, 20, 30, tzinfo=UTC))  # must not raise
    assert not (tmp_path / "dashboard" / "portfolio.json").exists()


def test_portfolio_export_is_skipped_entirely_when_the_dashboard_is_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`dashboard_enabled=False` must short-circuit before the payload is built — the build is the
    expensive part (it holds every collected day's bars in memory, #273), so gating it after the
    fact would keep the cost and drop only the file."""
    app = Application(_settings(data_dir=tmp_path, dashboard_enabled=False))
    built: list[int] = []
    monkeypatch.setattr(appmod, "build_portfolio_payload", lambda *a, **k: built.append(1) or {})

    app._export_portfolio(datetime(2026, 7, 2, 20, 30, tzinfo=UTC))
    assert built == []


def test_portfolio_export_writes_the_payload_on_the_happy_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The success side, so the two negatives above can't be mistaken for the only behaviours."""
    app = Application(_settings(data_dir=tmp_path))
    monkeypatch.setattr(appmod, "build_portfolio_payload", lambda *a, **k: {"trades": []})

    app._export_portfolio(datetime(2026, 7, 2, 20, 30, tzinfo=UTC))
    written = tmp_path / "dashboard" / "portfolio.json"
    assert written.exists() and json.loads(written.read_text()) == {"trades": []}


def test_the_0315_refresh_does_not_force_today(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`force_today` is what separates the two daily calls (#458). At 03:15 today has no bars at
    all, so forcing it would re-extract an empty day and discard the cached real one."""
    app = Application(_settings(data_dir=tmp_path))
    seen: list[object] = []

    def _capture_force(*args: object, **kwargs: object) -> dict[str, object]:
        seen.append(kwargs.get("force_dates"))
        return {}

    monkeypatch.setattr(appmod, "build_portfolio_payload", _capture_force)

    asyncio.run(app._on_portfolio_refresh())  # the 03:15 job
    app._export_portfolio(datetime(2026, 7, 2, 20, 30, tzinfo=UTC))  # the 16:30 EOD call

    assert seen[0] == set(), "the overnight refresh must not force today"
    assert seen[1], "the EOD call must force today — its bars have just landed"


def test_eod_report_skips_a_non_trading_day(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Like the bars job: the 16:30 cron fires every weekday, and a holiday must not write a
    report for a session that never happened."""
    app = Application(_settings(data_dir=tmp_path))
    monkeypatch.setattr(appmod, "now_et", lambda: datetime(2026, 7, 3, 16, 30, tzinfo=ET))
    built: list[int] = []
    monkeypatch.setattr(appmod, "build_eod_report", lambda *a, **k: built.append(1))

    asyncio.run(app._on_eod_report())
    assert built == []


def test_backfill_stops_short_when_the_gateway_is_down(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The morning catch-up runs at 03:45, when the Gateway may still be restarting. Fundamentals
    come over plain HTTP and must still run — that is the whole reason they sit outside the IBKR
    retry — but bar backfill must not be attempted against a dead connection."""
    app = Application(_settings(data_dir=tmp_path))
    capture = _FlakyCapture(fail_times=0)
    backfilled: list[object] = []

    async def _backfill_recent(days: object) -> list[date]:
        backfilled.append(days)
        return []

    capture.backfill_recent = _backfill_recent  # type: ignore[attr-defined]
    monkeypatch.setattr(app, "capture", capture)
    monkeypatch.setattr(app.transport, "is_connected", lambda: False)
    monkeypatch.setattr(appmod, "now_et", lambda: datetime(2026, 7, 2, 3, 45, tzinfo=ET))

    asyncio.run(app._on_eod_backfill())

    assert backfilled == []  # no bar backfill against a down Gateway
    assert capture.fundamentals_calls > 0  # ...but fundamentals still ran


def test_eod_report_writes_the_analysis_and_the_markdown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The 16:30 job's actual work, end to end on a seeded day: the analysis rows land in the
    store (they are what the review and results pages read) and the markdown artifact is written.

    Previously only the two *skip* paths were covered, so a report job that silently wrote nothing
    would have looked identical to a holiday.
    """
    app = Application(_settings(data_dir=tmp_path))
    _seed_day(app.store, _DAY)
    monkeypatch.setattr(appmod, "now_et", lambda: datetime(2026, 7, 2, 16, 30, tzinfo=ET))
    monkeypatch.setattr(appmod, "build_portfolio_payload", lambda *a, **k: {})

    asyncio.run(app._on_eod_report())

    assert (tmp_path / "reports" / f"eod_{_DAY.isoformat()}.md").exists()
    assert not app.store.read("analysis", dt=_DAY).is_empty()


def test_backfill_rewrites_the_report_for_each_repaired_day(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The point of the morning catch-up (#100) is not just to fetch the missing bars — it is to
    *refresh the artifact* with them. A backfill that filled bars and left yesterday's report
    describing a day with no data would look like it had worked."""
    app = Application(_settings(data_dir=tmp_path))
    _seed_day(app.store, _DAY)
    capture = _FlakyCapture(fail_times=0)

    async def _backfill_recent(days: object) -> list[date]:
        return [_DAY]  # pretend this day's bars were just repaired

    capture.backfill_recent = _backfill_recent  # type: ignore[attr-defined]
    monkeypatch.setattr(app, "capture", capture)
    monkeypatch.setattr(app.transport, "is_connected", lambda: True)
    monkeypatch.setattr(appmod, "now_et", lambda: datetime(2026, 7, 3, 3, 45, tzinfo=ET))

    asyncio.run(app._on_eod_backfill())

    assert (tmp_path / "reports" / f"eod_{_DAY.isoformat()}.md").exists()
