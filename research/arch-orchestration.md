# Orchestration, Scheduling & Process Supervision for a Long-Lived Event-Driven Trading App (Single VPS)

_Research date: 2026-06-29. Target: single Oracle Cloud Ampere ARM VPS (~12-24 GB, headless, unattended), Python, free/open-source only, solo developer._

---

## TL;DR Recommendation

**Run the trading core as a single long-lived `asyncio` process supervised by a `systemd` service (`Restart=always`).** Model "tasks with dependencies" *inside* that process using `asyncio.TaskGroup` (Python 3.11+) — or `anyio` if you want richer cancellation/readiness primitives. Use **APScheduler 3.x (`AsyncIOScheduler`)** *inside* the same event loop for the time-triggered jobs (04:00 ET scan start, 11:59 ET stop, EOD report), with a `cron` trigger pinned to `America/New_York`. Run **IB Gateway via the `gnzsnz/ib-gateway-docker` image with IBC** in its own container with `restart: unless-stopped` and `AutoRestartTime` for the daily IBKR restart; the Python app reconnects via `ib_async`. Deploy with **Docker Compose** (one file: gateway + app), optionally fronted by a small **Ansible** playbook if/when you want reproducible host provisioning.

**Do NOT adopt Airflow / Dagster / Prefect / Temporal / Celery.** They are batch-DAG or distributed-workflow engines that fight a real-time event loop and add a heavyweight always-on scheduler/worker/DB tier that is overkill for one box and one developer. The orchestration tension is explained below.

---

## The Core Tension: Batch DAG Orchestrators vs. a Real-Time Event Loop

The README language — "processes that spawn tasks, tasks are managed, tasks can have dependencies" — *sounds* like it's asking for a workflow orchestrator (Airflow/Dagster/Prefect). It is not, and conflating the two is the central architecture risk here.

