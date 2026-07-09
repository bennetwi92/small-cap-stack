---
name: box-data
description: Pull raw tracker data off the box (the VPS `/data` Parquet store) into a Claude Code web/mobile session for analysis, without SSH. Dispatches the `data-export` GitHub Action on the box's self-hosted runner, which queries `/data` and commits the result to the `data-export` branch; then reads that file back over GitHub and loads it into polars/DuckDB. Use when a web/mobile session needs bars, opportunities, scanner_hits, news, fundamentals, or analysis rows for a symbol/date range and can't reach the box directly.
---

# box-data

Get raw `/data` off the box and into a **web/mobile** session for analysis.

## Why this exists (don't try to SSH)
A cloud session **cannot** SSH into the box: the web sandbox allows only HTTP/HTTPS through a
domain-allowlist proxy (no port-22 / raw-TCP egress, even on "Full"), and it has **no secret store**
(env vars are stored in plaintext in the environment config). The box also keeps **no inbound
ports**. So data comes out the same way deploys go in — through GitHub and the self-hosted
`[self-hosted, vps]` runner. On the **Mac**, prefer the direct `docker exec` recipe in the
`review-analysis` skill instead; this skill is the cloud-only path.

**Prerequisite:** the `[self-hosted, vps]` runner must be registered on the box (issue #6, same gate
as `deploy.yml`). If it isn't up, the dispatched run just queues.

## The loop (GitHub MCP tools)
1. **Dispatch** `.github/workflows/data-export.yml` (`workflow_dispatch`) with `actions_run_trigger`.
   Inputs:
   - `dataset` — `bars` | `opportunities` | `scanner_hits` | `news` | `fundamentals` | `analysis` |
     `query`
   - `start_date` / `end_date` — inclusive `YYYY-MM-DD` (dataset mode; blank = unbounded)
   - `symbols` — comma-separated, symbol-keyed datasets only (e.g. `SNDQ,AAPL`)
   - `query` — raw DuckDB SQL over the dataset views (only when `dataset = query`); e.g.
     `SELECT symbol, count(*) FROM opportunities WHERE dt >= '2026-07-01' GROUP BY 1`
   - `format` — `parquet` (default, compressed — best for wide ranges) | `csv` | `ndjson`
   - `ref` — ref to run the exporter from (default `main`)
2. **Poll** the run with `actions_get` (list recent runs with `actions_list` to find the run id) until
   `status = completed`. On `conclusion = failure`, read `get_job_logs` — the job summary echoes the
   exporter's `sql=/rows=/schema=` line.
3. **Read the result** from the **`data-export`** branch (an orphan, data-only branch — never merged
   to `main`, mirrors `review-data`). The workflow commits to
   `exports/<run_id>/<dataset>_<run_id>.<ext>` plus an `export.log`. Fetch with `get_file_contents`
   (`ref: data-export`), base64-decode the content, and:
   - **parquet:** write the bytes to a temp file and `pl.read_parquet(path)`.
   - **csv / ndjson:** decode to text and `pl.read_csv` / `pl.read_ndjson`.
4. **Analyze** locally in the session (polars / an in-process DuckDB). For engine R-metric replays,
   the same helpers used on the box apply: `report.symbol_runs`, `rmetrics.compute_r_metrics` (see
   `scripts/analysis/probe_run.py` and the `review-analysis` skill).

## Notes
- The exporter is `scripts/analysis/export_query.py`; it reuses `Store("/data").query(...)`, so every
  dataset with data is available as a DuckDB view of its raw Parquet.
- Scope by date/symbol when you can — a full `bars` history is large. Parquet keeps it compact.
- Nothing here needs a secret or a network-policy change: **Trusted** network access is enough
  (`github.com` is already allowlisted).
