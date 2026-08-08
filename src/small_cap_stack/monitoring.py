"""Observability (#5, expanded #688): the Prometheus metric surface + a Healthchecks.io switch.

Metrics are module-level (the idiomatic prometheus-client pattern) and incremented from the
app/capture/connection/storage layers. The Heartbeat pings Healthchecks.io each tick so an external
service alerts if the process dies or wedges; cold IBKR disconnects trigger a failure ping.

**What this surface is for.** Healthchecks answers *is the process alive*. It cannot answer *is it
still doing its job correctly*, and this application is built so that it never falls over when it
stops doing its job: every dashboard write, every per-symbol capture, every canary rebuild is
wrapped in ``except: log.warning(...)`` so a failure can't break a tick. That is the right call for
a data-collection phase — and it means a persistently failing artefact writer, a dead float source
or a stalling store append produces a warning line nobody reads and no other signal at all. Every
counter here whose name ends ``_failures_total`` exists because there is a swallowed exception
behind it, and the swallow is deliberate.

**Three patterns are used deliberately, and mixing them up breaks the alerts:**

- **Counters** for things that happen. Alert on ``rate()`` / ``increase()``.
- **Gauges** for standing conditions (connected, mismatch, in-session). Alert on the value.
- **``*_last_success_timestamp_seconds`` gauges** for things that must *keep* happening. A counter
  that stops incrementing is invisible to ``rate()`` once the event ages past the lookback — the
  series just sits flat, which is indistinguishable from healthy-and-idle. The timestamp turns
  absence into a number: ``time() - scs_job_last_success_timestamp_seconds{job="eod_report"}``.
  Anything whose *failure mode is silence* gets one.

**Labels come from closed sets** (:data:`TICK_PHASES`, :data:`IBKR_REQUEST_KINDS`,
:data:`DASHBOARD_ARTIFACTS`, the scheduler's job ids, the store's dataset names). Grafana Cloud's
free tier bills 10k active series, and a label fed from anything unbounded — a symbol, an
opportunity id, a raw IBKR error code — spends it in a day. ``research/observability.md`` carries
the series budget; ``tests/test_observability_contract.py`` pins names against the dashboards.

⚠️ **The published ``status.json`` stays coarse (#340/#344).** That payload is public, so raw
seconds and headroom numbers live here and are reachable via a Prometheus scrape or SSH, never on
the dashboard. Adding a metric here does not make it publishable.
"""

from __future__ import annotations

import asyncio
import shutil
import time
import urllib.request
from collections.abc import Awaitable, Callable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from prometheus_client import REGISTRY, Counter, Gauge, Histogram, start_http_server

from .logging import get_logger

log = get_logger(__name__)

# --- label vocabularies -----------------------------------------------------------------
# Closed sets, named here rather than passed as bare strings at the call site, so the cardinality
# of every labelled metric is readable in one place and a typo is a NameError instead of a second
# silent series that no panel selects.

#: Stages of a tick, timed separately. `_on_tick` is one function doing four unrelated things, and
#: "the tick took 47s" does not say which — #321's regression was the scan, but the status export
#: grows with history (#273) and the stats/charts refresh reads the whole day.
TICK_PHASES = ("scan", "capture", "status", "stats_charts")

#: IBKR request kinds, by the pacing class they belong to. Historical bars are the rate-limited one
#: (<60 requests / 10 min, CLAUDE.md); news and the scanner are not, but share the timeout.
IBKR_REQUEST_KINDS = ("scanner", "historical_bars", "news")

#: Dashboard payloads written best-effort under `/data/dashboard`. Each is a separate failure
#: domain — `portfolio.json` can fail for a week while `status.json` writes fine every tick, and
#: before this the only difference between those two worlds was which warning scrolled past.
DASHBOARD_ARTIFACTS = ("status", "stats", "charts", "index", "portfolio", "canary")

#: Capture stages that swallow a per-symbol failure so the rest of the batch proceeds.
CAPTURE_STAGES = (
    "open_opportunity",
    "scanner_hits",
    "news",
    "day_bars",
    "day_news",
    "fundamentals",
    "backfill",
)

