---
name: delegate-issue
description: Hand a small, fully-specified unit of work to the remote Claude build agent on GitHub instead of doing it in this session — write the brief, dispatch it with the `agent` label, monitor the run, review and squash-merge the PR. Use when a task is closed-form and CI-verifiable, or when several independent small tasks could build in parallel while this session works on something harder.
---

# delegate-issue

`.github/workflows/claude.yml` runs a Claude agent on a GitHub-hosted runner. Adding the **`agent`**
label to an issue dispatches it; it works on its own branch and opens a PR. **You** review and
squash-merge — nothing auto-merges.

The point is **parallelism**: three delegated issues build at once while this session does the piece
that needs judgement. It is not a way to avoid work, and it is not free — remote runs draw on the
**same Max subscription quota** as this session.

## 1. Triage — is this delegable?

Delegate only when **all four** hold. If any fails, do it here and say why in one line.

1. **`make check` is a sufficient verdict.** The hosted runner has no `.env`, no IB Gateway, no box,
   no `/data`, no `data/recon/`. If confirming the work needs a live session, real parquet, a chart
   to look at or a screenshot, it stays here.
2. **The spec is closed-form.** You can write the entire brief *now* — exact files, exact behaviour,
   a named test. **The remote agent cannot ask a question mid-flight.** If it would need to, it's yours.
3. **XS or S tier** (CLAUDE.md's estimation table: ≤ ~250 lines, ≤ ~5 files). M/L cost more to review
   than they save in typing.
4. **It isn't what's being actively iterated on.** A round trip is slower than just doing it.

**Engine/strategy work IS delegable** when the brief names the exact rule and the exact test —
trading logic is exhaustively unit-tested, so CI is a real gate there. Wide scope is deliberate.

**Never delegate:** spikes · reports and analyses · review-page investigations · deploys, backfills,
harvest · anything under `data/` · anything needing the box or IBKR · anything where the judgement
*is* the work.

**Batch trigger:** when ≥3 independent qualifying items exist, dispatch them together. One delegated
issue rarely beats just doing it; three in parallel does.

## 2. Write the brief

The issue body is the **whole contract** — the agent sees it and `CLAUDE.md`, and nothing of this
conversation. Vagueness here is what comes back as a wrong PR. Six fixed headings:

```markdown
## Goal
One sentence. What is true after this merges that isn't true now.

## Files
The exact paths to touch. If a path is a guess, that's a triage failure — find it first.

## Change
The precise behaviour. Name functions, constants, settings keys, selectors. Give the before/after
for anything numeric. If a rule changes, quote the current rule.

## Acceptance
- `make check` passes.
- <named test> covers <case>.  ← a specific test, new or existing. "add tests" is not acceptance.

## Out of scope
What NOT to touch. This is the highest-value heading — it is what stops a 40-line PR becoming 400.

## Conventions
Branch `<type>/<issue#>-<slug>` · conventional PR title · `Closes #<n>` in the body ·
`Co-Authored-By: Claude <noreply@anthropic.com>` · run `make check` before pushing.
```

## 3. Dispatch

```bash
gh issue create --title "<conventional: title>" --label agent --label <phase-1|infra|strategy|…> --body "<brief>"
gh project item-add 3 --owner bennetwi92 --url <issue-url>
scripts/board.sh <issue#> "In Progress"
```

The `agent` label is the trigger — apply it at creation. Adding it later works too (`gh issue edit
<n> --add-label agent`) and is how you delegate an issue that already exists.

## 4. Monitor

```bash
gh run list --workflow=claude.yml --limit 5        # did it start?
gh run watch <run-id>                              # live
gh pr list --state open                            # what came back
```

The action posts a **tracking comment** on the issue with a live checklist — that's the surface to
watch from the phone. Tell the user what was dispatched and where to look; don't sit and poll.

## 5. Land

```bash
gh pr diff <pr#>          # read it — every line. This is the only review it gets.
gh pr checks <pr#>        # lint-typecheck-test must be green
gh pr merge <pr#> --squash --delete-branch
scripts/board.sh <issue#> Done
```

⚠️ **Verify `lint-typecheck-test` actually ran.** GitHub suppresses workflow triggers for PRs opened
with the default `GITHUB_TOKEN`. If the PR shows no checks, close and reopen it to force CI —
and if it recurs, that's a workflow bug to fix (see the note in `claude.yml`), not a per-PR ritual.

## 6. Revise, don't redo

Comment on the **PR** with `@claude <what to change>` — it amends that branch in place. Cheaper than
a new issue, and keeps the review thread in one place. If two revisions don't converge, pull the
branch and finish it here; that's a triage-rule failure worth noting.
