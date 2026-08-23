# How we work — the reasoning behind the working agreement

`CLAUDE.md` carries the **rules**: short, imperative, loaded into every session and every delegated
agent run. This file carries the **why** — the incidents, measurements and post-mortems those rules
were distilled from. Read it when a rule looks arbitrary, when you are about to argue with one, or
when you are deciding whether a new rule earns its place.

Kept separate on purpose: `CLAUDE.md` was 26.6 KB (~7,000 tokens) and about half of it was these
stories. On a Pro subscription that is a real cost paid at the start of every session, so the
narrative moved here and the rules stayed there.

---

## 1. Why the strategy spec is generated, and never restated

Seven surfaces used to state the rules and they disagreed on four price bands (#551). Two published
reports went on asserting a float gate that had **never run**. The fix only holds if new prose
**links** to `research/strategy.md` instead of copying its numbers — which is why
`tests/test_strategy_doc.py` fails when the generated spec drifts from `config.py`, and why
`make strategy` is mandatory after a rule change.

The same failure is why a report gets a `correction:` line rather than an edit: before #551 nothing
marked a report stale, so being overtaken was invisible.

## 2. Why `detect_day`'s defaults are not `config.py`'s values

`detect_day`'s own defaults are a deliberate **shape-only, rule-OFF baseline** for tests and spikes —
8 of 21 differ from the shipped values on purpose. `detect_day_with_settings` is the only path that
reads `config.py`, so a knob wired anywhere else silently runs with its rule switched off, and a
parameter the wrapper forgets is a rule that quietly stops applying.

`tests/test_settings_wiring.py` derives that requirement from the function signature, so it covers a
knob added tomorrow (#525). The recurring temptation is to "deduplicate" the two sets of defaults.
Don't — they are answering different questions.

## 3. Why `passed` ≠ `takeable`

`passed` = the bull flag is well-formed (shape gates only) — what the review workbench and the 25
golden fixtures are written against. `takeable` = *and it's one we'd select*.

A $1.50 name or an 11:00 break therefore stays **visible and scoreable** rather than being reported
as malformed, which is what a data-collection phase needs (§D-03, "collect before you filter" — a
Phase-1 stance, not a trading philosophy; it gets revisited at the Phase-2 gate).

Float and news follow the same logic one step further: they are enrichment written *after* a name is
flagged. Neither the shape gates nor the selection rules read them, and `gates.py::float_gate` /
`news_gate` feed EOD **counts** only. So the book really does take high-float names — it holds CLSK
(246M) and XRX (119M). Evidence: `docs/reports/2026-07-31-float-vs-max-r.md`. If that should change,
the gate goes in the engine's selection tier; `tests/test_portfolio_extract.py` pins today's
behaviour and the float test's failure message tells you to delete it if you meant it.

## 4. Why read cost is measured in files, not rows

For this Parquet store, **read cost tracks FILE count, not row count or bytes on disk** — every read
opens each file's footer, so 32k one-row files read ~40× slower than the same rows in a few hundred
files.

This is not theory: **three separate PRs missed a 36s/60s tick regression** because they sized reads
in rows and gigabytes (#318/#319/#321). Keep hot-path reads `dt=`-scoped and watch the `files` counts
in `status.json` / `scs_dataset_files`.

## 5. Why `review-data` must never be pruned

Four branches are data, not work. `review-data` is the dangerous one: ~167 hand-made JSON files —
the trader's saved reviews and chart annotations, the ground truth the 25 golden fixtures came from.
It is **irreplaceable**: hand-made, not regenerable from anything.

Nothing in the working agreement named it until #522, which is exactly why an audit proposed deleting
it as "a stale full-tree copy". It *looks* like one, because `review.js` creates it off `main`'s HEAD
on first save and then commits `reviews/*.json` on top.

## 6. Why coverage is gated on main and not on PRs

The PR run is the merge gate and runs the suite bare; the push-to-main run is the covered one and
enforces `--cov-fail-under=90` over **line *and* branch** coverage (#494/#495).

Branch coverage matters separately: line coverage alone called `portfolio/extract.py` 92% while its
branch figure was 80% (#530). Leaving coverage on PRs costs ~40% of the pytest step to re-measure a
number that has sat above 93% for weeks and says nothing about the diff under review. The split is
about CI cost, not about relaxing the bar — `make check` runs `make cov`, so a PR that would drop
main below the bar is visible before you push.

**Why the flags live on the CI line and in `make cov`, never in pytest's `addopts`:** in addopts,
`pytest tests/one_file.py` exits 1 with *"Required test coverage of 80% not reached. Total coverage:
4.77%"* while every test passes — which teaches you to read past a red pytest (#530).

**Why `filterwarnings = ["error"]` stays:** a deprecation from polars or duckdb is a change to how
this system computes money, so it should fail the build on the PR that bumps the dep.

**Why `ci` is the only required check:** CodeQL default setup and `build-image`'s PR trigger were
both removed. A one-person repo gained nothing from two extra non-required jobs except more red Xs to
interpret — and a GitHub Actions outage that fails them makes a healthy PR look broken.

**Why `--locked`, not `--frozen` (#546):** measured, not assumed — with a dep added to `pyproject`
and no re-lock, `--frozen` exits 0 and installs without it, while `--locked` exits 1. That matters
because `build-image` builds the deployed image from `requirements.lock` and does **not** run on pull
requests, so under `--frozen` forgetting `make lock` would go green on the PR *and* on the push to
main, and surface as the live tracker ImportError-ing on the box.

## 7. Why `docs/` is the frontend, and what the 10-minute cache does

`docs/` is the GitHub Pages dashboard, published by our own `.github/workflows/pages.yml`
(`build_type: workflow`, #486) — the legacy build's managed workflow aborted the deployment after 10
minutes, which GitHub's publish step started routinely overrunning. Renaming `docs/` is therefore no
longer forbidden by Pages, but it is still a rename of the `path:` in that workflow and of every
reference to it.

**The cache trap:** Pages serves HTML and JS with `max-age=600`, unversioned. A browser reload
revalidates the navigation HTML while still serving the *script* from cache — so for up to ~10
minutes after a deploy that changed the markup, a returning visitor runs the **previous** JS against
the **current** HTML, and the page dies on an element the new markup doesn't have.

**A self-consistent PR does not prevent this.** #403 removed `#charts-card` from both halves and
still produced `Cannot set properties of null (setting 'hidden')` in the wild. It self-heals when the
cache expires. So don't chase a post-deploy null-property error as a box or data outage — check
whether the markup changed in the last deploy first.

**Why pages carry no commentary (#414):** prose on a page has to be re-read and re-approved every
time the data moves. That is how the pre-#414 Plan page and Projection view went stale. Commentary
goes in a report, where it is dated and can be superseded.

## 8. Why `data/recon/` is a separate store root

It holds pre-market days rebuilt from **vendor** bars, kept apart so nothing reading `data/` can
return vendor rows by accident (#430). Only `build_portfolio_payload(recon_store=…)` opts in, and its
charts publish to their own `recon_index.json` namespace (#488). Every trade carries
`source: "live" | "recon"`. Design rationale: §D-30/§D-31/§D-33. Operations: `deploy/RUNBOOK.md` §13.

## 9. Why the box rules are absolute

The box is a Hetzner CX23: **2 vCPU / 4 GB**. A heavy job takes it down hard — sshd stops answering,
the runner drops **offline (busy)**, and you can then neither cancel nor SSH in. Each rule cost
something:

- **`backfill-dashboard --all` (#264) cost 5h37m of CI.** `--all` needs `--force`, and the workflows
  need a separate `force` input on top of `all` (#261) — two deliberate actions, on purpose.
- **`docker exec` into the app for a heavy job spends the tracker's cgroup and OOMs the tracker
  instead of the job** (#545). Use `scripts/box-job.sh`, which gets its own 1 GB container.
- **Per-date is not automatically safe either:** `build_portfolio_payload` holds *every* collected
  day's bars in memory whichever date you ask for, and that grows daily (#273).
- **Never `systemctl restart` the runner mid-job** — it cancels the job, and a cancelled deploy can
  leave the app container **stopped**.
- **After any OOM, confirm the runner came back** — a `failed` runner makes CI queue silently forever
  rather than fail.

Recovery and the `hcloud` out-of-band CLI: `deploy/RUNBOOK.md` §9/§9.1.

## 10. Why there is no automation layer

An automation layer was built on 2026-07-17 and **rolled back on 2026-07-19** (#377, §D-27): the
`/spec` gate, auto-triage, the slash-command control plane, the watchdogs, the overnight analyst, the
auto-merge risk policy. Nothing gates you now — ask for any change on any issue, whatever its labels,
and it gets built.

**Delegation (#489) is the one piece that came back**, deliberately scoped: a hosted-runner agent that
builds a fully-specified issue. Its security posture is non-negotiable — hosted runner only (a public
repo plus a self-hosted runner means any fork PR could execute on the trading box), no
`pull_request_target` anywhere, comment triggers gated on `author_association`, SHA-pinned action,
least-privilege permissions, wall-clock timeout. `id-token: write` is not optional: the action
exchanges an OIDC token before it does anything, so without it every dispatch dies in
`setupGitHubToken` having done no work (#499, the same gap as #370).

Before adding a *second* agent workflow, read `research/archive/github-automation.md` — the design,
the post-mortem, and the `git show` range to resurrect from.

**Liveness monitoring** is the app's own Healthchecks.io dead-man's switch (`monitoring.py`,
`HEALTHCHECKS_PING_URL`). It predates the automation layer and is the signal to trust.

## 11. Why the repo is public, and what that is worth

Public repos get **unlimited GitHub-hosted Actions minutes**. CI runs on `ubuntu-latest` and
`publish-dashboard` fires 96 times a day. Making the repo private would start metering against the
2,000 min/mo allowance — so "public" is a standing cost decision, not just an openness one.

The security posture in §10 exists *because* the repo is public. The two are a package.

## 12. What the box actually costs, and why it can't be halved

Investigated 2026-08-23 against live Hetzner pricing (`hcloud server-type describe`):

| Option | Spec | Price/mo | Verdict |
|---|---|---|---|
| **cx23** (current) | 2 vCPU / 4 GB x86, fsn1 | **€6.59** | what we pay |
| cax11 | 2 vCPU / 4 GB **ARM** | €7.19 | **more expensive**, and `Available: no` in fsn1/nbg1/hel1 |
| cpx11 | 2 vCPU / **2 GB** x86 | €6.59 | same price, half the RAM — on a box that already OOMs at 4 GB |
| cpx12 | 1 vCPU / 2 GB x86 | €13.79 | more expensive and smaller |
| Oracle A1 Always Free | 4 GB+ ARM | €0 | **already tried and abandoned** — repeated "Out of host capacity" (§D-01 row 5) |

So the ARM story is dead twice over: it costs more *and* it is sold out. Dropping the IPv4 primary IP
saves ~€0.60/mo and breaks SSH from IPv4-only networks — not worth it.

⚠️ **Correction to a standing claim:** `deploy/RUNBOOK.md` and `research/free-tier-services.md` say
"our images are multi-arch". **The app image is not** — `.github/workflows/build-image.yml` builds
`linux/amd64` only. The *Gateway* image (`gnzsnz/ib-gateway`) genuinely is multi-arch (its pinned
digest carries linux/amd64 and linux/arm64). Anyone planning an ARM migration must add
`linux/arm64` to that `platforms:` line first, and check that `requirements.lock`'s
`--require-hashes` install resolves on aarch64.

**The costs that actually matter are not infrastructure.** `research/broker-costs.md`: at $500
capital, commission drag is **9–13% per month** (the per-order minimum, not the per-share rate),
versus ~2.9% at $2,000. #311 (the $10/mo L1 market-data bundle) is correctly still unpaid while
Phase 1 only collects. The tail risk is professional reclassification at ~$100+/mo, which would dwarf
every other line.
