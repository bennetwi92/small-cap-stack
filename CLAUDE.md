# CLAUDE.md — working agreement for small-cap-stack

Automated systematic trading system for US small-cap momentum (Warrior-style), via IBKR.
**Read `research/strategy.md` for what the system does** (the canonical spec, generated from
`config.py`), `research/decisions.md` for why each rule is what it is, and
`research/findings-index.md` for the research record.

This file is the **rules**. The reasoning, incidents and post-mortems behind them live in
**[`research/how-we-work.md`](./research/how-we-work.md)** — cited below as *(why: §n)*. Read a
section of it when a rule looks arbitrary or you are about to argue with one.

## Cost posture (read this first)
The subscription is **Pro**, not Max. Context is a budget, not free.
- **Sonnet is the default model** and effort is **medium** (`.claude/settings.json`). Escalate with
  `/model opus` for genuine strategy judgement; drop back after.
- **One task per session, then `/clear`.** A long session hits auto-compaction, which re-processes
  everything — the most expensive thing that happens silently.
- ⚠️ **Never read these files whole. Grep them, or `sed -n` the range you need:**

  | file | cost if read whole |
  |---|---|
  | `research/decisions.md` | ~38,000 tokens — read the generated index table, then the one `§D-nn` |
  | `spikes/README.md` | ~14,600 tokens |
  | `tests/test_harvest.py` · `docs/portfolio.js` | ~25,000 / ~23,000 tokens |
  | `src/small_cap_stack/config.py` | ~13,000 tokens — grep the knob |

- **Iterate with `pytest tests/the_one_file.py`.** Run the full `make check` (1,280 tests + coverage)
  **once**, before pushing.
- **No multi-agent fan-out for a bounded question** — an inline `grep` beats a subagent that has to
  re-read the repo. Delegate only per the routing table below.

## The agent fleet — match the tier to the work
Nine subagents live in `.claude/agents/`. A cheap agent doing mechanical work costs a fraction of an
expensive one doing the same job, so **route deliberately**; the orchestrating session should stay on
Sonnet and hand work down or up.

