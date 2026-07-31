---
name: publish-report
description: Write an analysis and publish it to the dashboard's Reports page. Use when the user asks for a report, write-up, analysis, or investigation on a topic and wants to read it in the web app — anything like "write me a report on X", "publish an analysis of Y", "put that on the reports page".
---

# publish-report

A **report** is a markdown analysis in `docs/reports/`, listed on the dashboard's Reports tab
(`docs/reports.html`) and served by GitHub Pages. It lands when its PR merges — nothing is
scheduled, nothing is automatic.

## Procedure

1. **Issue.** Create/locate a GitHub issue for the report (labels: `phase-1` + the topic's label,
   e.g. `strategy` / `data`), add it to board #3, and set it In Progress:
   `scripts/board.sh <issue#> "In Progress"`.

2. **Do the analysis first, then write.** A report is only worth publishing if its numbers are
   real. Pull the data before drafting:
   - Web/mobile session → the **`box-data`** skill (the `data-export` workflow; no SSH from the cloud).
   - Mac → the **`review-analysis`** skill's `docker exec` recipe, or the local store.
   - Repo-only questions (methodology, spec archaeology) → `research/` and the code.
   State the window the numbers cover and the row counts they rest on. If a number could not be
   verified, say so in the report rather than rounding the uncertainty away.

3. **Scaffold the file:**
   ```bash
   .venv/bin/python -m small_cap_stack.reports new \
     --title "Float gate revisited" --summary "What the 20M-share cap costs us." --tags strategy,data
   ```
   That writes `docs/reports/<today>-<slug>.md` with front matter (`title`, `published`, `summary`,
   `tags`; `author` optional, defaults to Claude) and re-indexes. Pass `--published YYYY-MM-DD` to
   date it differently — the list sorts on that field, newest first.

4. **Write the report.** Markdown; GFM tables, code blocks and blockquotes all render. Conventions:
   - The page renders the title and metadata from the front matter, so the body's own opening
     `# Title` line is dropped — don't repeat headings, just write.
   - `##` headings are the section rule the eye follows — one per question answered.
   - Lead with the answer. The reader is the person who asked; they want the finding, then the
     evidence, then the method.
   - Link the issues and decisions the analysis touches (`#127`, `research/decisions.md`).

5. **Rebuild the index and check:**
   ```bash
   make reports   # regenerate docs/reports/index.json — a report missing from it is invisible
   make check     # ruff + mypy + pytest; test_reports.py fails if the index is stale
   ```

6. **Land it.** PR titled `docs: <report title>` with `Closes #<issue#>`, squash-merge, then
   `scripts/board.sh <issue#> Done`. The Reports page picks it up as soon as Pages redeploys.

## Notes

- **Reports live in the repo, never on the box.** `publish-dashboard` force-pushes a fresh single
  commit to `dashboard-data` every 15 min, so anything hand-written on that branch is destroyed.
- Reports are prose, not data: no secrets, no raw account values, no credentials — the Pages site
  is `noindex` but public.
- To correct a published report, edit the markdown and re-run `make reports` in the same PR.
