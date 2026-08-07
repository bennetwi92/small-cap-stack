#!/usr/bin/env bash
# Run an on-demand job against the box's store — in its OWN container (#545).
#
#   scripts/box-job.sh <label> -- <python args…>
#   scripts/box-job.sh backfill -- -m small_cap_stack.dashboard_backfill --date 2026-07-01
#
# Deliberately NOT `docker exec` into the running app, which is what the backfill and data-export
# workflows used to do. Two separate problems with that, both live:
#
#  1. **The app's cgroup is the tracker's budget.** compose caps it at `mem_limit: 2g` with
#     `oom_score_adj: 500`, so a backfill that grows — and `build_portfolio_payload` holds every
#     collected day's bars in memory regardless of which date you asked for (#273) — pushes the
#     cgroup over and the kernel reaps *the live tracker*, not the job. `scripts/harvest.sh` was
#     rewritten around exactly this lesson; `deploy/actions-runner-restart.conf` even says
#     "constraining backfill memory has to happen at the container level". Nothing had done it.
#  2. **`docker exec` does not forward cancellation.** Actions kills the client on a timeout
#     (#544); the process inside the app container keeps running and keeps eating RAM. An attached
#     `docker run` gets the signal, so the timeout added in #544 now actually stops the work.
#
# The same shape as harvest.sh, with its own slice so a job dispatched during the 12:30–03:00 ET
# harvest window cannot push the harvest's cgroup over and have the kernel kill the night.
set -euo pipefail

usage() { echo "usage: $0 <label> [-e KEY=VALUE …] -- <python args…>" >&2; exit 2; }

[ "$#" -ge 3 ] || usage
LABEL="$1"; shift

# Env passthrough, so data-export can hand the job its SCS_* inputs without this script knowing
# what they are. Values come from the workflow's `env:` block, never interpolated into the script
# by the Actions templater — same injection posture as the callers (#333).
declare -a PASSTHROUGH=()
while [ "$#" -gt 0 ] && [ "$1" != "--" ]; do
  case "$1" in
    -e) [ "$#" -ge 2 ] || usage; PASSTHROUGH+=(-e "$2"); shift 2 ;;
    *) usage ;;
  esac
done
[ "${1:-}" = "--" ] || usage
shift
[ "$#" -ge 1 ] || usage

REPO_DIR="${REPO_DIR:-/opt/small-cap-stack}"
ENV_FILE="${ENV_FILE:-$REPO_DIR/.env}"
DATA_VOLUME="${DATA_VOLUME:-small-cap-stack_scs-data}"
MEM_LIMIT="${BOX_JOB_MEM_LIMIT:-1g}"

# The tag compose is actually running, read off the box's .env the way harvest.sh does.
#
# Not cosmetic. `IMAGE_TAG` is not in the workflow environment, so relying on it there silently
# resolves `:latest` — tip-of-main — while the tracker runs its pinned `sha-<short>`. The backfill
# WRITES /data/dashboard and rebuilds the cross-day book, so that means regenerating the published
# dashboard with different code than the deployed system. The worst shape is a rollback:
# `deploy-backfill-publish` pins an older SHA, then the backfill regenerates everything using the
# code you just rolled back FROM.
IMAGE_TAG="${IMAGE_TAG:-}"
if [ -z "$IMAGE_TAG" ] && [ -f "$ENV_FILE" ]; then
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line#"${line%%[![:space:]]*}"}"
    case "$line" in
      IMAGE_TAG=* | "export IMAGE_TAG="*)
        IMAGE_TAG="${line#*=}"
        IMAGE_TAG="${IMAGE_TAG%\"}"
        IMAGE_TAG="${IMAGE_TAG#\"}"
        IMAGE_TAG="${IMAGE_TAG%\'}"
        IMAGE_TAG="${IMAGE_TAG#\'}"
        ;;
    esac
  done < "$ENV_FILE"
fi
IMAGE="ghcr.io/bennetwi92/small-cap-stack:${IMAGE_TAG:-latest}"

