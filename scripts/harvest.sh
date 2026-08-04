#!/usr/bin/env bash
# Nightly pre-market harvest (#431) — one containerised, memory-capped, nice'd run.
#
# Deliberately NOT `docker exec` into the running app. The app's cgroup is the tracker's 2 GB
# budget: a harvest sharing it would spend the tracker's headroom, and a harvest that regressed
# would OOM-kill the tracker rather than itself. A separate `docker run` gets its own 1 GB cgroup,
# its own OOM score, and dies alone.
#
# Called by scs-harvest.service (see deploy/). Safe to run by hand on the box:
#
#     /opt/small-cap-stack/scripts/harvest.sh run            # tonight's sessions
#     /opt/small-cap-stack/scripts/harvest.sh status         # what is done, what is left
#     /opt/small-cap-stack/scripts/harvest.sh run --limit 1  # the single-session smoke test
#
# The job refuses to start outside 17:00-03:00 ET on its own (harvest_start_et/harvest_stop_et), so
# a timer that fires late, or a hand-run at the wrong hour, stops itself rather than competing with
# the 04:00 scan window. That check lives in the app, not here, precisely so this script cannot be
# the thing that gets it wrong.
set -euo pipefail

REPO_DIR="${REPO_DIR:-/opt/small-cap-stack}"
ENV_FILE="${ENV_FILE:-$REPO_DIR/.env}"
DATA_VOLUME="${DATA_VOLUME:-small-cap-stack_scs-data}"
# Same tag compose is pinned to, so the harvest runs the deployed code rather than drifting to
# :latest behind the tracker's back.
IMAGE_TAG="$(grep -E '^IMAGE_TAG=' "$ENV_FILE" 2>/dev/null | cut -d= -f2- || true)"
IMAGE="ghcr.io/bennetwi92/small-cap-stack:${IMAGE_TAG:-latest}"

# 1 GB hard, swap pinned to the same value (memory-swap is the COMBINED limit, so equal values mean
# no swap at all). The box has 4 GB and the tracker + Gateway want ~3.5 of it; swapping a background
# job is how the CX23 thrashes past sshd (#264).
MEM_LIMIT="${HARVEST_MEM_LIMIT:-1g}"

# --env-file carries MASSIVE_API_KEY and any HARVEST_* overrides. It also carries `DATA_DIR=./data`
# and `DUCKDB_PATH=./data/...` — the LOCAL-dev values from .env.example, which would override the
# image's `/data` and land the whole harvest inside the container's working directory, to be
# deleted with it on --rm. So re-assert the container paths afterwards: `-e` takes precedence over
# --env-file, and these are properties of the container, not of the host's config.
#
# HEALTHCHECKS_PING_URL is blanked for the same reason it is never constructed in the harvest code:
# a stalled harvest must never page as a tracker outage (#431).
exec nice -n 19 ionice -c 3 \
  docker run --rm \
    --name scs-harvest \
    --memory="$MEM_LIMIT" \
    --memory-swap="$MEM_LIMIT" \
    --oom-score-adj=800 \
    --cpus=1 \
    --env-file "$ENV_FILE" \
    -e DATA_DIR=/data \
    -e DUCKDB_PATH=/data/small_cap_stack.duckdb \
    -e HEALTHCHECKS_PING_URL= \
    -v "$DATA_VOLUME":/data \
    "$IMAGE" \
    python -m small_cap_stack.harvest "$@"
