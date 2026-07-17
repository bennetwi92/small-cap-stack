# GitHub-native automation — options for a public, agent-driven repo

*Added 2026-07-17. Standing report / decision input. Scope: how far we can push GitHub's free
tier (Actions / Issues / Pages / secrets) to develop and operate this project **without the
laptop**, including running **Claude Code in CI on the Max subscription** for build, triage, and
self-heal. Companion to [`free-tier-services.md`](./free-tier-services.md) (which covers the
runtime/hosting free tier); this doc covers the **automation & agentic layer** on top of it.*

The repo is **public** (`bennetwi92/small-cap-stack`) and runs a **self-hosted `vps` runner** on
the live Hetzner box (CX23, 2 vCPU / 4 GB, holds the `.env` + live IBKR session). That combination
is the axis everything below turns on — read §0 before anything else.

---

## 0. ⚠️ Blocker: public repo + self-hosted runner is a live security hole

GitHub's own guidance is blunt:

> "Self-hosted runners should almost never be used for public repositories, because any user can
> open a pull request and compromise the environment." — [GitHub Docs, secure use][gh-secure]

**The attack:** anyone forks the repo, opens a PR whose workflow sets `runs-on: [self-hosted, vps]`
on a fork-triggerable event, and their code executes **on the trading box** — reaching `.env`, the
IBKR session, and `/data`. This is not theoretical; it is the default behaviour of a self-hosted
runner attached to a public repo.

**Mitigations (do these before adding any new automation):**
- Never let a fork-triggerable event (`pull_request`, `pull_request_target`, `issue_comment`) run
  on the self-hosted runner. Restrict the VPS runner to `workflow_dispatch` / `push`-to-`main`.
- Guard every self-hosted job with `if: github.repository == 'bennetwi92/small-cap-stack'` so a
  fork can never match.
- Keep **"Require approval for all outside collaborators"** on (public-repo default).
- Run **all Claude / triage / self-heal jobs on GitHub-hosted runners** (`ubuntu-latest`) — free,
  ephemeral, isolated — never on the VPS.
- Note the cost drift: since **2026-03-01 self-hosted minutes carry a $0.002/min platform charge**
  ([changelog][gh-pricing]) — one more reason to keep automation on hosted runners.

References: [StepSecurity][stepsec] · [Latchkey][latchkey].

---

## 1. Economics — why this is effectively free

- **Public repos get unlimited free minutes on standard GitHub-hosted runners** ([billing
  docs][gh-billing]). Only larger runners and self-hosted minutes cost money. So all the
  hosted-runner automation below is $0 compute.
- **Claude in CI runs on the Max subscription, not an API bill.** `anthropics/claude-code-action`
  supports OAuth-token auth: run `claude setup-token` locally → paste the token into repo secret
  **`CLAUDE_CODE_OAUTH_TOKEN`** → reference it as `anthropic_api_key: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}`.
  Usage counts against the Max quota. ⚠️ **Do not also set `ANTHROPIC_API_KEY`** — if both are
  present the API key wins and you get billed. ([action setup docs][cc-setup])
- Free-on-public security tooling worth enabling regardless: **CodeQL**, **secret scanning + push
  protection**, **Dependabot**.

---

## 2. The options (ranked for this project)

1. **`@claude` on issues/PRs — the phone-first dev loop (highest leverage).** File an issue from
   mobile, comment `@claude implement this`, Claude opens a PR on a hosted runner, `ci.yml` runs,
   you review + squash-merge from the phone. Fits the trunk-based / issue-driven flow with no
   process change. This is the core "develop without the laptop" unlock.
2. **Auto-triage on `issues.opened`.** Claude applies the existing labels
   (`strategy`/`data`/`infra`/`spike`/…), adds the issue to project board #3, sets Status=Todo, and
   cross-links `research/decisions.md` + related issues — automating the hygiene CLAUDE.md already
   mandates on every task.
3. **Self-heal on CI failure.** A `workflow_run: completed (failure)` trigger feeds the failing
   `lint-typecheck-test` logs to Claude, which comments a root-cause diagnosis or opens a fix PR.
4. **Scheduled overnight analyst (cron agent).** Nightly, Claude pulls the day's tracker data via
   the existing **`data-export`** flow, summarizes tick health / opportunities / R-capture into an
   issue comment or a Pages report, and flags `decisions.md` drift. No box compute — it reads
   exported Parquet, so it can't OOM the CX23.
5. **Issues/comments as a safe control plane.** Instead of exposing raw `workflow_dispatch` (which
   can OOM the box), a guarded comment command like `/backfill 2026-07-15` triggers a **single-date**
   job that enforces the "never `--all`, one date at a time" box rule in code. Issue Forms become a
   phone UI for backtest/spike requests.
6. **Pages beyond the dashboard.** PR preview deploys of the cockpit, a rendered `research/` site,
   status badges, and publishing the nightly analyst report — all free on public repos.

---

## 3. Recommended rollout order

**(0) Lock down the self-hosted runner → (1) wire `CLAUDE_CODE_OAUTH_TOKEN` + `@claude` →
(2) auto-triage → (3) self-heal → (4) overnight analyst.**

Steps 0–1 are the foundation and deliver the phone-first workflow immediately; everything else
builds on them. Every new Claude/automation job runs on `ubuntu-latest`, never the VPS runner.

## 4. Open questions

- **Deploy approval gates.** Should Claude-proposed deploys go through a GitHub *Environment* with a
  required reviewer, so live deploys still need one human click even when the PR is agent-authored?
- **Token blast radius.** `CLAUDE_CODE_OAUTH_TOKEN` is a long-lived personal credential in repo
  secrets — acceptable on a solo public repo, but worth a rotation cadence and a note in the runbook.
- **OIDC / short-lived creds.** Can the VPS-touching workflows move from long-lived secrets to
  GitHub OIDC to shrink the standing-secret surface?

---

<!-- links -->
[gh-secure]: https://docs.github.com/en/actions/reference/security/secure-use
[gh-billing]: https://docs.github.com/en/actions/concepts/billing-and-usage
[gh-pricing]: https://github.blog/changelog/2025-12-16-coming-soon-simpler-pricing-and-a-better-experience-for-github-actions/
[cc-setup]: https://github.com/anthropics/claude-code-action/blob/main/docs/setup.md
[stepsec]: https://www.stepsecurity.io/blog/defend-your-github-actions-ci-cd-environment-in-public-repositories
[latchkey]: https://latchkey.dev/learn/ci-how-to/secure-self-hosted-runner-public-repo-github-actions