# Buckets are chosen against the thing each measures, not copied from prometheus-client's default
# (which tops out at 10s and would put every EOD job in +Inf).
_TICK_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0)
_JOB_BUCKETS = (0.5, 1.0, 5.0, 15.0, 30.0, 60.0, 120.0, 300.0, 600.0, 1800.0)
_REQUEST_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0)
_APPEND_BUCKETS = (0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0, 5.0)
_READ_BUCKETS = (0.01, 0.05, 0.1, 0.5, 1.0, 5.0, 15.0, 60.0)

# --- identity ---------------------------------------------------------------------------
BUILD_INFO = Gauge(
    "scs_build_info",
    "Always 1; the labels carry the identity of the running build",
    ["version", "commit", "mode"],
)
APP_START_TIME = Gauge(
    "scs_app_start_timestamp_seconds", "Unix time the process finished starting up"
)

# --- session gating ---------------------------------------------------------------------
# The single most important pair for alerting. Almost every symptom below — no scan results, no
# opportunities, a disconnected Gateway — is *correct* at 02:00 on a Sunday, and an alert that
# cannot tell the difference gets muted, which is the same as not existing. Every session-scoped
# rule in `deploy/grafana/alerts/` multiplies by one of these.
TRADING_DAY = Gauge("scs_trading_day", "1 when today is an XNYS session (minus the override list)")
IN_SCAN_WINDOW = Gauge("scs_in_scan_window", "1 when now is inside the configured scan window")

# --- the tick ---------------------------------------------------------------------------
SCAN_TICKS = Counter("scs_scan_ticks_total", "Scan/capture ticks executed")
OPPORTUNITIES = Counter("scs_opportunities_total", "Opportunities opened")
BARS_APPENDED = Counter("scs_bars_appended_total", "5-min bars appended")
# Tick self-reporting (#321): three PRs missed a 36s/60s tick regression because nothing measured
# the tick. These are also surfaced in status.json every tick, so they're readable on the
# dashboard without SSH or a Prometheus scrape.
TICK_SECONDS = Gauge("scs_tick_seconds", "Duration of the last completed tick")
# The gauge above is the *last* tick and is what `status.json` reports; this is the distribution.
# A p95 is what distinguishes "one slow tick after a deploy" from "the tick is now 40s every time",
# and a last-value gauge scraped every 60s cannot tell those apart at all.
TICK_DURATION = Histogram("scs_tick_duration_seconds", "Tick duration", buckets=_TICK_BUCKETS)
TICK_PHASE_SECONDS = Histogram(
    "scs_tick_phase_seconds", "Duration of one phase of a tick", ["phase"], buckets=_TICK_BUCKETS
)
TICKS_OVER_BUDGET = Counter(
    "scs_ticks_over_budget_total", "Ticks that ran longer than half the tick interval"
)
JOBS_MISSED = Counter(
    "scs_jobs_missed_total",
    "Scheduled jobs skipped entirely (max_instances/misfire) — previously invisible",
)

# --- scheduled jobs ---------------------------------------------------------------------
# Every job the scheduler owns is wrapped (see `instrument_job`), so a job added tomorrow is
# measured without anyone remembering to measure it — the same reasoning as
# `tests/test_settings_wiring.py` deriving its requirement from a signature.
JOB_RUNS = Counter("scs_job_runs_total", "Scheduled job invocations", ["job", "outcome"])
JOB_SECONDS = Histogram(
    "scs_job_duration_seconds", "Scheduled job duration", ["job"], buckets=_JOB_BUCKETS
)
JOB_LAST_SUCCESS = Gauge(
    "scs_job_last_success_timestamp_seconds",
    "Unix time a job last completed without raising",
    ["job"],
)

# --- the scanner ------------------------------------------------------------------------
SCAN_CANDIDATES = Gauge("scs_scan_candidates", "Rows the most recent scan returned")
SCAN_LAST_SUCCESS = Gauge(
    "scs_scan_last_success_timestamp_seconds", "Unix time the scanner last returned successfully"
)

# --- capture ----------------------------------------------------------------------------
CAPTURE_FAILURES = Counter(
    "scs_capture_failures_total",
    "Per-symbol capture failures that were swallowed so the batch could continue",
    ["stage"],
)