- **Airflow, Dagster, and Prefect are scheduled-DAG / data-pipeline tools.** They model a workflow as a directed acyclic graph of discrete tasks that **start, run to completion, and exit**, typically on a schedule or trigger. They are built for "run this pipeline at 02:00, materialize these assets, finish." ([astronomer](https://llms.astronomer.io/managed-airflow-vs-dagster-vs-temporal), [dev.to 2026](https://dev.to/datastackx/airflow-vs-prefect-vs-dagster-picking-the-right-orchestrator-in-2026-1ifb))
- **The trading core is the opposite shape**: a 24/5 process that stays resident, holds an open socket to IB Gateway, and reacts to live market-data ticks via callbacks on an `asyncio` loop (`ib_async` is asyncio-native). There is no "run to completion" — the loop *is* the application. A DAG engine has no good way to represent "a coroutine that lives for 5 days reacting to ticks."
- All of Airflow/Prefect/Dagster **require always-on scheduler/daemon processes** (and usually a metadata DB), and Temporal workers must poll continuously ([astronomer](https://llms.astronomer.io/managed-airflow-vs-dagster-vs-temporal)). On a single VPS that's a second always-on subsystem competing for RAM with your actual trading process, for no benefit.

**Conclusion:** "tasks with dependencies" should be satisfied **in-process** with structured concurrency, and the time-triggered parts with a lightweight scheduler — not by importing a distributed orchestrator. The daily intraday pipeline (scanner → gate checks → raw data capture → EOD report) is a sequence of coroutine stages with ordering constraints; that is a structured-concurrency problem, not a Kubernetes-scale workflow problem.

---

## 1. In-Process Task Model (tasks + dependencies inside the event loop)

| Option | Maturity | Maintenance | Footprint | ARM | Learning curve | Verdict |
|---|---|---|---|---|---|---|
| **`asyncio` + `TaskGroup`** | Stdlib, 3.11+ | Tracks CPython | Zero deps | N/A (pure Python) | Low (already using asyncio) | **Recommended baseline** |
| **`anyio`** | Mature (powers Prefect, HTTPX, FastAPI ecosystem) | Very active (agronholm) | 1 small pure-Python dep | Pure Python | Low-medium | **Recommended if you want richer cancellation/readiness** |
| Lightweight actor model (e.g. roll-your-own, or `thespian`/`pykka`) | Varies; several are semi-dormant | Mixed | Extra dep + mental model | Pure Python | Medium-high | Overkill — adds a paradigm you don't need |

**Details & recommendation.**

- `asyncio.TaskGroup` (Python 3.11) gives structured concurrency: child tasks are scoped to the group, exceptions propagate, and cleanup is guaranteed even if the parent is cancelled — there's no window where tasks outlive their parent ([dataleadsfuture](https://www.dataleadsfuture.com/why-taskgroup-and-timeout-are-so-crucial-in-python-3-11-asyncio/)). It is the natural, dependency-free way to express "stage B runs after stage A; if A fails, cancel the rest."
- TaskGroup's API is deliberately narrow: it gives **no way to list or individually cancel contained tasks**, and **no task-readiness signal** ([anyio docs](https://anyio.readthedocs.io/en/stable/why.html), [Matt Westcott](https://mattwestcott.org/blog/structured-concurrency-in-python-with-anyio)). If you need to cancel a single sub-pipeline (e.g. "stop the scanner but keep capturing"), or wait until a task signals "ready," `anyio`'s task groups add a per-group **cancel scope**, `move_on_after()` timeouts, and readiness primitives — same structured-concurrency semantics, strictly more capable ([anyio why](https://anyio.readthedocs.io/en/stable/why.html), [applifting](https://applifting.io/blog/python-structured-concurrency)).
- **Model dependencies as code, not as a graph object.** For a linear daily pipeline, `await stage()` ordering inside a TaskGroup is sufficient. If stages fan out (e.g. several gate checks in parallel that must all pass before capture), use a TaskGroup per fan-out stage and sequence the stages. Reach for a tiny dependency helper (a dict of `asyncio.Event`s, or `graphlib.TopologicalSorter` from the stdlib to order stages) only if the dependency graph genuinely becomes non-linear — still zero external deps.

**Pick:** `asyncio.TaskGroup` as the default; adopt `anyio` if you find yourself needing per-task cancellation, timeouts, or readiness gating (likely, given live-data reconnection logic). Skip actor frameworks.

---

## 2. Time-Based Scheduling (04:00 ET scan start / 11:59 ET stop / EOD report)

| Option | Maturity | Maintenance | Footprint | ARM | Fit for asyncio event loop |
|---|---|---|---|---|---|
| **APScheduler 3.x `AsyncIOScheduler`** | Very mature (3.11.x current) | Actively maintained | Pure Python, in-process | Pure Python | **Best — runs *in* your existing loop** |
| `schedule` | Mature, tiny | Maintained but minimal | Pure Python | Pure Python | Poor — sync, blocking, no tz/cron, needs own thread |
| `cron` (system) | Decades | OS | None | Native | OK for *external* triggers only; can't reach into a running loop |
| `systemd` timers | Mature | OS | None | Native | OK for external triggers; better logging/deps than cron |

**Details & recommendation.**

- **APScheduler `AsyncIOScheduler` is the right tool** because it runs jobs directly on your existing asyncio event loop and can execute native coroutines, with a `cron` trigger type for "at 04:00 / 11:59 on weekdays" ([apscheduler asyncio docs](https://apscheduler.readthedocs.io/en/3.x/modules/schedulers/asyncio.html), [user guide](https://apscheduler.readthedocs.io/en/3.x/userguide.html)). It is the "robust, production-ready" choice vs. the deliberately minimal `schedule` library ([leapcell](https://leapcell.io/blog/scheduling-tasks-in-python-apscheduler-versus-schedule)). Set the trigger timezone explicitly to `America/New_York` so DST is handled correctly (critical for "ET" semantics).
- **Maintenance note on 4.0:** APScheduler 4.0 is a ground-up rewrite (workers pull jobs from a data store; full async via AnyIO) but is **pre-release and explicitly "do NOT use in production"** — `4.0.0a6` landed 2025-04-27 and the API may still change incompatibly ([4.0 tracking issue #465](https://github.com/agronholm/apscheduler/issues/465), [migration docs](https://github.com/agronholm/apscheduler/blob/master/docs/migration.rst), [PyPI](https://pypi.org/project/APScheduler/)). **Use the stable 3.11.x line.** 3.x is still receiving releases, so this is a safe, maintained choice today.
- **Why not `schedule`:** it's synchronous and blocking, has no timezone/cron support, and you'd have to run it in a side thread and marshal back into the loop — strictly worse here ([leapcell](https://leapcell.io/blog/scheduling-tasks-in-python-apscheduler-versus-schedule)).
- **Why not cron/systemd timers for this:** they launch *new processes*; they can't tell your already-running, stateful event loop "begin scanning now." They are excellent for *external, fire-and-forget* jobs (e.g. nightly log rotation, a standalone backfill script), and `systemd` timers beat cron for logging, dependencies, and `OnCalendar` clarity — but the scan-start/stop signals belong *inside* the live process via APScheduler. (A defensible alternative: keep the EOD report as a **separate** `systemd` timer + oneshot service if you want it decoupled from the trading process's health. Reasonable, but APScheduler keeps everything in one place.)

**Pick:** APScheduler 3.x `AsyncIOScheduler` with `CronTrigger(..., timezone="America/New_York")`, embedded in the trading process.

---

## 3. Workflow Orchestrators — Fit & Overkill Assessment

| Tool | What it's for | Always-on overhead | ARM | Verdict for this project |
|---|---|---|---|---|
| **Airflow** | Scheduled DAGs of batch tasks; large data platforms | Scheduler + webserver + metadata DB | Yes | **Overkill.** Built for 100+ pipelines / platform teams ([dev.to](https://dev.to/datastackx/airflow-vs-prefect-vs-dagster-picking-the-right-orchestrator-in-2026-1ifb)) |
| **Dagster** | Asset-aware data orchestration | Daemon + DB + web UI | Yes | **Overkill.** Great DX for data assets, wrong shape for a live loop |
| **Prefect** | Python-native pipeline orchestration | Server/agent processes | Yes | **Overkill** here, but the *least* heavy of the DAG tools; see note |
| **Celery** | Distributed task queue | Broker (Redis/RabbitMQ) + workers | Yes | **Overkill.** Distributed worker pool you don't need on one box |
| **Temporal** | Durable long-running stateful workflows | Server + PostgreSQL + workers (continuous poll) | Yes (linux/arm64 builds, CLI) | **Overkill**, but the most *conceptually* relevant; see note |

**Details.**

- These are **centralized orchestrators** — a coordinator schedules/monitors every step, and if it goes down all pipelines stall — and every one of them keeps an always-on scheduler/daemon (and usually a DB) resident ([astronomer](https://llms.astronomer.io/managed-airflow-vs-dagster-vs-temporal)). On a single VPS that's a permanent RAM/CPU tax on top of the trading process.
- **When would one actually help?** If you needed (a) durable execution that survives crashes mid-workflow and resumes exactly where it left off, (b) cross-machine distribution, or (c) a rich observability UI / retry-and-backfill console across many pipelines. **Temporal** is the only one whose model ("long-running, stateful, event-driven workflows reacting to external signals, reliable across failures") genuinely overlaps with a trading system ([zenml temporal-vs-airflow](https://www.zenml.io/blog/temporal-vs-airflow), [astronomer](https://llms.astronomer.io/managed-airflow-vs-dagster-vs-temporal)). Temporal does run on ARM64 (`--platform linux/arm64`, CLI for linux arm64) ([temporal self-hosted guide](https://docs.temporal.io/self-hosted-guide)).
- **Why still no, today:** Temporal self-hosted needs a server + PostgreSQL + continuously-polling workers; its own guidance is that once you need real reliability/scale you should pay for the managed service ([procycons](https://procycons.com/en/blogs/workflow-orchestration-platforms-comparison-2025/), [taigrr](https://blog.taigrr.com/blog/setting-up-a-production-ready-temporal-server/)). For a solo dev on one box, that operational weight and the rewrite of your trading logic into Temporal workflow/activity primitives is disproportionate. **Revisit Temporal only if** durability (resume-exactly-where-it-failed across process restarts) becomes a hard requirement you can't meet with `systemd` restart + idempotent reconnection logic.
- **Prefect** earns an honorable mention as "fastest path from a Python script to a scheduled production pipeline with minimal infra" ([orchestra](https://www.getorchestra.io/guides/best-apache-airflow-alternatives-in-2026-for-modern-data-teams)) and is built on `anyio` internally ([prefect blog](https://www.prefect.io/blog/oss-love-letters-how-anyio-powers-prefects-async-architecture)). If you later want a UI + retry history specifically for the **EOD batch report** (a true run-to-completion job), Prefect is the most reasonable add-on. It is still unnecessary for the live loop.

**Pick:** none. Use in-process structured concurrency + APScheduler. Keep Temporal on a "if durability becomes critical" watch-list and Prefect on a "if the EOD batch grows a UI need" watch-list.

---

## 4. Process Supervision / Keep-Alive (trading app + IB Gateway, unattended)

| Option | Maturity | Maintenance | Footprint | ARM | Fit |
|---|---|---|---|---|---|
| **`systemd` service** | OS-standard | OS | None (already on the host) | Native | **Best for the bare-metal Python app** |
| **Docker Compose `restart:` policy** | Mature | Docker | Container runtime | Yes | **Best for IB Gateway container** |
| **supervisord** | Mature but older (Python 2 heritage; runs on 3) | Low activity | Small daemon | Pure Python | Redundant if you have systemd; useful only inside a container |

**Details & recommendation.**

- **`systemd` with `Restart=always`** is the established, zero-extra-dependency way to keep a long-lived process alive and auto-restart on crash or reboot ([bootvar](https://bootvar.com/systemd-service-for-docker-compose/)). It also gives you `journald` logging, resource limits, `WatchdogSec` liveness, and ordering/`After=` dependencies. This is the supervisor for the trading process (whether you run it bare or as `docker compose up` via a systemd unit).
- **Docker Compose restart policies** (`unless-stopped` / `always`) restart containers on failure, daemon restart, and host reboot, with exponential backoff (100 ms doubling, reset after 10 s healthy) ([oneuptime](https://oneuptime.com/blog/post/2026-02-08-how-to-use-docker-compose-restart-policy-options/view), [docker docs](https://docs.docker.com/engine/containers/start-containers-automatically/)). Use this for the IB Gateway container.
- **Do not stack both supervisors on the same thing.** Guidance is explicit: don't combine Docker's restart policies with a host process manager for the *same* process — it creates conflicts ([docker docs](https://docs.docker.com/engine/containers/start-containers-automatically/)). Pattern: either (a) everything in Compose, with one systemd unit that runs `docker compose up` so the *stack* survives reboot; or (b) gateway in Compose, app as a native systemd service. Pick one boundary.
- **supervisord** is only worth it *inside* a container that must keep multiple processes alive ([towardsdatascience](https://towardsdatascience.com/building-heavy-duty-containers-204354a67036/)) — e.g. the IB Gateway image already uses IBC/supervisor internally. You don't need supervisord at host level when systemd exists.

**IB Gateway daily-restart interaction (important):**
- Use the well-maintained **`gnzsnz/ib-gateway-docker`** image (Debian-based, IBC bundled, ARM-capable, VNC) — or the `hartza-capital/docker-ib-gateway` fork ([gnzsnz](https://github.com/gnzsnz/ib-gateway-docker), [hartza](https://github.com/hartza-capital/docker-ib-gateway)).
- Set **IBC `AutoRestartTime`** (e.g. `"02:00 AM"`) for the IBKR-mandated daily restart; configured this way it does **not** require daily 2FA ([gnzsnz discussion #145](https://github.com/gnzsnz/ib-gateway-docker/discussions/145), [ibkrguides](https://www.ibkrguides.com/traderworkstation/auto-restart-considerations.htm)).
- **Weekly caveat:** roughly once a week (IBKR invalidates security tokens Sunday ~01:00 ET) you must restart the container to re-enter credentials — i.e. there is an unavoidable **weekly manual auth touchpoint** for a fully unattended setup ([gnzsnz](https://github.com/gnzsnz/ib-gateway-docker)). Plan for it (Sunday-evening manual login or IBKR's "permanent" session options where eligible).
- **Your Python app must treat the gateway connection as ephemeral**: detect disconnects around the daily restart window and reconnect automatically with `ib_async` (community asyncio continuation of `ib_insync`) — the standard pattern, mirrored by IBridgePy's auto-reconnect ([interactivebrokers/IBridgePy](https://www.interactivebrokers.com/campus/ibkr-quant-news/ibridgepy-solves-the-issue-of-ib-daily-restart-or-daily-shutdown-by-automatically-reconnecting/)). Set the app's APScheduler scan window so you aren't depending on the socket during the gateway's `AutoRestartTime`.

**Pick:** systemd (`Restart=always`) supervises the app/stack; Compose `restart: unless-stopped` + IBC `AutoRestartTime` for IB Gateway; app reconnects via `ib_async`.

---

## 5. Deployment / Provisioning on the VPS (solo dev, one box)

| Option | Maturity | Footprint | ARM | Learning curve | Fit |
|---|---|---|---|---|---|
| **Docker Compose** | Mature | Docker runtime | Yes | Low | **Best primary choice** — one file, gateway + app, reproducible |
| **Bare systemd + venv** | OS-standard | Minimal | Native | Low | Viable, leanest; but you hand-manage IB Gateway/IBC install |
| **Ansible** | Mature | Control-node only (agentless/SSH) | Yes | Medium | **Optional add-on** for reproducible host setup |
| Terraform / Kubernetes | Mature | Heavy | Yes | High | **Overkill** for one box |

**Details & recommendation.**

- **Docker Compose is the pragmatic established choice for a handful of containers on a single host** — orchestration systems like Kubernetes add maintenance and onboarding cost that a small app won't recoup ([dev.to kuwv](https://dev.to/kuwv/why-i-use-ansible-over-docker-compose-edg)). One `compose.yaml` pins IB Gateway + your app, their network, restart policies, and env — reproducible and ARM-friendly (the gateway images publish arm64). This also cleanly contains the IB Gateway/IBC/Java install you'd otherwise babysit on bare metal.
- **systemd to own the stack across reboots:** wrap `docker compose up -d` in a systemd unit so the box self-heals after reboot ([bootvar](https://bootvar.com/systemd-service-for-docker-compose/)).
- **Ansible is the right *complement*, not a replacement:** agentless over SSH, ideal for templating config, encrypting secrets (Vault), installing Docker, and laying down the systemd unit — the common pattern is "Ansible configures the host + drops Compose files" ([medium/Jay Hardee](https://medium.com/swlh/deploying-docker-compose-applications-with-ansible-and-github-actions-7f1740392507), [techtarget](https://www.techtarget.com/searchsoftwarequality/tip/Compare-Ansible-vs-Docker-use-cases-and-combinations)). For a solo dev, start with Compose + a README of host steps; add a small Ansible playbook once you care about rebuilding the VPS from scratch reproducibly. **Terraform/K8s are unjustified** for a single static box.
- **Bare systemd + venv (no Docker)** is the leanest option and perfectly valid for the Python app itself; the friction is installing/maintaining IB Gateway + IBC + a headless display by hand. Docker offloads that, which is why Compose wins for *this* app specifically.

**Pick:** Docker Compose (gateway + app) launched/kept-alive by a systemd unit; add a lightweight Ansible playbook for reproducible host provisioning when ready.

---

## Recommended Stack (summary table)

| Concern | Choice | Why |
|---|---|---|
| In-process task model + dependencies | `asyncio.TaskGroup` (→ `anyio` if richer cancel/readiness needed) | Structured concurrency, zero/one dep, fits the live loop |
| Time-based scheduling | APScheduler **3.x** `AsyncIOScheduler` + `CronTrigger(tz="America/New_York")` | Runs in the existing loop; cron + DST; mature (avoid 4.0 pre-release) |
| Workflow orchestrator | **None** (watch-list: Temporal for durability, Prefect for EOD-batch UI) | DAG/distributed engines are overkill and mis-shaped for a real-time loop |
| Process supervision — app | `systemd` service, `Restart=always` | OS-standard, no extra deps, journald logging, reboot recovery |
| Process supervision — IB Gateway | `gnzsnz/ib-gateway-docker` + IBC `AutoRestartTime`, Compose `restart: unless-stopped`; app auto-reconnects via `ib_async` | Handles daily IBKR restart unattended (weekly manual auth caveat) |
| Deployment / provisioning | Docker Compose (gateway + app) under a systemd unit; optional Ansible for host setup | Pragmatic single-box standard; ARM-friendly; reproducible |

---

## Sources

- APScheduler — PyPI: https://pypi.org/project/APScheduler/
- APScheduler AsyncIOScheduler docs: https://apscheduler.readthedocs.io/en/3.x/modules/schedulers/asyncio.html
- APScheduler user guide: https://apscheduler.readthedocs.io/en/3.x/userguide.html
- APScheduler 4.0 progress tracking (issue #465): https://github.com/agronholm/apscheduler/issues/465
- APScheduler migration / 4.0 status: https://github.com/agronholm/apscheduler/blob/master/docs/migration.rst
- APScheduler vs schedule (Leapcell): https://leapcell.io/blog/scheduling-tasks-in-python-apscheduler-versus-schedule
- AnyIO — why use AnyIO over asyncio: https://anyio.readthedocs.io/en/stable/why.html
- Structured concurrency with AnyIO (Matt Westcott): https://mattwestcott.org/blog/structured-concurrency-in-python-with-anyio
- Structured concurrency in Python (Applifting): https://applifting.io/blog/python-structured-concurrency
- TaskGroup & Timeout in 3.11 (Data Leads Future): https://www.dataleadsfuture.com/why-taskgroup-and-timeout-are-so-crucial-in-python-3-11-asyncio/
- Managed Airflow vs Dagster vs Temporal (Astronomer): https://llms.astronomer.io/managed-airflow-vs-dagster-vs-temporal
- Airflow vs Prefect vs Dagster 2026 (DEV): https://dev.to/datastackx/airflow-vs-prefect-vs-dagster-picking-the-right-orchestrator-in-2026-1ifb
- Orchestration showdown Dagster/Prefect/Airflow (ZenML): https://www.zenml.io/blog/orchestration-showdown-dagster-vs-prefect-vs-airflow
- Temporal vs Airflow (ZenML): https://www.zenml.io/blog/temporal-vs-airflow
- Kestra vs Temporal vs Prefect 2025 (Procycons): https://procycons.com/en/blogs/workflow-orchestration-platforms-comparison-2025/
- Best Airflow alternatives 2026 (Orchestra): https://www.getorchestra.io/guides/best-apache-airflow-alternatives-in-2026-for-modern-data-teams
- How AnyIO powers Prefect: https://www.prefect.io/blog/oss-love-letters-how-anyio-powers-prefects-async-architecture
- Temporal self-hosted guide: https://docs.temporal.io/self-hosted-guide
- Temporal production-ready self-host (taigrr): https://blog.taigrr.com/blog/setting-up-a-production-ready-temporal-server/
- systemd service for Docker Compose (bootvar): https://bootvar.com/systemd-service-for-docker-compose/
- Docker Compose restart policy options (OneUptime): https://oneuptime.com/blog/post/2026-02-08-how-to-use-docker-compose-restart-policy-options/view
- Start containers automatically (Docker docs): https://docs.docker.com/engine/containers/start-containers-automatically/
- Building heavy-duty containers / supervisord (Towards Data Science): https://towardsdatascience.com/building-heavy-duty-containers-204354a67036/
- gnzsnz/ib-gateway-docker: https://github.com/gnzsnz/ib-gateway-docker
- gnzsnz restart period discussion #145: https://github.com/gnzsnz/ib-gateway-docker/discussions/145
- hartza-capital/docker-ib-gateway: https://github.com/hartza-capital/docker-ib-gateway
- IBKR auto-restart considerations: https://www.ibkrguides.com/traderworkstation/auto-restart-considerations.htm
- IBridgePy auto-reconnect after IB daily restart: https://www.interactivebrokers.com/campus/ibkr-quant-news/ibridgepy-solves-the-issue-of-ib-daily-restart-or-daily-shutdown-by-automatically-reconnecting/
- Ansible vs Docker Compose (StackShare): https://stackshare.io/stackups/ansible-vs-docker-compose
- Why I use Ansible over docker-compose (DEV): https://dev.to/kuwv/why-i-use-ansible-over-docker-compose-edg
- Deploying Docker Compose with Ansible (Medium): https://medium.com/swlh/deploying-docker-compose-applications-with-ansible-and-github-actions-7f1740392507
- Ansible vs Docker use cases (TechTarget): https://www.techtarget.com/searchsoftwarequality/tip/Compare-Ansible-vs-Docker-use-cases-and-combinations
