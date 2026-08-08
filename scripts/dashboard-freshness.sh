#!/usr/bin/env bash
# How stale is the PUBLISHED dashboard? (#688)
#
# The app knows when it last wrote `/data/dashboard/status.json`
# (`scs_dashboard_artifact_written_timestamp_seconds`). It does not and cannot know whether that
# file ever reached the branch the frontend reads: `publish-dashboard` is a GitHub Actions cron on
# the self-hosted runner, and it fails in ways the app is not involved in —
#
#   * GitHub DISABLES scheduled workflows after ~60 days of repo inactivity (RUNBOOK §7). It is the
#     documented way this breaks, it is silent, and `publish-dashboard` is the only schedule left.
#   * the runner goes `offline (busy)` after an OOM and every queued job sits there forever (#264).
#   * a force-push lands an empty or partial tree.
#
# In all three the app is perfectly healthy and the dashboard is frozen. So this measures the thing
# from the outside — the same URL the browser fetches — and reports the age of the payload.
#
# It writes node_exporter textfile metrics rather than exposing a port, because it is a oneshot: it
# is gone before the next scrape. Alloy's unix exporter serves whatever is in the textfile
# directory (deploy/alloy/config.alloy).
#
# Install: deploy/RUNBOOK.md §7.

set -euo pipefail

URL="${SCS_STATUS_URL:-https://raw.githubusercontent.com/bennetwi92/small-cap-stack/dashboard-data/status.json}"
TEXTFILE_DIR="${SCS_TEXTFILE_DIR:-/var/lib/node_exporter/textfile_collector}"
OUT="${TEXTFILE_DIR}/scs_published.prom"

mkdir -p "$TEXTFILE_DIR"

# Written to a temp file and renamed, not appended in place. node_exporter reads this directory on
# every scrape, and a half-written file is a PARSE ERROR that takes down the whole textfile
# collector — not just this metric. Same reasoning as storage.py's atomic part-file writes.
tmp="$(mktemp "${OUT}.XXXXXX")"
trap 'rm -f "$tmp"' EXIT

emit() {
	printf '%s\n' "$1" >>"$tmp"
}

emit '# HELP scs_published_probe_success 1 when the published dashboard payload was fetched and parsed.'
emit '# TYPE scs_published_probe_success gauge'
emit '# HELP scs_published_status_age_seconds Age of the published status.json, by its own generated_utc stamp.'
emit '# TYPE scs_published_status_age_seconds gauge'

body="$(curl --fail --silent --show-error --max-time 20 "$URL" 2>/dev/null || true)"

if [ -z "$body" ]; then
	emit 'scs_published_probe_success 0'
	mv "$tmp" "$OUT"
	trap - EXIT
	exit 0
fi

# The stamp is an ISO-8601 UTC datetime written by `dashboard.build_status`. Extracted with grep
# rather than jq: jq is not otherwise needed on the box, and one more package to keep installed for
# one field is a dependency the recovery path has to remember.
stamp="$(printf '%s' "$body" | grep -o '"generated_utc"[[:space:]]*:[[:space:]]*"[^"]*"' | head -n 1 | sed 's/.*"\([^"]*\)"$/\1/')"

if [ -z "$stamp" ]; then
	# Fetched something, but it is not the payload we think it is — an empty force-push, or a
	# GitHub error page served with a 200. Deliberately NOT reported as a successful probe.
	emit 'scs_published_probe_success 0'
	mv "$tmp" "$OUT"
	trap - EXIT
	exit 0
fi

if ! generated="$(date -u -d "$stamp" +%s 2>/dev/null)"; then
	emit 'scs_published_probe_success 0'
	mv "$tmp" "$OUT"
	trap - EXIT
	exit 0
fi

now="$(date -u +%s)"
emit 'scs_published_probe_success 1'
emit "scs_published_status_age_seconds $((now - generated))"

mv "$tmp" "$OUT"
trap - EXIT