# --- the IBKR connection ----------------------------------------------------------------
IBKR_CONNECTED = Gauge("scs_ibkr_connected", "1 if connected to IBKR else 0")
# Split from the gauge above on purpose (#163-C2/#677): error 1100 leaves the API socket OPEN, so
# the process is connected and receiving nothing. `is_connected()` folds the two together for
# callers; alerting needs them apart, because "socket closed" and "feed dead behind a live socket"
# have different causes and only the second looks like a quiet tape.
IBKR_DATA_FARM_OK = Gauge("scs_ibkr_data_farm_ok", "1 while the market-data farm is up (no 1100)")
# A gauge, not a counter (#663): a mode mismatch is a standing condition until someone fixes the
# config, not an event to accumulate. `IBKR_TRADING_MODE` labels the mode; the connected account's
# id is the only authoritative answer, so this is the one signal that can tell you the dashboard is
# saying "paper" over a live account.
TRADING_MODE_MISMATCH = Gauge(
    "scs_trading_mode_mismatch",
    "1 when IBKR_TRADING_MODE disagrees with the connected account's real mode",
)
COLD_DISCONNECTS = Counter("scs_cold_disconnects_total", "Cold (unexpected) IBKR disconnects")
IBKR_DISCONNECTS = Counter(
    "scs_ibkr_disconnects_total", "IBKR disconnects, by whether they were expected", ["expected"]
)
IBKR_CONNECT_ATTEMPTS = Counter("scs_ibkr_connect_attempts_total", "Connection attempts made")
IBKR_CONNECT_FAILURES = Counter(
    "scs_ibkr_connect_failures_total", "Connection attempts that raised"
)
IBKR_RESYNCS = Counter("scs_ibkr_resyncs_total", "On-connect resyncs, by outcome", ["outcome"])
# Labelled by code, and safe to be: `classify_connection_error` only routes 1100/1101/1102, so the
# label space is three values. Labelling by the RAW code would be unbounded — IBKR emits a
# per-request error code for every rejected contract, and a bad symbol list would mint a series
# apiece.
IBKR_CONNECTIVITY_EVENTS = Counter(
    "scs_ibkr_connectivity_events_total", "Connectivity errors 1100/1101/1102 received", ["code"]
)
IBKR_CLIENT_ID = Gauge("scs_ibkr_client_id", "Client id the live connection is using")
# From `BrokerState` — free, and zero in Phase 1 by construction. They are here so the first order
# placed in Phase 2 is visible without a code change, and so "the broker says we hold something and
# the app doesn't" is answerable from the same place as everything else (#313, decisions §D-43).
IBKR_OPEN_ORDERS = Gauge(
    "scs_ibkr_open_orders", "Working orders the broker reported at last resync"
)
IBKR_POSITIONS = Gauge("scs_ibkr_positions", "Positions the broker reported at last resync")
IBKR_REQUESTS = Counter(
    "scs_ibkr_requests_total", "IBKR API requests, by kind and outcome", ["kind", "outcome"]
)
IBKR_REQUEST_SECONDS = Histogram(
    "scs_ibkr_request_seconds", "IBKR API request duration", ["kind"], buckets=_REQUEST_BUCKETS
)

# --- the store --------------------------------------------------------------------------
DATASET_FILES = Gauge(
    "scs_dataset_files",
    "Parquet files per dataset — for this store, read cost tracks file count, not rows",
    ["dataset"],
)
DATASET_BYTES = Gauge("scs_dataset_bytes", "Bytes on disk per dataset", ["dataset"])
STORE_APPEND_SECONDS = Histogram(
    "scs_store_append_seconds", "Store append duration", ["dataset"], buckets=_APPEND_BUCKETS
)
STORE_APPEND_ROWS = Counter("scs_store_append_rows_total", "Rows appended", ["dataset"])
STORE_APPEND_FAILURES = Counter(
    "scs_store_append_failures_total", "Appends that raised", ["dataset"]
)
# The number the cost model actually predicts (CLAUDE.md): read cost tracks FILE count, so a
# small-file explosion shows up here as latency long before it shows up as an OOM. #318/#319/#321
# were diagnosed after the fact from a 36s tick; this is the leading indicator.
STORE_READ_SECONDS = Histogram(
    "scs_store_read_seconds", "Store read duration", ["dataset"], buckets=_READ_BUCKETS
)

