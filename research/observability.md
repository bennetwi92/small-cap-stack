# Observability — what is measured, why, and what it costs

**Status:** LIVE since 2026-08-08 (#688). Supersedes nothing; before it, the collector half of this
system existed only on the box and was not in the repo at all.

The *what* lives in code and config, and this document does not repeat it:

| the thing | where it lives |
|---|---|
| the metrics | [`src/small_cap_stack/monitoring.py`](../src/small_cap_stack/monitoring.py) |
| the collector | [`deploy/alloy/config.alloy`](../deploy/alloy/config.alloy) |
| the dashboards | `deploy/grafana/dashboards/*.json` |
| the alerts | `deploy/grafana/alerts/scs-rules.yaml` |
| how to install it | [`deploy/RUNBOOK.md`](../deploy/RUNBOOK.md) §7 |
| the contract between them | `tests/test_observability_contract.py` |

This file is the *why*: the three questions the system answers, the reason the alert set is shaped
the way it is, and the series budget.

---

## 1. Three questions, three answers, and only one of them is Grafana

**Is the process alive?** → **Healthchecks.io**, and deliberately not Grafana. It is a dead-man's
switch that lives off-box and pings on every completed tick, so it does not depend on Alloy, on
this repo's config, or on Grafana Cloud being up. It predates everything else here and it stays the
first thing to trust. If Grafana and Healthchecks disagree about whether the tracker is alive,
Healthchecks is right.

**Is it doing its job correctly?** → **Prometheus + Grafana**, which is what #688 built. A
dead-man's switch structurally cannot answer this, and the gap is not theoretical: the app is
*built* so that it never falls over when it stops doing its job. Every dashboard write, every
per-symbol capture, every canary rebuild is wrapped in `except: log.warning(...)` so that one
failure cannot break a tick (#254). That is the right call for a data-collection phase. It also
means a persistently failing artefact writer, a dead float source or a stalling store append
produces a warning line nobody reads and **no other signal at all**.

Every counter in `monitoring.py` whose name ends `_failures_total` exists because there is a
swallowed exception behind it. The swallow is deliberate; the silence was not.

**Did the answer reach a human?** → the **alert rules**, and the thing that makes them worth having
is §3 below.

---

## 2. Three metric patterns, and what breaks when they are mixed up

- **Counters** for things that happen. Alert on `rate()` / `increase()`.
- **Gauges** for standing conditions — connected, mismatch, in-session. Alert on the value.
- **`*_last_success_timestamp_seconds` gauges** for things that must *keep* happening.

The third is the one worth stating. A counter that stops incrementing is invisible to `rate()` once
the last event ages past the lookback: the series sits flat, which is indistinguishable from
healthy-and-idle. `eod_bars` is the only thing in the system that fetches a day's 5-min bars, and
"it has not run" is exactly the failure that matters — so it gets a timestamp, and the alert reads
`time() - scs_job_last_success_timestamp_seconds{job="eod_bars"} > 36h`.

The same reasoning produces the freshness half of `record_dashboard_write`. `portfolio.json` is
rebuilt exactly twice a day (16:30 and 03:15, #458), so it can go a week stale without a single
exception being raised — a failure the write-failure counter is blind to by construction.

---

## 3. The alert set is shaped by one rule: a muted alert is worse than no alert

An alert that fires when nothing is wrong gets muted, and a muted alert is *worse* than none,
because it looks like coverage. Three consequences run through every rule in
`deploy/grafana/alerts/scs-rules.yaml`:

**Session gating.** Almost every symptom worth alerting on is *correct* outside a session. The
Gateway is restarted nightly at 23:45 by design; the scanner returns nothing at 21:00; there are no
opportunities on a Sunday. So the app publishes `scs_trading_day` and `scs_in_scan_window` from the
same variables the tick branches on — the alert and the app can never disagree about whether it is
a session — and every symptom rule multiplies by both.
`test_in_session_symptoms_are_gated_on_the_session` checks this rather than trusting it.

**`for:` longer than the thing's own recovery.** The supervisor reconnects with backoff and
`publish-dashboard` runs on a lagging 15-minute cron. A two-minute window would page on routine
recovery, every day.

**Every alert says what to do.** The descriptions cite `deploy/RUNBOOK.md` sections because at
04:00 the answer needs to be one link away, not a search.

### What is deliberately not alerted

- **Post-deploy JavaScript errors on the dashboard pages.** Assets are cached for 10 minutes and
  unversioned, so for ~10 minutes after a markup change a returning visitor runs the *previous* JS
  against the *current* HTML (CLAUDE.md). It self-heals. `SCSPagesDown` fires only when the site
  does not answer at all.
- **A single cold disconnect.** The supervisor's whole job is to recover from these. It is a panel,
  not a page.
- **Low opportunity counts.** Some sessions are genuinely quiet, and this is a data-collection
  phase — a quiet day is data, not an incident. `SCSScannerReturningNothing` needs a full hour of a
  live session before it says anything, for exactly this reason.

---

## 4. The canary gets its alert back, without the automation layer coming back

The data-quality canary (#346) writes positive-confirmation assertions over each day's raw
captures: float coverage, news recency, bar sanity. It catches the failure mode of a
store-raw/compute-on-read system — a dead float source or a dead news feed producing confident
**wrong** opportunities while every liveness signal stays green.

The CI watchdog that asserted those verdicts was rolled back with the rest of the GitHub automation
layer (§D-27, #377), and since then **nothing has read them automatically**. `canary.json` has been
written every five minutes for a human who mostly does not look.

`export_canary_metrics` publishes them as gauges, and the alert lives in Grafana. That is not a
re-creation of what was rolled back: Grafana is not this repo's CI, it does not open issues, it
does not comment on PRs, and it cannot dispatch work. It sends a notification to one person. §D-27
rejected an autonomous layer acting on the repo; it did not reject being told when the data is
wrong.

An **indeterminate** verdict (`ok: null` — bars have no verdict before the 16:20 EOD batch lands)
*removes* the series rather than publishing a value. Publishing 0 would alert every morning until
someone muted the rule; publishing 1 would assert a pass nobody checked. Absence is the only honest
encoding, and the alert rules are written to tolerate it.

---

## 5. Cardinality is a budget, and it is spent by labels

Grafana Cloud's free tier bills **10k active series** ([`free-tier-services.md`](./free-tier-services.md)).
Approximate current spend:

| source | series | note |
|---|---|---|
| the app (`scs_*`) | ~450 | dominated by the six histograms |
| node_exporter | ~700 | after `set_collectors`, the filesystem excludes and `unit_include` |
| cadvisor | ~250 | after `docker_only`, `store_container_labels = false` and the relabel drops |
| blackbox | ~30 | two targets |
| **total** | **~1.4k** | ~14% of the free tier |

The headroom is real but it is not free of traps, and all three are in `config.alloy` with the
reasoning inline:

1. **Container labels as metric labels.** compose stamps a config-hash label on each container that
   changes on **every deploy**, so each deploy would mint a fresh set of series that never expire.
2. **Every systemd unit on the box.** Three series apiece for `man-db.timer` and friends.
3. **overlay2 mounts.** One filesystem mount per container layer, churning on every deploy.

On the app's own side the rule is that **every label comes from a closed set** — `TICK_PHASES`,
`IBKR_REQUEST_KINDS`, `DASHBOARD_ARTIFACTS`, `CAPTURE_STAGES`, the scheduler's job ids, the store's
dataset names. A symbol, an opportunity id or a raw IBKR error code as a label would spend the
budget in a day; IBKR emits a distinct error code per rejected request, which is why
`scs_ibkr_connectivity_events_total` is labelled only with the three connectivity codes and
everything else returns before the counter. The closed sets are checked against their call sites by
`test_the_label_vocabularies_match_their_call_sites`, because a cardinality budget that is
documentation-only is how a free tier gets spent.

---

## 6. Why the dashboards are in the repo, and what stops them rotting

`docs/` has the same problem in different clothes: nothing links a page's HTML to the module that
reaches into it by id, so `tests/test_dashboard_dom.py` makes the permanent form of that mismatch
unmergeable (#406). Grafana is worse, because the two halves are not even in the same *system* — a
dashboard lives in Grafana Cloud, the metric lives in `monitoring.py`, and **nothing anywhere fails
when they disagree**. A renamed metric produces a panel that draws *No data*, which is
indistinguishable from "the thing being measured is quiet" — the worst possible failure for a
monitoring system, because it is indistinguishable from health on the screen you use to judge
health.

`tests/test_observability_contract.py` binds them in both directions: every `scs_*` a panel or rule
names must exist in the registry, and every `scs_*` the app defines must be named by a panel or a
rule. The second direction is about cost with no reader — a metric nobody looks at is series paid
for forever plus a wiring site to keep correct, so it is either worth watching or worth deleting.

**Workflow.** Edit dashboards *in Grafana*, then export (Share → Export → "Export for sharing
externally" off; keep the `ds` datasource variable) and commit the JSON. The contract test is the
gate, not a generator — a committed generator would fight the editing workflow it exists to serve.

---

## 7. What is deliberately absent

- **Loki / log shipping.** The app already emits structured JSON (`JSON_LOGS=true`) and
  `loki.source.docker` + `loki.process` would parse it into labelled streams. Nothing in the alert
  set depends on logs today, and 50 GB/month of free ingest is the sort of allowance that quietly
  stops being free. The seam is one Alloy component; add it when a panel actually wants to link to
  the lines behind a spike.
- **Self-hosted Prometheus + Grafana on the box.** Considered and rejected in
  [`free-tier-services.md`](./free-tier-services.md): the CX23 has 2 vCPU / 4 GB and has been taken
  down hard three times by memory pressure (#264/#273/#320). Adding a TSDB and a rendering server
  to the machine whose health you are trying to measure is the wrong trade. The cost is Grafana
  Cloud's **14-day retention**, which is fine — long-term history is the Parquet store, not the
  metrics.
- **Tracing.** There is one process, one loop and six scheduled jobs. Per-phase histograms answer
  everything a trace would, at a fraction of the setup.
- **Alerting on Phase-2 execution.** There are no orders yet. `scs_ibkr_open_orders` /
  `scs_ibkr_positions` are published now so the first one is visible without a code change, and
  `SCSClientIdRotatedWithWorkingOrders` is armed and silent until then — it catches the #677 hazard
  (a reconnect rotating the client id orphans a resting stop) which is unrecoverable and, until
  that alert existed, entirely silent.

---

## 8. The failure modes this was built from

Each of these already happened, and none of them had a signal at the time:

| what happened | the signal now |
|---|---|
| 36s/60s tick regression missed by three PRs (#321) | `scs_tick_duration_seconds` + per-phase histogram |
| small-file explosion, 32k one-row files (#247/#318/#319) | `scs_dataset_files` + `scs_store_read_seconds`, and the `delta()` alert |
| host OOM took the box past sshd, 5h37m of CI (#264) | `node_pressure_memory_waiting_seconds_total`, `node_vmstat_oom_kill` |
| portfolio build growing with history (#273) | `scs_job_duration_seconds{job="portfolio_refresh"}`, `scs_dataset_bytes` |
| paper mode over a live account (#663) | `scs_trading_mode_mismatch` — already a gauge; now alerted |
| dead feed behind a live socket (#677) | `scs_ibkr_data_farm_ok`, gated on the session |
| permanently-null float disqualifying a runner (#255) | `scs_capture_failures_total{stage="fundamentals"}`, canary `float_coverage` |
| canary verdicts nobody read since #377 | `scs_canary_ok` + `SCSCanaryFailing` |
| a scheduled workflow silently disabled by GitHub | `scs_published_status_age_seconds` (probed from outside) |
