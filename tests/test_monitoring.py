"""Tests for monitoring (#5, expanded #688): heartbeat + the metric surface."""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path

import pytest
from prometheus_client import REGISTRY

from small_cap_stack.monitoring import (
    JOB_SECONDS,
    SCAN_TICKS,
    STORE_READ_SECONDS,
    Heartbeat,
    dataset_bytes,
    disk_used_pct,
    export_canary_metrics,
    ibkr_request,
    instrument_job,
    mem_available_mb,
    metric_names,
    observe,
    record_dashboard_write,
    set_build_info,
)


def test_heartbeat_ping_and_fail_urls() -> None:
    calls: list[str] = []
    hb = Heartbeat("https://hc-ping.com/abc/", fetch=calls.append)
    asyncio.run(hb.ping())
    asyncio.run(hb.fail())
    assert calls == ["https://hc-ping.com/abc", "https://hc-ping.com/abc/fail"]


def test_heartbeat_noop_without_url() -> None:
    calls: list[str] = []
    hb = Heartbeat("", fetch=calls.append)
    asyncio.run(hb.ping())
    asyncio.run(hb.fail())
    assert calls == []


def test_heartbeat_swallows_errors() -> None:
    def boom(_url: str) -> None:
        raise RuntimeError("network down")

    hb = Heartbeat("https://hc-ping.com/abc", fetch=boom)
    asyncio.run(hb.ping())  # must not raise


def test_metric_increments() -> None:
    before = SCAN_TICKS._value.get()  # type: ignore[attr-defined]
    SCAN_TICKS.inc()
    assert SCAN_TICKS._value.get() == before + 1  # type: ignore[attr-defined]


def test_mem_available_reads_proc_meminfo(tmp_path: Path) -> None:
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal:  4014356 kB\nMemFree:   123456 kB\nMemAvailable: 1024000 kB\n")
    assert mem_available_mb(meminfo) == 1000.0


def test_mem_available_none_where_unreadable(tmp_path: Path) -> None:
    assert mem_available_mb(tmp_path / "absent") is None  # e.g. macOS dev box
    garbled = tmp_path / "garbled"
    garbled.write_text("MemAvailable: lots\n")
    assert mem_available_mb(garbled) is None


def test_disk_used_pct(tmp_path: Path) -> None:
    pct = disk_used_pct(tmp_path)
    assert pct is not None and 0.0 <= pct <= 100.0
    assert disk_used_pct(tmp_path / "absent") is None


# --------------------------------------------------------------- the instrumentation helpers (#688)


def test_metric_names_strips_sample_suffixes() -> None:
    """The registry names metrics; PromQL names samples. Comparing them needs one normal form.

    `Counter("scs_scan_ticks_total")` registers as `scs_scan_ticks` and exposes
    `scs_scan_ticks_total`; a histogram exposes `_bucket`/`_count`/`_sum`. Without this,
    `tests/test_observability_contract.py` would have to maintain a hand-written list of what
    exists — which is the kind of list that is wrong on its first run.
    """
    names = metric_names()
    assert "scs_scan_ticks" in names
    assert "scs_tick_duration_seconds" in names  # the histogram, not scs_tick_duration_seconds_sum
    assert not any(n.endswith(("_bucket", "_sum", "_count", "_created")) for n in names)


def test_observe_times_a_block_even_when_it_raises() -> None:
    """A failed call is precisely the one whose duration is the observation.

    An IBKR request that times out at 30s is the finding; recording only successes would leave the
    histogram describing a system that never has a bad day.
    """
    before = _hist_count(STORE_READ_SECONDS, dataset="probe")
    with contextlib.suppress(RuntimeError), observe(STORE_READ_SECONDS, dataset="probe"):
        raise RuntimeError("boom")
    assert _hist_count(STORE_READ_SECONDS, dataset="probe") == before + 1


def test_ibkr_request_separates_a_timeout_from_any_other_failure() -> None:
    """The distinction is the point: a raised error is IBKR refusing the request, a timeout is the
    Gateway not answering — which is what a pacing violation, a wedged farm and a saturated box all
    look like from here."""

    def outcome_count(kind: str, outcome: str) -> float:
        return (
            REGISTRY.get_sample_value("scs_ibkr_requests_total", {"kind": kind, "outcome": outcome})
            or 0.0
        )

    before = {o: outcome_count("news", o) for o in ("ok", "error", "timeout")}
    with ibkr_request("news"):
        pass
    with contextlib.suppress(TimeoutError), ibkr_request("news"):
        raise TimeoutError
    with contextlib.suppress(ValueError), ibkr_request("news"):
        raise ValueError("refused")

    assert outcome_count("news", "ok") == before["ok"] + 1
    assert outcome_count("news", "timeout") == before["timeout"] + 1
    assert outcome_count("news", "error") == before["error"] + 1