# --- dashboard artefacts ----------------------------------------------------------------
STATUS_BUILD_SECONDS = Gauge("scs_status_build_seconds", "Duration of the last status build")
DASHBOARD_WRITE_FAILURES = Counter(
    "scs_dashboard_write_failures_total",
    "Dashboard payload writes that raised (each is a swallowed exception)",
    ["artifact"],
)
DASHBOARD_ARTIFACT_WRITTEN = Gauge(
    "scs_dashboard_artifact_written_timestamp_seconds",
    "Unix time a dashboard payload was last written successfully",
    ["artifact"],
)

# --- the data-quality canary ------------------------------------------------------------
# #346 wrote these verdicts to a JSON file, and the CI watchdog that asserted them was rolled back
# with the rest of the automation layer (#377) — so since then nothing has read them automatically.
# As gauges they are alertable without resurrecting an automation layer: the alert lives in Grafana,
# which is not this repo's CI and does not open issues.
CANARY_OK = Gauge(
    "scs_canary_ok",
    "1 pass / 0 fail per canary assertion (absent when indeterminate)",
    ["assertion"],
)
CANARY_FLOAT_COVERAGE = Gauge(
    "scs_canary_float_coverage_ratio", "Fraction of today's opportunities with a usable float"
)
CANARY_NEWS_AGE_HOURS = Gauge(
    "scs_canary_news_age_hours", "Age of the newest story across today's opportunities"
)

# --- host headroom ----------------------------------------------------------------------
# Also read from node_exporter via Alloy, deliberately. These are what the APP saw when it made a
# decision, which is the number that explains a skipped rebuild; node's is the truth about the box.
# When they disagree, the app is looking at the wrong filesystem — which is itself the finding.
MEM_AVAILABLE_MB = Gauge("scs_mem_available_mb", "Host MemAvailable as the app reads it")
DISK_USED_PCT = Gauge("scs_disk_used_pct", "Percent used of the filesystem holding the data dir")


def metric_value(name: str) -> float:
    """Read a metric's current value from the default registry (0.0 if absent)."""
    return REGISTRY.get_sample_value(name) or 0.0


def metric_names() -> set[str]:
    """Every metric name the default registry currently exposes, without the sample suffixes.

    ``scs_tick_duration_seconds`` is exported as ``_bucket``/``_count``/``_sum`` samples and
    ``scs_scan_ticks_total`` as ``_total``/``_created``; a dashboard or an alert names the metric,
    not a sample. Stripping here is what lets ``tests/test_observability_contract.py`` compare the
    two sides directly instead of maintaining a hand-written list of what exists.
    """
    names: set[str] = set()
    for metric in REGISTRY.collect():
        names.add(metric.name)
        for sample in metric.samples:
            for suffix in ("_bucket", "_count", "_sum", "_total", "_created"):
                if sample.name.endswith(suffix):
                    names.add(sample.name[: -len(suffix)])
                    break
            else:
                names.add(sample.name)
    return names


def set_build_info(version: str, commit: str, mode: str) -> None:
    """Publish the running build's identity as an info-style gauge.

    The convention (``*_info`` at a constant 1, everything interesting in labels) exists so a
    dashboard can join on it — "which commit was deployed when the tick got slow" is answerable by
    overlaying this on any other panel, and it is the only place the deployed SHA reaches Grafana.
    """
    BUILD_INFO.labels(version=version, commit=commit or "unknown", mode=mode).set(1)


@contextmanager
def observe(histogram: Histogram, **labels: str) -> Iterator[None]:
    """Time a block into ``histogram``, **including when it raises**.

    ``prometheus_client``'s own ``.time()`` decorator does the same thing; this exists because the
    label-passing form reads better at the call sites here and because a failed call is exactly the
    one whose duration matters — an IBKR request that times out at 30s is the observation, and a
    ``try/finally`` written by hand at six call sites is six chances to drop the ``finally``.
    """
    start = time.perf_counter()
    try:
        yield
    finally:
        metric = histogram.labels(**labels) if labels else histogram
        metric.observe(time.perf_counter() - start)


