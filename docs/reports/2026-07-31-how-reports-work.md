---
title: How reports work
published: 2026-07-31
summary: The publishing path for written analyses — ask Claude, it lands here on merge.
tags: docs, process
---

# How reports work

This page is a report about reports. It exists so the Reports tab is never empty, and so the
publishing path is written down where you actually read it.

## Asking for one

Ask Claude, in whatever words: *"write me a report on how often the float gate rejects a setup
that would have run"*. Claude does the analysis (pulling box data via the `box-data` skill if the
question needs it), writes it up as markdown, and opens a PR. When that PR merges, the report shows
up in the list above — GitHub Pages serves it within a minute or two.

Nothing is scheduled and nothing is automatic: a report exists because you asked for it.

## Where a report lives

| Thing | Where |
| --- | --- |
| The prose | `docs/reports/<published>-<slug>.md` |
| The list the page reads | `docs/reports/index.json` (generated) |
| The page | `docs/reports.html` + `docs/reports.js` |
| The builder | `src/small_cap_stack/reports.py` |

Reports live in the **repo**, not on the box. That is deliberate. The box publishes its dashboard
JSON by force-pushing a fresh single commit to the `dashboard-data` branch every 15 minutes, so
anything hand-written there is destroyed on the next cycle. Prose belongs in git, reviewed through
the same PR flow as the code, and `docs/` is already the Pages source — so a merged report is a
served report, with no box round-trip and no new workflow.

## The file format

Each report is markdown with a small front-matter header:

```markdown
---
title: Float gate revisited
published: 2026-07-20
summary: What the 20M-share cap costs us.
tags: strategy, data
---

# Float gate revisited

…the analysis…
```

`title` and `published` are required; `summary`, `tags` and `author` are optional (`author`
defaults to Claude). `published` is an ISO date, or a full ISO datetime when two reports land on
the same day and the order matters. The front matter is *not* rendered — the page shows it as the
report's header instead.

## Rebuilding the index

The list on the Reports page is `index.json`, built from the markdown by:

```bash
make reports        # or: python -m small_cap_stack.reports build
```

Regenerating is not optional — a report whose entry is missing from `index.json` is invisible to
the page. `tests/test_reports.py` fails when the committed index is stale, so CI catches it before
a merge does.

> **Scaffolding a report:** `python -m small_cap_stack.reports new --title "…" --tags strategy`
> writes the file with front matter filled in and re-indexes in one step.

## Rendering

Markdown is rendered in the browser (`marked`, from the same jsDelivr CDN the charts and grids use)
and styled with the cockpit's own tokens rather than a stylesheet from the renderer — so headings,
tables and code blocks match the rest of the dashboard. If the CDN is unreachable the report still
opens, as its raw markdown source.

Because reports go through a PR like any code change, raw HTML inside one is treated as ours and
rendered as authored.
