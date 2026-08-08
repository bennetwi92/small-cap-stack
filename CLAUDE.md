# CLAUDE.md — working agreement for small-cap-stack

Automated systematic trading system for US small-cap momentum (Warrior-style), via IBKR.
**Read `research/strategy.md` for what the system does** (the canonical spec, generated from
`config.py`), `research/decisions.md` for why each rule is what it is, and
`research/findings-index.md` for the research record. This file documents **how we work** — follow
it on every task.

## Project shape
- **Phases:** P1 = tracker only (no orders, 3 months data collection) · P2 = paper trading · P3 = live.
- **Strategy: `research/strategy.md` is the single source of truth — do not restate its numbers
  here or anywhere else.** It is generated from `config.py` by `make strategy`, and
  `tests/test_strategy_doc.py` fails when it drifts. Seven surfaces used to state the rules and
  they disagreed on four price bands (#551); the fix only holds if new prose links instead of
  copying. What is worth knowing structurally:
  - **Three stages, each owning one question** (#567) — the scan asks *what do we see* (wide,
    deliberately), the engine asks *what would we select*, the book asks *how would we execute*.
    **The dividing line between engine and book is selection vs execution**: the price band and the
    trigger-time window are selection, so they live in the engine (`select_*`) beside the shape
    gates; the 2-a-day cap is capacity, so it stays in the book. Put a new rule wherever its
    question lives, and don't reach for `portfolio_` just because the book consumes it.
  - **`passed` ≠ `takeable`, deliberately.** `passed` = the bull flag is well-formed (shape gates
    only — what the review workbench and the 25 golden fixtures are written against). `takeable` =
    *and it's one we'd select*. A $1.50 name or an 11:00 break stays visible and scoreable rather
    than being reported as malformed, which is what a data-collection phase needs. Selection rules
    go in `takeable`; never fold them into `passed`.
  - ⚠️ **Float and news are COLLECTED, never gated.** They are enrichment written *after* a name is
    flagged; neither the shape gates nor the selection rules read them, and `gates.py::float_gate` /
    `news_gate` feed EOD *counts* only. So the book really does take high-float names — it holds
    CLSK (246M) and XRX (119M). If that should change, the gate goes in the engine's selection tier;
    `tests/test_portfolio_extract.py` pins it today, and the float test's own failure message says to delete
    it if you meant it. Evidence: `docs/reports/2026-07-31-float-vs-max-r.md`.
  - **Entry splits in two (#182/#190):** a mechanical trigger above the last consolidation candle's
    high decides *when* the setup fires; R is measured against a separate, deliberately
    conservative fill. Stop = the consolidation low.
  - The live detector is the **full-day** `bullflag/day.py::detect_day` (compute-on-read over a
    whole day, gated by scanner-appearance time + staleness, with exhaustion on late cycles) —
    consumed by `rmetrics.py` and `charts.py`. The superseded anchored detector was deleted in #296.
    Read `research/bull-flag.md` (the *what*) and `research/engine-v2.md` (the *how*) for the
    grammar behind the rules.
  - **`config.py` is the single source of truth for the values** (#302), and
    `detect_day_with_settings` is the only path that reads them — a new knob wired anywhere else
    does nothing. `detect_day`'s own defaults are a deliberate **shape-only, rule-OFF** baseline for
    tests and spikes, *not* a copy of the shipped values (8 of 21 differ on purpose), so a
    parameter the wrapper forgets silently runs with the rule switched off.
    `tests/test_settings_wiring.py` derives that requirement from the signature, so it covers a
    knob added tomorrow (#525). **After changing a rule, run `make strategy`** or CI fails on the
    stale spec.
- **Core principle:** *store raw, compute derived on read* — capture raw data at flag time; gate/stat logic is replayable pure functions so methodology can change retroactively.
- **Parquet-store cost model:** for this store, **read cost tracks FILE count, not row count or
  bytes on disk** — every read/query opens each file's footer, so 32k one-row files read ~40×
  slower than the same rows in a few hundred files (#318/#319/#321; three PRs missed a 36s/60s
  tick regression by sizing reads in rows/GB). Keep hot-path reads `dt=`-scoped and watch the
  `files` counts in `status.json` / `scs_dataset_files`.

## Branching & PRs (trunk-based)
- `main` is protected: **all changes go through a PR**; no direct pushes. Required check: `lint-typecheck-test`. Linear history (squash-merge), no force-push. Solo self-merge is allowed (0 approvals required).
- Branch names: `feat/…`, `fix/…`, `chore/…`, `spike/…`, `docs/…`.
- Commit/PR titles: conventional prefixes (`feat:`, `fix:`, `chore:`, `spike:`, `docs:`).
- Link issues in the PR body: `Closes #N` when the PR completes the issue, else `Refs #N`; always reference the epic (`Refs #1`) for Phase-1 work.
- End commit messages with the `Co-Authored-By:` trailer for Claude.
- Squash-merge and delete the branch after merge. "Automatically delete head branches" is **on**, so
  this looks after itself for anything merged through a PR.
- ⚠️ **Four branches are long-lived and must never be pruned as stale.** They are data, not work:

  | branch | carries | written by |
  |---|---|---|
  | `main` | the code | PRs |
  | `dashboard-data` | the published payloads | `publish-dashboard`, force-pushed fresh every 15 min |
  | `review-data` | **the trader's saved reviews + chart annotations** — ~167 hand-made JSON files, the ground truth the 25 golden fixtures came from | `docs/review.js`, straight from the browser |
  | `data-export` | on-demand `/data` slices for a cloud session | `data-export.yml` (recreates it; may not exist between runs) |

  `review-data` is the one to be careful with: it is **irreplaceable** — hand-made, not regenerable
  from anything — and nothing in this file named it until #522, which is exactly why an audit
  proposed deleting it as "a stale full-tree copy". It *looks* like one, because `review.js` creates
  it off `main`'s HEAD on first save and then commits `reviews/*.json` on top.

## CI / quality gates (run locally before pushing)
Toolchain lives in `.venv`. CI runs ruff + mypy + pytest on every PR.
- **`ci` is the only check a PR gets.** `lint-typecheck-test` is the single required context; nothing
  else runs on `pull_request`. CodeQL default setup (GitHub-side, no workflow file) and
  `build-image`'s PR trigger were both removed — a one-person repo gained nothing from two extra
  non-required jobs except more red Xs to interpret, and a GitHub Actions outage that fails them
  makes a healthy PR look broken. Don't re-add a PR-triggered job unless it is worth making required.
```bash
.venv/bin/ruff check .          # lint
.venv/bin/ruff format --check . # format
.venv/bin/mypy                  # type-check (strict; package only)
.venv/bin/pytest                # tests (bare — no coverage)
make cov                        # tests + the coverage gate CI enforces on main
```
- Python **3.11**. mypy is `--strict` and only checks `src/small_cap_stack` (so `spikes/` is exempt).
- Trading logic (gates, sizing, stats) must be exhaustively unit-tested — it is the product.
- **Coverage is gated on `main`, not on PRs (#494/#495).** The PR run is the merge gate and runs the
  suite bare; the push-to-main run is the covered one and enforces `--cov-fail-under=90` over
  **line *and* branch** coverage (#530 — line alone called `portfolio/extract.py` 92% while its
  branch figure was 80%). `make check` runs `make cov`, so a PR that would drop `main` below the
  bar is visible before you push — the split is about CI cost, not about relaxing the bar.
- **The coverage flags live on the CI line and in `make cov`, never in pytest's `addopts` (#530).**
  In addopts, `pytest tests/one_file.py` exits 1 with "Required test coverage of 80% not reached.
  Total coverage: 4.77%" while every test passes — which teaches you to read past a red pytest.
- `filterwarnings = ["error"]`: a deprecation from polars/duckdb is a change to how this system
  computes money, so it fails the build on the PR that bumps the dep. If a floating dep trips it
  on an unrelated PR, add a targeted `ignore` for that message — don't remove the gate.

## Throughput & estimation
Size tiers, also the board's **Size** field: **XS** ≤50 lines · **S** 50–250 · **M** 250–850 ·
**L** 850–1300. Roughly 5–10 / 10–15 / 20–30 / 30–45 min each; median PR ≈ 110 lines / ~4 files.
**Ten XS PRs cost more than one M PR of the same diff** (fixed per-PR overhead) — batch trivia. Box,
IBKR and spike work isn't estimable from these at all. Anchors, sample and caveats:
**[`research/throughput.md`](./research/throughput.md)**.

## Issue & project hygiene (keep these current — every task)
- **Every unit of work is a GitHub issue** with labels: `epic`, `phase-1`, `spike`, `infra`, `setup`, `ibkr`, `data`, `strategy`, `bug`. Epic is **#1**.
- **Project board:** `https://github.com/users/bennetwi92/projects/3` (project id `PVT_kwHOCGbB5M4Bb_HY`).
  - **Status** field `PVTSSF_lAHOCGbB5M4Bb_HYzhWrRtM` — Backlog `9544b6ad` / Todo `f75ad846` /
    In Progress `47fc9ee4` / Blocked `ab0407fa` / Done `98236657`.
  - **Size** field `PVTSSF_lAHOCGbB5M4Bb_HYzhZ7oxU` — XS `2c5c01af` / S `dbe01fd8` / M `07ea1ac7` /
    L `69a53ac5`. These are the **estimation tiers** from "Throughput & estimation" above; set one on
    every issue so a slice of the board can be costed and the anchors can be checked against reality.
  - When creating an issue: `gh issue create` then add it to the board (`gh project item-add 3 --owner bennetwi92 --url <issue-url>`) and set Status.
  - **Status lifecycle:** Backlog (real, but not next) → Todo (next up, ready to start) →
    In Progress → Done (when its PR merges / issue closes). **Blocked** is for waiting on the world —
    a calendar, a purchase, another issue — *not* for "haven't got to it", which is Backlog.
    Keep In Progress honest: if it hasn't moved this week it belongs in Todo or Backlog.
  - Set a field: `gh project item-edit --project-id <PROJ_ID> --id <itemId> --field-id <FIELD_ID> --single-select-option-id <optId>`.
- **Record findings on the issue**, not just in chat — spikes/experiments get a results comment on their issue (`gh issue comment N`).
- When a decision is made, update `research/decisions.md` (and memory). Entries carry a stable
  `D-nn` ID and a **`**Status:**` line** under the heading (`LIVE` / `SUPERSEDED` / `REVERSED`);
  amending an older entry means updating *its* status line too, then `make decisions` to rebuild the
  index. Cite a decision as `§D-nn`, never by line number.

## Spikes (de-risking experiments)
- Throwaway harnesses live in `spikes/`; documented in `spikes/README.md`; exempt from mypy/tests but ruff-linted.
- Outputs (CSV/JSON/XML) go to `data/spikes/` which is **gitignored** — never commit data.
- Each spike maps to an issue; record the go/no-go + findings as an issue comment.

## IBKR / runtime
- Library: **`ib_async`** (asyncio). Ports: TWS paper 7497 / live 7496 · IB Gateway paper 4002 / live 4001.
  In the docker-compose stack the app connects to the `gnzsnz/ib-gateway` container via **socat** (paper
  **4004** / live **4003**) — the raw 4002/4001 API binds localhost-only with `TrustedIPs=127.0.0.1`, so a
  cross-container client on those ports connects then gets dropped. Set `IBKR_PORT` to the socat port.
- `reqHistoricalData` uses `barSizeSetting=` (not `barSize`). Short-term volume is native: `stVolume5minAbove` etc. — do not derive 5-min volume from bars.
- Pacing: ≤50 scanner rows, ~50 msg/sec, historical < 60 req / 10 min. Always `outsideRth=True` for pre-market.
- Secrets via `.env` (gitignored); see `.env.example`. Never commit credentials.

## Repo layout
- `src/small_cap_stack/` — the package (typed, tested).
- `tests/` — pytest suite (incl. `fixtures/review_cases/` — 25 real-market regression cases).
- `spikes/` — de-risking experiments (see `spikes/README.md`).
- `research/` — the **documentation root**: `strategy.md` (**the canonical spec — the state**,
  generated from `config.py`) + `decisions.md` (**the log** — why each rule is what it is, and when
  it changed) + `findings-index.md` (the research record) + the grammar specs (`bull-flag.md` = the
  *what*, `engine-v2.md` = the *how*) and the standing reports. `research/archive/` holds one-off
  reports that already did their job (the 2026-06-29 `arch-*` set and `strategy-validation.md`) —
  kept as the record, not as live docs.
- ⚠️ **`docs/` is NOT documentation** — it is the **GitHub Pages dashboard frontend** (HTML/CSS/JS;
  `cockpit.css` + `docs/js/` modules). Pages is published by our own
  **`.github/workflows/pages.yml`** (`build_type: workflow`, #486) — the legacy build's managed
  workflow aborted the deployment after 10 minutes, which GitHub's publish step started routinely
  overrunning. Renaming `docs/` is therefore no longer forbidden by Pages (the workflow uploads
  whatever path it is given), but it *is* still a rename of the `path:` in that workflow and of
  every reference below — don't do it casually. Docs live in `research/`; only root keeps
  `README`/`CLAUDE`/`CONTRIBUTING`/`DISCLAIMER` (#300).
  `docs/reports/` is the one exception that *is* prose: published reports (see below).
  - ⚠️ **Dashboard pages carry numbers, statuses and instructional text — never commentary
    (#414).** A label, a unit, a legend, a tooltip that says what a metric *is*, a line that says
    what a control does: yes. Anything that argues, justifies or interprets — why a rule exists,
    what a result means, what to conclude — goes in a **report**, where it is dated and can be
    superseded. Prose on a page has to be re-read and re-approved every time the data moves;
    that is how the pre-#414 Plan page and Projection view went stale. If a panel can't be
    rendered from the published data, ask whether it belongs on a page at all.
  - **`docs/plan.html` / `plan.js` is the plan board (#410, rebuilt #414).** Every value is computed
    at render time from the published payloads, and each gate's *status* is derived from GitHub issue
    state — so it looks after itself. The committed `PHASES` / `GATES` constants are **labels only**
    and mirror [`research/phase-2-roadmap.md`](./research/phase-2-roadmap.md): change a gate's name,
    issues or dependencies **there and here in the same PR**.
- `data/` — local runtime data (gitignored). ⚠️ **`data/recon/` is a separate store root** (#430) of
  pre-market days rebuilt from vendor bars, kept apart so nothing reading `data/` can return vendor
  rows by accident: only `build_portfolio_payload(recon_store=…)` opts in, and its charts publish to
  their own `recon_index.json` namespace (#488). Every trade carries `source: "live" | "recon"`.
  Producer is `src/small_cap_stack/harvest/` (#431), run nightly by `scs-harvest.timer`.
  **Why it is shaped this way: `decisions.md` §D-30/§D-31/§D-33. How to run and debug it:
  `deploy/RUNBOOK.md` §13** — including the run window, the memory cap, and that `harvest daily`
  must precede `harvest run`.
- `scripts/` — repo helpers (e.g. `board.sh`).
- `deploy/` — host runbook + systemd units.

## The dashboard frontend (`docs/`) — the HTML↔JS contract
Each page is static HTML plus one ES module that reaches into it **by id** (`el("stats")`), with no
build step and no framework. Nothing links the two halves at build time, so the contract is a rule:
- **Touch both halves in the same PR.** Remove or rename an element and you remove or rename every
  lookup of it — and vice versa. `tests/test_dashboard_dom.py` fails when a page's module graph
  reaches for an id that page can't produce (static markup, injected markup, or an options-bar
  field), so the permanent form of this mistake can't merge (#406).
- **`el()` lives in `docs/js/dom.js`** — import it, never re-fork `document.getElementById`. It
  throws a `MissingElementError` naming the id instead of returning null, and page error banners go
  through its `showError`/`setBanner` so a front-end mismatch stops being reported as a data-feed
  failure. The test enforces the no-fork rule too.
- ⚠️ **Assets are cached for 10 minutes and are not versioned.** Pages serves HTML and JS with
  `max-age=600`; a browser reload revalidates the navigation HTML while still serving the *script*
  from cache. So for up to ~10 min after a deploy that changed the markup, a returning visitor can
  run the **previous** JS against the **current** HTML — the page then dies on an element the new
  markup doesn't have. **A self-consistent PR does not prevent this** (#403 removed `#charts-card`
  from both halves and still produced `Cannot set properties of null (setting 'hidden')` in the
  wild). It self-heals when the cache expires; a hard reload (Cmd/Ctrl-Shift-R) fixes it now, and
  the banner says so. **Don't chase a post-deploy null-property error on a dashboard page as a box
  or data outage** — check whether the markup changed in the last deploy first.
- **There is no browser coverage in CI.** `make check` type-checks and tests Python only; nothing
  loads a page. Frontend changes that alter the DOM want a manual smoke-load of every page —
  index / review / results / portfolio / reports — because a broken one still ships green.

## Quick commands
`make help` lists everything. Common ones: `make setup` (venv + deps), `make check` (all CI gates), `make lint` / `make fmt` / `make typecheck` / `make test`. Run `make check` before every push.
Three generators keep committed files honest — **`make strategy`** after changing a rule in
`config.py`, **`make reports`** after adding or editing a report, **`make decisions`** after adding
or amending an entry in `research/decisions.md`. Each has a test that fails on a stale artefact, so
forgetting costs a red CI rather than a wrong doc.

## Reports (published analyses)
Ask for an analysis — *"write me a report on how often a wide stop costs us the trade"* — and it gets
published to the dashboard's **Reports** tab. The **`publish-report`** skill is the procedure.
**Reports are also where all commentary lives** (#414): the dashboard's other pages are status
boards, so any writing that explains, justifies or concludes belongs here rather than in a panel.
- A report is markdown in **`docs/reports/<published>-<slug>.md`** with front matter (`title`,
  `published` required; `summary`, `tags`, `author`, `correction` optional).
  `src/small_cap_stack/reports.py` parses it into **`docs/reports/index.json`**, which
  `docs/reports.js` renders as the list (newest first) and then fetches the markdown on click
  (`?r=<slug>` deep-links a report).
- ⚠️ **A report is dated and is never silently rewritten. When one is overtaken or rests on a
  premise that turned out wrong, add a `correction:` line** — one sentence, dated, rendered as a
  gold banner on the list row and above the body. Editing the analysis instead destroys the record
  of what was believed when a decision was taken. Before #551 nothing marked a report stale, so
  two of them went on asserting a float gate that had never run.
- **Run `make reports` after adding or editing one** — a report missing from `index.json` is
  invisible to the page. `tests/test_reports.py` fails when the committed index is stale.
- ⚠️ **`docs/.nojekyll` must stay.** A Jekyll pass over `docs/` turns each front-matter-carrying
  `docs/reports/*.md` into `reports/<slug>.html` and serves **no** raw `.md` — so `reports.js`,
  which fetches the markdown itself, 404s on every report while the list still loads (`index.json`
  is a static asset). The workflow-based deploy (#486) uploads the directory as-is and never runs
  Jekyll, so this is belt-and-braces now rather than the only thing standing between the Reports
  tab and a blank page — but it costs nothing and it is what makes the directory safe to serve from
  anywhere. `tests/test_reports.py` fails if it's deleted.
- ⚠️ **Reports live in the repo, never on the box.** `publish-dashboard` force-pushes a fresh single
  commit to `dashboard-data` every 15 min, so anything hand-written on that branch is destroyed on
  the next cycle. `docs/` is already the Pages source: a merged report is a served report.

## Helper scripts
- `scripts/board.sh <issue#> <Backlog|Todo|"In Progress"|Blocked|Done>` — set an issue's Status on
  project board #3. The same script takes a size tier instead — `scripts/board.sh <issue#> <XS|S|M|L>`
  — to set Size; the two value spaces don't collide. It encapsulates the project/field IDs, so use it
  instead of re-deriving `gh project item-edit` calls.

## Box access — YOU HAVE IT from the Mac (do not claim otherwise)
When running on the **Mac** (the primary working dir, not a cloud/web session), you can operate the live box directly — don't tell the user "I have no box access":
- **Trigger GitHub Actions** (deploy, backfill, data-export, publish-dashboard) with `gh workflow run <name>.yml --field k=v`; they run on the self-hosted `vps` runner. Deploy: `gh workflow run deploy.yml --field ref=main`.
- **SSH into the box**: `ssh -i ~/.ssh/oracle_scs root@138.199.151.179` (root; repo `/opt/small-cap-stack`; app container `small-cap-stack-app-1`; systemd unit `small-cap-stack`). Full details in **`deploy/host.local.md`** (gitignored). ICMP is firewalled so `ping` always fails — that's normal, not a symptom.
- ⚠️ **The box is small (Hetzner CX23: 2 vCPU / 4 GB) and a heavy job takes it down hard** — sshd
  stops answering, the runner drops **offline (busy)**, and you can then neither cancel nor SSH in.
  Four rules, all learned the expensive way (#264 cost 5h37m of CI):
  - **NEVER `backfill-dashboard --all`.** Recompute **per date** (`--field date=YYYY-MM-DD`, one at
    a time), or `scripts/box-job.sh backfill -- -m small_cap_stack.dashboard_backfill --date <d>` —
    its own 1 GB container, **never `docker exec` into the app**, which spends the tracker's cgroup
    and OOMs the tracker instead of the job (#545). `--all` needs `--force`, and the workflows need
    a separate `force` input on top of `all` (#261) — two deliberate actions, on purpose.
  - **Per-date is not automatically safe either.** `build_portfolio_payload` holds *every* collected
    day's bars in memory whichever date you ask for, and that grows daily (#273). Prefer a **past**
    date over the live day, run one at a time, watch `free -m`.
  - **Never `systemctl restart` the runner while a job is in flight** — it cancels the job, and a
    cancelled deploy can leave the app container **stopped**.
  - **After any OOM, confirm the runner came back** — a `failed` runner makes CI queue silently
    forever rather than fail.
  - Recovery, the `hcloud` out-of-band CLI and the full incident record: **`deploy/RUNBOOK.md`
    §9/§9.1** (Mac only; needs the token in `~/.config/hcloud/cli.toml`).

## How work gets done (#377, #489)
Work happens by **you driving Claude Code** (desktop or mobile). **There is no automation layer** —
no `/spec` gate, no auto-triage, no watchdog, no agent that opens issues on its own; the 2026-07-17
one was rolled back (#377, `decisions.md` §D-27). Nothing gates you: ask for any change on any
issue, whatever its labels, and it gets built.
- **Delegation (#489) is the one agent piece that came back.** Labelling an issue `agent` dispatches
  `claude.yml` — a Claude agent on a **hosted** runner (never the VPS) builds it and opens a PR;
  `@claude …` on that PR revises it; a human reviews and squash-merges. **Delegate only when all
  four hold:** `make check` is a sufficient verdict (the runner has no `.env`, IBKR, box or `/data`)
  · the brief is closed-form, because the agent can't ask a question mid-flight · XS/S tier · it
  isn't what you're actively iterating on. Engine/strategy work qualifies when the brief names the
  exact rule and test. Spikes, reports, review investigations and anything box- or data-touching
  stay in-house. Procedure — including the six-heading brief template — is the **`delegate-issue`**
  skill. ⚠️ Before adding a *second* agent workflow, read
  [`research/archive/github-automation.md`](./research/archive/github-automation.md), which is the
  design, the post-mortem and the `git show` range to resurrect from.
- The other workflows are the **hands-off, human-triggered** ones: `ci` (on every PR),
  `deploy`, `build-image`, `publish-dashboard` (scheduled), `backfill-dashboard`,
  `deploy-backfill-publish`, `data-export`. Trigger them with `gh workflow run <name>.yml`.
- **Liveness monitoring** is the app's own Healthchecks.io dead-man's switch (`monitoring.py`,
  `HEALTHCHECKS_PING_URL`) — it pings each tick and `/fail`s if the tick dies. It predates the
  automation layer and is the signal to trust.

## Working remotely (Claude Code on mobile / web)
The cloud environment has GitHub access (issues, PRs, board, CI all work) and can run `make setup`/`make check`, but it does **NOT** have: the local `.venv`, the local `gh` keyring token, the `.env` file, or any **live IBKR connection**. Therefore:
- ✅ Safe remotely: code, tests, docs, issues, PRs, reviewing CI.
- ❌ Not possible remotely: running `spikes/` or the trading app — anything needing IB Gateway must run on the **Mac or the VPS** (Gateway lives at `127.0.0.1`, with credentials + market-data entitlement that aren't in the cloud).
- 📊 **Reading box data from the cloud:** you **cannot** SSH into the box from a web session — the sandbox allows only HTTP/HTTPS through a domain-allowlist proxy (no port-22 egress) and has **no secret store** (env vars are plaintext). Pull `/data` instead via the on-demand **`data-export`** workflow (self-hosted `vps` runner queries `/data` → commits to the `data-export` branch → the session reads it back over GitHub). Drive it with the **`box-data`** skill. On the **Mac**, use the direct `docker exec` recipe (`review-analysis` skill).
- **Secrets** live in three places, never in git: `.env` (local dev), GitHub Actions secrets (CI), and the VPS environment (runtime). The cloud reads data through GitHub, so it needs **no** secret — **Trusted** network access is enough.