@contextmanager
def ibkr_request(kind: str) -> Iterator[None]:
    """Time and count one IBKR API call, separating a **timeout** from any other failure.

    The distinction is the point. Every request in this app is bounded by
    ``ibkr_request_timeout_sec`` (#163-C2), so the two failure modes look identical in a log — but
    they mean different things: a raised error is IBKR refusing the request, while a timeout is the
    Gateway not answering, which is what a pacing violation, a wedged farm and a saturated box all
    look like from here. ``rate(scs_ibkr_requests_total{outcome="timeout"})`` on ``historical_bars``
    is the pacing signal (<60 requests / 10 min, CLAUDE.md); on the scanner it is the one that
    silently costs a tick.

    A sync context manager, deliberately: the body may contain ``await``s, and nothing here needs
    to be awaited itself.
    """
    start = time.perf_counter()
    outcome = "error"
    try:
        yield
    except TimeoutError:
        outcome = "timeout"
        raise
    else:
        outcome = "ok"
    finally:
        IBKR_REQUEST_SECONDS.labels(kind=kind).observe(time.perf_counter() - start)
        IBKR_REQUESTS.labels(kind=kind, outcome=outcome).inc()


def record_dashboard_write(artifact: str, *, ok: bool) -> None:
    """Record the outcome of a best-effort dashboard payload write.

    Both halves are needed and neither is sufficient. The failure counter catches a writer that is
    *raising*; the success timestamp catches one that has silently stopped being called at all —
    a job that no longer runs raises nothing, so the counter stays flat and looks healthy.
    """
    if ok:
        DASHBOARD_ARTIFACT_WRITTEN.labels(artifact=artifact).set(time.time())
    else:
        DASHBOARD_WRITE_FAILURES.labels(artifact=artifact).inc()


def export_canary_metrics(payload: Mapping[str, Any]) -> None:
    """Mirror a built canary payload (#346) into gauges.

    Kept here rather than inside ``canary.py`` so that module stays pure over the store — it is
    called from analysis paths and by the dashboard backfill, and neither should be writing to a
    process-global registry as a side effect of computing a verdict.

    An **indeterminate** verdict (``ok: null`` — bars before the EOD batch has landed) removes the
    series rather than publishing a value. Publishing 0 would alert every morning; publishing 1
    would assert a pass nobody checked. Absence is the honest encoding, and it is what
    ``ok`` alert rules must be written to tolerate.
    """
    assertions = payload.get("assertions") or {}
    if not isinstance(assertions, Mapping):
        return
    for name, verdict in assertions.items():
        if not isinstance(verdict, Mapping):
            continue
        ok = verdict.get("ok")
        if ok is None:
            _retract_canary(str(name))
            continue
        CANARY_OK.labels(assertion=name).set(1.0 if ok else 0.0)
        _CANARY_PUBLISHED.add(str(name))
    float_pct = _nested(assertions, "float_coverage", "pct")
    if isinstance(float_pct, int | float):
        CANARY_FLOAT_COVERAGE.set(float(float_pct))
    news_age = _nested(assertions, "news_recent", "newest_age_h")
    if isinstance(news_age, int | float):
        CANARY_NEWS_AGE_HOURS.set(float(news_age))


#: Assertions that currently have a published ``CANARY_OK`` child. Tracked here rather than probed
#: off the metric because ``Gauge.remove`` raises ``KeyError`` for a label set that was never
#: created — and the first canary of the day is exactly that case, so a bare ``try/except KeyError``
#: would also swallow a real one from elsewhere in the loop.
_CANARY_PUBLISHED: set[str] = set()


def _retract_canary(assertion: str) -> None:
    """Drop an assertion's series when its verdict goes indeterminate."""
    if assertion in _CANARY_PUBLISHED:
        CANARY_OK.remove(assertion)
        _CANARY_PUBLISHED.discard(assertion)


def _nested(assertions: Mapping[str, Any], key: str, field: str) -> Any:
    verdict = assertions.get(key)
    return verdict.get(field) if isinstance(verdict, Mapping) else None