| agent | tier | use it for |
|---|---|---|
| `scout` | haiku | "where is X", "which files touch Y", "does Z exist" — read-only lookup |
| `board-keeper` | haiku | issues, labels, Status/Size on board #3, findings comments |
| `ci-watcher` | haiku | watching a PR or run to completion, extracting the failure |
| `doc-generator` | haiku | `make strategy` / `make reports` / `make decisions` and commit the diff |
| `builder` | sonnet | a closed-form change in `src/` or `tests/` — the default worker |
| `frontend-dev` | sonnet | anything in `docs/` (the dashboard's HTML↔JS contract) |
| `spike-runner` | sonnet | running a `spikes/` harness and reporting the numbers |
| `report-author` | sonnet | turning supplied numbers into a dated published report |
| `strategy-analyst` | **opus** | should a rule change; what does a result mean — judgement only |

⚠️ **Don't send implementation to `strategy-analyst`** — decide there, build with `builder`. And
don't let `spike-runner` draw conclusions; measurement and interpretation are deliberately split.

## Project shape
- **Phases:** P1 = tracker only (no orders, 3 months data collection) · P2 = paper trading · P3 = live.
- **Strategy: `research/strategy.md` is the single source of truth — do not restate its numbers
  here or anywhere else.** Generated from `config.py` by `make strategy`;
  `tests/test_strategy_doc.py` fails when it drifts *(why: §1)*. What is worth knowing structurally:
  - **Three stages, each owning one question** (#567) — the scan asks *what do we see* (wide,
    deliberately), the engine asks *what would we select*, the book asks *how would we execute*.
    **The dividing line between engine and book is selection vs execution**: the price band and the
    trigger-time window are selection, so they live in the engine (`select_*`) beside the shape
    gates; the 2-a-day cap is capacity, so it stays in the book. Put a new rule wherever its question
    lives, and don't reach for `portfolio_` just because the book consumes it.
  - **`passed` ≠ `takeable`, deliberately.** `passed` = the bull flag is well-formed (shape gates
    only). `takeable` = *and it's one we'd select*. Selection rules go in `takeable`; never fold them
    into `passed` *(why: §3)*.
  - ⚠️ **Float and news are COLLECTED, never gated.** Enrichment written *after* a name is flagged;
    no gate and no selection rule reads them. The book really does hold high-float names. If that
    should change, the gate goes in the engine's selection tier *(why: §3)*.
  - **Entry splits in two (#182/#190):** a mechanical trigger above the last consolidation candle's
    high decides *when* the setup fires; R is measured against a separate, deliberately conservative
    fill. Stop = the consolidation low.
  - The live detector is the **full-day** `bullflag/day.py::detect_day` (compute-on-read over a whole
    day, gated by scanner-appearance time + staleness, with exhaustion on late cycles) — consumed by
    `rmetrics.py` and `charts.py`. The superseded anchored detector was deleted in #296. Read
    `research/bull-flag.md` (the *what*) and `research/engine-v2.md` (the *how*).
  - **`config.py` is the single source of truth for the values** (#302), and
    `detect_day_with_settings` is the only path that reads them — a knob wired anywhere else does
    nothing. `detect_day`'s own defaults are a deliberate **shape-only, rule-OFF** baseline, *not* a
    copy of the shipped values (8 of 21 differ on purpose) — never "deduplicate" them *(why: §2)*.
    **After changing a rule, run `make strategy`** or CI fails on the stale spec.
- **Core principle:** *store raw, compute derived on read* — capture raw data at flag time; gate/stat
  logic is replayable pure functions so methodology can change retroactively.
- **Parquet-store cost model: read cost tracks FILE count, not row count or bytes on disk.** Keep
  hot-path reads `dt=`-scoped and watch the `files` counts in `status.json` / `scs_dataset_files`
  *(why: §4)*.

## Branching & PRs (trunk-based)
- `main` is protected: **all changes go through a PR**; no direct pushes. Required check:
  `lint-typecheck-test`. Linear history (squash-merge), no force-push. Solo self-merge is allowed.
- Branch names: `feat/…`, `fix/…`, `chore/…`, `spike/…`, `docs/…`. Same conventional prefixes on
  commit and PR titles.
- Link issues in the PR body: `Closes #N` when the PR completes the issue, else `Refs #N`; always
  reference the epic (`Refs #1`) for Phase-1 work.
- End commit messages with the `Co-Authored-By:` trailer for Claude.
- Squash-merge and delete the branch after merge ("Automatically delete head branches" is on).
- ⚠️ **Four branches are long-lived and must never be pruned as stale.** They are data, not work:

  | branch | carries | written by |
  |---|---|---|
  | `main` | the code | PRs |
  | `dashboard-data` | the published payloads | `publish-dashboard`, force-pushed every 15 min |
  | `review-data` | **the trader's saved reviews + chart annotations** — ~167 hand-made JSON files, the ground truth behind the 25 golden fixtures | `docs/review.js`, from the browser |
  | `data-export` | on-demand `/data` slices for a cloud session | `data-export.yml` |

  `review-data` is **irreplaceable** and looks exactly like a stale full-tree copy. Never delete it
  *(why: §5)*.

## CI / quality gates (run locally before pushing)
Toolchain lives in `.venv`. **`ci` is the only check a PR gets** — don't add a PR-triggered job
unless it is worth making required *(why: §6)*.
```bash
.venv/bin/ruff check .          # lint
.venv/bin/ruff format --check . # format
.venv/bin/mypy                  # type-check (strict; package only)
.venv/bin/pytest                # tests (bare — no coverage)
make cov                        # tests + the coverage gate CI enforces on main
```
- Python **3.11**. mypy is `--strict` and only checks `src/small_cap_stack` (so `spikes/` is exempt).
- Trading logic (gates, sizing, stats) must be exhaustively unit-tested — it is the product.
- **Coverage is gated on `main`, not on PRs** (90%, line *and* branch). **The coverage flags live on
  the CI line and in `make cov`, never in pytest's `addopts`** *(why: §6)*.
- `filterwarnings = ["error"]`: a dep's deprecation fails the build on purpose. Add a targeted
  `ignore` for that message — don't remove the gate.

## Throughput & estimation
Size tiers, also the board's **Size** field: **XS** ≤50 lines · **S** 50–250 · **M** 250–850 ·
**L** 850–1300. Roughly 5–10 / 10–15 / 20–30 / 30–45 min each; median PR ≈ 110 lines / ~4 files.
**Ten XS PRs cost more than one M PR of the same diff** — batch trivia. Box, IBKR and spike work
isn't estimable from these. Anchors and caveats: [`research/throughput.md`](./research/throughput.md).

## Issue & project hygiene (keep these current — every task)
Delegate this to **`board-keeper`**; it holds the procedure.
- **Every unit of work is a GitHub issue** with labels: `epic`, `phase-1`, `spike`, `infra`, `setup`,
  `ibkr`, `data`, `strategy`, `bug`. Epic is **#1**.
- **Project board:** `https://github.com/users/bennetwi92/projects/3`.
  `scripts/board.sh <issue#> <Backlog|Todo|"In Progress"|Blocked|Done>` sets Status; the same script
  takes `<XS|S|M|L>` to set Size. Use it instead of re-deriving `gh project item-edit` calls.
  New issue: `gh issue create`, then `gh project item-add 3 --owner bennetwi92 --url <url>`, then Status.
- **Status lifecycle:** Backlog (real, not next) → Todo (next up) → In Progress → Done.
  **Blocked** is for waiting on the world — a calendar, a purchase, another issue — *not* for
  "haven't got to it", which is Backlog. If In Progress hasn't moved this week, it isn't.
- **Record findings on the issue**, not just in chat (`gh issue comment N`).
- When a decision is made, update `research/decisions.md` (and memory): stable `D-nn` ID, a
  **`**Status:**` line** under the heading (`LIVE`/`SUPERSEDED`/`REVERSED`), then `make decisions`.
  Cite as `§D-nn`, never by line number.

## Spikes (de-risking experiments)
Run them with **`spike-runner`**; interpret with `strategy-analyst`.
- Throwaway harnesses live in `spikes/`, documented in `spikes/README.md`; exempt from mypy/tests but
  ruff-linted. Each spike maps to an issue; record the go/no-go as an issue comment.
- Outputs go to `data/spikes/`, which is **gitignored** — never commit data.
- ⚠️ **No lookahead:** a selection rule must be decidable at trigger time; "first two that pass" is
  deliberate. ⚠️ **Never report stats on opportunities that could not have been traded**, not even as
  a contrast or a "lookahead delta".

## IBKR / runtime
- Library: **`ib_async`** (asyncio). Ports: TWS paper 7497 / live 7496 · IB Gateway paper 4002 / live
  4001. In the compose stack the app reaches Gateway via **socat** (paper **4004** / live **4003**) —
  the raw ports bind localhost-only with `TrustedIPs=127.0.0.1`, so a cross-container client on them
  connects then gets dropped. Set `IBKR_PORT` to the socat port.
- `reqHistoricalData` uses `barSizeSetting=` (not `barSize`). Short-term volume is native:
  `stVolume5minAbove` etc. — do not derive 5-min volume from bars.
- Pacing: ≤50 scanner rows, ~50 msg/sec, historical < 60 req / 10 min. Always `outsideRth=True` for
  pre-market.
- Secrets via `.env` (gitignored); see `.env.example`. Never commit credentials.

## Repo layout
- `src/small_cap_stack/` — the package (typed, tested). `tests/` — pytest suite (incl.
  `fixtures/review_cases/`, 25 real-market regression cases). `spikes/` — experiments.
  `scripts/` — repo helpers. `deploy/` — host runbook + systemd units.
- `research/` — the **documentation root**: `strategy.md` (the spec — the state) + `decisions.md`
  (the log) + `findings-index.md` (the research record) + `how-we-work.md` (the reasoning behind this
  file) + the grammar specs (`bull-flag.md` = *what*, `engine-v2.md` = *how*) and standing reports.
  `research/archive/` holds one-off reports that already did their job.
- ⚠️ **`docs/` is NOT documentation** — it is the GitHub Pages dashboard frontend. Docs live in
  `research/`; only root keeps `README`/`CLAUDE`/`CONTRIBUTING`/`DISCLAIMER` (#300). Renaming `docs/`
  means changing `pages.yml`'s `path:` and every reference *(why: §7)*.
  - ⚠️ **Dashboard pages carry numbers, statuses and instructional text — never commentary (#414).**
    A label, a unit, a legend, a tooltip saying what a metric *is*: yes. Anything that argues,
    justifies or interprets goes in a **report**, where it is dated and can be superseded *(why: §7)*.
  - **`docs/plan.html` / `plan.js`** computes every value at render time; the committed
    `PHASES`/`GATES` constants are **labels only** and mirror
    [`research/phase-2-roadmap.md`](./research/phase-2-roadmap.md) — change both in the same PR.
- `data/` — local runtime data (gitignored). ⚠️ **`data/recon/` is a separate store root** (#430) of
  pre-market days rebuilt from vendor bars; only `build_portfolio_payload(recon_store=…)` opts in and
  every trade carries `source: "live" | "recon"`. Producer: `src/small_cap_stack/harvest/`, run
  nightly by `scs-harvest.timer`. *(why: §8; ops: `deploy/RUNBOOK.md` §13 — `harvest daily` must
  precede `harvest run`.)*

## The dashboard frontend (`docs/`) — the HTML↔JS contract
Hand this to **`frontend-dev`**, which carries the full contract. The short form:
- **Touch both halves in the same PR** — remove or rename an element and you remove or rename every
  lookup of it. `tests/test_dashboard_dom.py` fails when a module reaches for an id its page can't
  produce (#406).
- **`el()` lives in `docs/js/dom.js`** — import it, never re-fork `document.getElementById`.
- ⚠️ **Assets are cached 10 minutes and unversioned**, so a post-deploy `Cannot set properties of
  null` is usually stale JS against fresh HTML — **not** a box or data outage. A self-consistent PR
  does not prevent it *(why: §7)*.
- **There is no browser coverage in CI** — a broken page ships green. Smoke-load index / review /
  results / portfolio / reports after a DOM change.

## Reports (published analyses)
Ask for an analysis and it gets published to the dashboard's **Reports** tab — the `publish-report`
skill is the procedure, **`report-author`** the agent. **Reports are where all commentary lives**
(#414).
- Markdown in **`docs/reports/<published>-<slug>.md`** with front matter (`title`, `published`
  required; `summary`, `tags`, `author`, `correction` optional), parsed by `reports.py` into
  `docs/reports/index.json`. **Run `make reports` after adding or editing one** — a report missing
  from the index is invisible. `tests/test_reports.py` fails on a stale index.
- ⚠️ **A report is dated and is never silently rewritten. When one is overtaken or rests on a premise
  that turned out wrong, add a `correction:` line** — one sentence, dated. Editing the analysis
  instead destroys the record of what was believed when a decision was taken *(why: §1)*.
- ⚠️ **`docs/.nojekyll` must stay** — a Jekyll pass would serve no raw `.md` and 404 every report.
  `tests/test_reports.py` fails if it's deleted.
- ⚠️ **Reports live in the repo, never on the box** — `dashboard-data` is force-pushed fresh every 15
  minutes, so anything hand-written there is destroyed on the next cycle.

## Box access — YOU HAVE IT from the Mac (do not claim otherwise)
On the **Mac** you can operate the live box directly — don't say "I have no box access":
- **Trigger workflows:** `gh workflow run <name>.yml --field k=v` (they run on the self-hosted `vps`
  runner). Deploy: `gh workflow run deploy.yml --field ref=main`.
- **SSH:** `ssh -i ~/.ssh/oracle_scs root@138.199.151.179` (repo `/opt/small-cap-stack`; container
  `small-cap-stack-app-1`; unit `small-cap-stack`). Details in `deploy/host.local.md` (gitignored).
  ICMP is firewalled so `ping` always fails — normal, not a symptom.
- ⚠️ **The box is a Hetzner CX23 (2 vCPU / 4 GB) and a heavy job takes it down hard.** Four rules,
  all learned the expensive way *(why: §9)*:
  - **NEVER `backfill-dashboard --all`.** Recompute **per date**, one at a time, via
    `scripts/box-job.sh` — **never `docker exec` into the app**, which OOMs the tracker (#545).
  - **Per-date is not automatically safe either** — prefer a **past** date, one at a time, watch `free -m`.
  - **Never `systemctl restart` the runner while a job is in flight.**
  - **After any OOM, confirm the runner came back** — a `failed` runner makes CI queue forever.
  - Recovery + the `hcloud` out-of-band CLI: `deploy/RUNBOOK.md` §9/§9.1.

## How work gets done (#377, #489)
Work happens by **you driving Claude Code**. **There is no automation layer** — no `/spec` gate, no
auto-triage, no watchdog, no agent that opens issues on its own *(why: §10)*. Nothing gates you.
- **Delegation (#489):** labelling an issue `agent` dispatches `claude.yml` — a Claude agent on a
  **hosted** runner (never the VPS) builds it and opens a PR; `@claude …` revises it; a human reviews
  and squash-merges. **Delegate only when all four hold:** `make check` is a sufficient verdict (the
  runner has no `.env`, IBKR, box or `/data`) · the brief is closed-form, because the agent can't ask
  a question mid-flight · XS/S tier · it isn't what you're actively iterating on. Procedure: the
  `delegate-issue` skill.
  ⚠️ **On Pro, a delegated run draws on the same quota as this session** and re-reads the repo from
  scratch — it buys wall-clock parallelism, not capacity. Use it only when you genuinely have
  something else to do meanwhile. ⚠️ Before adding a *second* agent workflow, read
  [`research/archive/github-automation.md`](./research/archive/github-automation.md).
- The other workflows are hands-off and human-triggered: `ci` (every PR), `deploy`, `build-image`,
  `publish-dashboard` (scheduled), `backfill-dashboard`, `deploy-backfill-publish`, `data-export`.
- **Liveness monitoring** is the app's own Healthchecks.io dead-man's switch — the signal to trust.
- ⚠️ **Keep the repo public.** Public = unlimited hosted Actions minutes, and CI plus 96
  `publish-dashboard` runs a day are metered the moment it goes private *(why: §11)*.

## Working remotely (Claude Code on mobile / web)
The cloud environment has GitHub access (issues, PRs, board, CI) and can run `make setup`/`make
check`, but has **no** `.venv`, `gh` keyring token, `.env`, or **live IBKR connection**.
- ✅ Safe remotely: code, tests, docs, issues, PRs, reviewing CI.
- ❌ Not possible remotely: `spikes/` or the trading app — anything needing IB Gateway runs on the
  **Mac or the VPS**.
- 📊 **Reading box data from the cloud:** you **cannot** SSH from a web session (no port-22 egress, no
  secret store). Use the on-demand **`data-export`** workflow via the **`box-data`** skill. On the
  **Mac**, use the direct `docker exec` recipe (`review-analysis` skill).
- **Secrets** live in three places, never in git: `.env` (local), Actions secrets (CI), the VPS
  environment (runtime). The cloud reads data through GitHub, so it needs no secret.

## Quick commands
`make help` lists everything. `make setup` · `make check` (all CI gates) · `make lint` / `fmt` /
`typecheck` / `test`. Run `make check` before every push.
Three generators keep committed files honest — **`make strategy`** after changing a rule in
`config.py`, **`make reports`** after adding or editing a report, **`make decisions`** after amending
a decision. Each has a test that fails on a stale artefact. Agent: **`doc-generator`**.