# ------------------------------------------------------------------------------------------------
# The market-hours guard
# ------------------------------------------------------------------------------------------------
# The harvest refuses to start outside 12:30–03:00 ET and recesses at 16:10 so it is never inside a
# session during the 16:20/16:30 EOD jobs (#455). These jobs had no such guard at all, so a phone
# dispatch at 09:45 competed with the live scan window for CPU and page cache on a 2-vCPU box.
#
# Refuses rather than waits: a job that silently sleeps for six hours holds the single self-hosted
# runner, which is the outage shape #544 just bounded.
if [ -z "${BOX_JOB_IGNORE_WINDOW:-}" ]; then
  # No leading-zero hazard: `[ -ge ]` parses base 10 (unlike `$(( ))`, which reads 0930 as octal
  # and errors). Kept as `[ ]` deliberately for that reason.
  now_et="$(TZ=America/New_York date +%H%M)"
  # 04:00–16:10 ET: pre-market open through the harvest's own EOD recess (#455). Outside it the
  # box is idle enough for a job to have the CPU and page cache to itself.
  if [ "$now_et" -ge 400 ] && [ "$now_et" -lt 1610 ]; then
    echo "refusing to run '$LABEL' at ${now_et:0:2}:${now_et:2:2} ET — inside the tracker's" >&2
    echo "session window (04:00-16:10). This competes with the live scan on a 2-vCPU box." >&2
    echo "Re-dispatch after 16:10 ET, or set ignore_window if you accept the contention." >&2
    # A caller that would rather skip this stage than fail the run says so. The one-click
    # deploy→backfill→publish pipeline uses it: before this, dispatching it at 10:00 ET deployed
    # fine, failed the backfill on the window, and then SKIPPED publish (its `if` requires the
    # backfill not to have failed) — leaving a freshly deployed box serving a stale dashboard, on
    # a red run, for a pipeline that worked at any hour before.
    [ -n "${BOX_JOB_SKIP_ON_WINDOW:-}" ] && { echo "skipping '$LABEL' (skip-on-window set)" >&2; exit 0; }
    exit 1
  fi
fi

declare -a CMD=(
  docker run --rm
  # `-i` so a caller can pipe a script in (`box-job.sh export -- -` < file), which is how
  # data-export ships `scripts/analysis/export_query.py` to the box. Harmless otherwise: with no
  # `-t` and stdin closed the process simply reads EOF.
  -i
  # Deliberately NO --name. `harvest.sh` names its container as a vendor-spend mutex and carries a
  # whole stale-container sweep for the consequence: `--rm` is server-side, so a daemon restart or
  # a SIGKILLed client leaves an Exited container holding the name, after which every later run
  # exits 125 in under a second. These jobs need no mutex — there is exactly one self-hosted
  # runner, so they cannot overlap — so taking the name buys a way to brick the job and nothing
  # else. The daemon generates one.
  # The container is created by the DAEMON, so without this it lands in Docker's own scope under
  # system.slice and every limit below applies to the client process instead of the job (#452).
  #
  # Its OWN slice, not the harvest's: sharing would let a job dispatched inside the 12:30-03:00 ET
  # window push the harvest's cgroup over and have the kernel kill the night — the hazard
  # harvest.sh documents for its read-only commands. If the slice unit is not installed on the box
  # the slice is created unbounded and `--memory` below still binds, so this degrades to "no
  # backstop", never to "no limit".
  --cgroup-parent=scs-jobs.slice
  # memory-swap is the COMBINED limit, so equal values mean no swap at all. Letting a background
  # job swap is how the CX23 thrashes past sshd (#264/#320).
  --memory="$MEM_LIMIT"
  --memory-swap="$MEM_LIMIT"
  # Higher than the app's 500, so under host pressure the kernel takes THIS and leaves the tracker.
  --oom-score-adj=800
  --cpus=1
  # Container-side paths: properties of the image and the mount, never of the host's config.
  -e DATA_DIR=/data
  -e DUCKDB_PATH=/data/small_cap_stack.duckdb
  # Blanked for the same reason the harvest never constructs a Heartbeat: a job that dies must not
  # page as a tracker outage.
  -e HEALTHCHECKS_PING_URL=
  ${PASSTHROUGH[@]+"${PASSTHROUGH[@]}"}
  -v "$DATA_VOLUME":/data
  "$IMAGE" python "$@"
)

if [ -n "${BOX_JOB_DRY_RUN:-}" ]; then
  printf '%q ' "${CMD[@]}"; echo
  exit 0
fi
exec "${CMD[@]}"