def instrument_job(
    job_id: str, job: Callable[[], Awaitable[None]]
) -> Callable[[], Awaitable[None]]:
    """Wrap a scheduled coroutine so its runs, duration and last success are measured.

    Applied to **every** job in ``build_scheduler``, not to a chosen few, so a job added later is
    instrumented by construction. That matters more than it sounds: the jobs are where the
    irreplaceable work happens — ``eod_bars`` is the only thing that fetches a day's bars, and
    ``JOBS_MISSED`` (#321) counts a job APScheduler skipped, which is a different failure from one
    that ran and raised. Before this, the second was a log line.

    The exception is re-raised: swallowing here would change the app's behaviour, and APScheduler
    already logs an unhandled job error. This only observes.
    """

    async def instrumented() -> None:
        start = time.perf_counter()
        outcome = "error"
        try:
            await job()
            outcome = "ok"
        finally:
            JOB_SECONDS.labels(job=job_id).observe(time.perf_counter() - start)
            JOB_RUNS.labels(job=job_id, outcome=outcome).inc()
            if outcome == "ok":
                JOB_LAST_SUCCESS.labels(job=job_id).set(time.time())

    return instrumented


def mem_available_mb(meminfo: Path = Path("/proc/meminfo")) -> float | None:
    """Host MemAvailable in MB, or None where unreadable (macOS dev, sandboxes).

    Read from /proc/meminfo, which under plain Docker reports the HOST, not the cgroup — exactly
    right here: the OOM history (#264/#273) is host-level pressure, and the app cgroup's own limit
    (#329) is the *containment* for it, not the signal.
    """
    try:
        for line in meminfo.read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) / 1024.0  # kB -> MB
    except (OSError, ValueError, IndexError):
        return None
    return None


def disk_used_pct(path: Path) -> float | None:
    """Percent of the filesystem holding ``path`` in use, or None where unreadable."""
    try:
        usage = shutil.disk_usage(path)
    except OSError:
        return None
    return usage.used / usage.total * 100.0


def dataset_bytes(data_dir: Path) -> dict[str, int]:
    """Bytes on disk per dataset directory.

    The companion to ``Store.file_counts``. File count is what prices a *read* here (CLAUDE.md);
    bytes is what prices the disk, and the disk is what the nightly harvest aborts on
    (``harvest_min_disk_free_mb``). Knowing which dataset is growing is the difference between
    "delete something" and "delete the right thing".
    """
    if not data_dir.exists():
        return {}
    out: dict[str, int] = {}
    for child in sorted(data_dir.iterdir()):
        if not child.is_dir():
            continue
        total = 0
        for path in child.glob("**/*.parquet"):
            try:
                total += path.stat().st_size
            except OSError:  # a file compacted away mid-walk is not an error worth raising
                continue
        if total:
            out[child.name] = total
    return out


def start_metrics_server(port: int) -> None:
    """Expose /metrics on the given port (no-op-safe to call once at startup)."""
    start_http_server(port)
    APP_START_TIME.set(time.time())
    log.info("metrics.server_started", port=port)


def _http_get(url: str) -> None:
    with urllib.request.urlopen(url, timeout=5) as resp:  # noqa: S310 — trusted Healthchecks URL
        resp.read()


class Heartbeat:
    """Healthchecks.io dead-man's switch. No-op when no URL is configured."""

    def __init__(
        self, url: str, fetch: Callable[[str], None] | None = None, timeout_sec: float = 10.0
    ) -> None:
        self.url = url.rstrip("/")
        self._fetch = fetch or _http_get
        self.timeout_sec = timeout_sec

    async def ping(self) -> None:
        await self._send(self.url)

    async def fail(self) -> None:
        await self._send(f"{self.url}/fail")

    async def _send(self, url: str) -> None:
        if not self.url:
            return
        try:
            async with asyncio.timeout(self.timeout_sec):
                await asyncio.to_thread(self._fetch, url)
        except Exception:  # noqa: BLE001 — heartbeat is best-effort, never break the loop
            log.warning("heartbeat.failed", url=url, exc_info=True)