def test_instrument_job_records_success_and_reraises_failure() -> None:
    """It observes; it must not change behaviour. Swallowing here would silently turn a failing
    scheduled job into a passing one, which is the opposite of the point."""

    async def ok() -> None:
        return None

    async def boom() -> None:
        raise ValueError("nope")

    asyncio.run(instrument_job("probe_ok", ok)())
    assert (
        REGISTRY.get_sample_value("scs_job_runs_total", {"job": "probe_ok", "outcome": "ok"}) == 1
    )
    assert (
        REGISTRY.get_sample_value("scs_job_last_success_timestamp_seconds", {"job": "probe_ok"})
        or 0
    ) > 0

    with pytest.raises(ValueError, match="nope"):
        asyncio.run(instrument_job("probe_bad", boom)())
    assert (
        REGISTRY.get_sample_value("scs_job_runs_total", {"job": "probe_bad", "outcome": "error"})
        == 1
    )
    # A failed run must NOT stamp last-success — that gauge is what the staleness alert reads, and
    # a failure that refreshed it would make the alert permanently unable to fire.
    assert (
        REGISTRY.get_sample_value("scs_job_last_success_timestamp_seconds", {"job": "probe_bad"})
        is None
    )
    # The duration is recorded on both paths: a job that dies after 20 minutes is a finding.
    assert _hist_count(JOB_SECONDS, job="probe_bad") == 1


def test_record_dashboard_write_tracks_failure_and_freshness_separately() -> None:
    """Neither half is sufficient. The counter catches a writer that is RAISING; the timestamp
    catches one that has silently stopped being called — which raises nothing at all."""
    record_dashboard_write("probe_artifact", ok=True)
    fresh = REGISTRY.get_sample_value(
        "scs_dashboard_artifact_written_timestamp_seconds", {"artifact": "probe_artifact"}
    )
    assert fresh is not None and fresh > 0
    record_dashboard_write("probe_artifact", ok=False)
    assert (
        REGISTRY.get_sample_value(
            "scs_dashboard_write_failures_total", {"artifact": "probe_artifact"}
        )
        == 1
    )
    # A failure must not refresh the freshness stamp, or a permanently-failing writer reads as
    # permanently up to date.
    assert (
        REGISTRY.get_sample_value(
            "scs_dashboard_artifact_written_timestamp_seconds", {"artifact": "probe_artifact"}
        )
        == fresh
    )


def test_export_canary_publishes_verdicts_and_retracts_indeterminate_ones() -> None:
    """An indeterminate verdict removes the series rather than publishing a number.

    Bars have no verdict before the 16:20 EOD batch lands. Publishing 0 there would alert every
    morning until someone muted the rule; publishing 1 would assert a pass nobody checked. Absence
    is the only honest encoding, and the alert rules are written to tolerate it.
    """
    export_canary_metrics(
        {
            "assertions": {
                "float_coverage": {"ok": True, "pct": 0.82},
                "news_recent": {"ok": False, "newest_age_h": 40.5},
                "bars_sane": {"ok": True},
            }
        }
    )
    assert REGISTRY.get_sample_value("scs_canary_ok", {"assertion": "float_coverage"}) == 1.0
    assert REGISTRY.get_sample_value("scs_canary_ok", {"assertion": "news_recent"}) == 0.0
    assert REGISTRY.get_sample_value("scs_canary_float_coverage_ratio") == 0.82
    assert REGISTRY.get_sample_value("scs_canary_news_age_hours") == 40.5

    export_canary_metrics({"assertions": {"bars_sane": {"ok": None, "symbols": 0}}})
    assert REGISTRY.get_sample_value("scs_canary_ok", {"assertion": "bars_sane"}) is None
    # Retracting one that was never published must not raise — the first canary of the day is
    # exactly that case, every day.
    export_canary_metrics({"assertions": {"never_seen": {"ok": None}}})


def test_export_canary_tolerates_a_malformed_payload() -> None:
    """It runs inside the tick's best-effort block, so it must never be the thing that breaks it."""
    export_canary_metrics({})
    export_canary_metrics({"assertions": "not a mapping"})
    export_canary_metrics({"assertions": {"weird": "not a mapping"}})
    export_canary_metrics({"assertions": {"float_coverage": {"ok": True, "pct": None}}})


def test_dataset_bytes_sums_parquet_per_dataset(tmp_path: Path) -> None:
    """The companion to file counts: files price the READ, bytes price the disk — and the disk is
    what the nightly harvest aborts on."""
    (tmp_path / "bars" / "dt=2026-08-08").mkdir(parents=True)
    (tmp_path / "bars" / "dt=2026-08-08" / "part-a.parquet").write_bytes(b"x" * 100)
    (tmp_path / "bars" / "dt=2026-08-08" / "part-b.parquet").write_bytes(b"x" * 50)
    (tmp_path / "bars" / "dt=2026-08-08" / "ignored.tmp").write_bytes(b"x" * 999)
    (tmp_path / "empty").mkdir()
    (tmp_path / "loose.txt").write_text("not a dataset")

    assert dataset_bytes(tmp_path) == {"bars": 150}
    assert dataset_bytes(tmp_path / "absent") == {}


def test_set_build_info_labels_an_unknown_commit_rather_than_an_empty_one() -> None:
    """`DEPLOYED_COMMIT` is empty on a local run and in any image built outside CI. An empty label
    value is legal and reads as a missing series in Grafana rather than as a local build."""
    set_build_info("9.9.9", "", "paper")
    assert (
        REGISTRY.get_sample_value(
            "scs_build_info", {"version": "9.9.9", "commit": "unknown", "mode": "paper"}
        )
        == 1
    )


def _hist_count(histogram: object, **labels: str) -> float:
    """Observation count of a labelled histogram child (0.0 before its first observation)."""
    name = histogram._name  # type: ignore[attr-defined]  # no public accessor for the metric name
    return REGISTRY.get_sample_value(f"{name}_count", labels) or 0.0
