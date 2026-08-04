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
#
# `HARVEST_DRY_RUN=1` prints the docker command instead of running it — which is how
# tests/test_harvest_env.py verifies the whole of the below on any machine, with no daemon and no
# box. What used to be "check this by hand on the box before the first night" is now a CI gate.
#
# ## Where the vendor key comes from — and why you never need SSH to place it
#
# An ambient `MASSIVE_API_KEY` in this script's environment WINS over the one in .env. That is what
# lets `.github/workflows/harvest.yml` inject `secrets.MASSIVE_API_KEY` straight into a run, so a
# workflow-triggered harvest works on a box that has never seen the key. The systemd timer fires
# outside GitHub and has no such injection, so for the nightly job the key does have to land in
# .env — which is what `install-key` below is for:
#
#     MASSIVE_API_KEY=… scripts/harvest.sh install-key
#
# The workflow runs exactly that on the box, with the value coming from Actions secrets. Adding
# the secret is the one step only the owner can do (this repo's tooling must never handle the key),
# and it is a normal web form — github.com -> Settings -> Secrets and variables -> Actions — which
# works from a phone browser. No laptop, no SSH.
set -euo pipefail

REPO_DIR="${REPO_DIR:-/opt/small-cap-stack}"
ENV_FILE="${ENV_FILE:-$REPO_DIR/.env}"
DATA_VOLUME="${DATA_VOLUME:-small-cap-stack_scs-data}"
# Where THIS copy of the script lives — the workflow runs the checked-out one, so `install-units`
# installs the units from the same ref you dispatched rather than whatever the box last deployed.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SYSTEMD_DIR="${SYSTEMD_DIR:-/etc/systemd/system}"

# ------------------------------------------------------------------------------------------------
# Read .env ourselves rather than handing it to `docker run --env-file`
# ------------------------------------------------------------------------------------------------
# Two reasons, and the second is the one that bites:
#
# 1. `--env-file` uses Docker's own parser, which is NOT compose's. It has no inline-comment
#    handling and no quote stripping, so `AUTO_RESTART_TIME=23:45 PM  # IBC daily restart` becomes
#    a value with the comment in it. The box's .env is written for compose and is full of those.
# 2. That file is a copy of .env.example, which carries the LOCAL-dev `DATA_DIR=./data`. Passed in
#    wholesale it overrides the image's `/data`, so the entire harvest lands inside the container's
#    working directory and is deleted with it on `--rm` — a night of API budget written to nothing,
#    with a clean exit and an empty store.
#
# So: parse it here (compose's rules), and pass through only what the harvest actually reads. The
# allowlist is also why TWS_PASSWORD and the Healthchecks URL never enter this container — the
# harvest has no business holding the broker credentials, and nothing here should be able to ping
# the tracker's dead-man's switch.
env_value() {
  # Strip a compose-style value: optional quotes, or an unquoted trailing ` # comment`.
  local v="$1"
  v="${v#"${v%%[![:space:]]*}"}" # ltrim
  case "$v" in
    \"*)
      v="${v#\"}"
      v="${v%%\"*}"
      ;;
    \'*)
      v="${v#\'}"
      v="${v%%\'*}"
      ;;
    *)
      v="${v%%[[:space:]]#*}"                 # cut at the first " #"
      v="${v%"${v##*[![:space:]]}"}" ;;       # rtrim
  esac
  printf '%s' "$v"
}

# An ambient key (the workflow's Actions secret) beats the stored one — see the header.
AMBIENT_KEY="${MASSIVE_API_KEY:-}"

# ------------------------------------------------------------------------------------------------
# install-key: put the vendor key where the systemd timer can find it, without SSH
# ------------------------------------------------------------------------------------------------
if [ "${1:-}" = "install-key" ]; then
  if [ -z "$AMBIENT_KEY" ]; then
    echo "install-key: MASSIVE_API_KEY is empty. Add it as a repository Actions secret first" >&2
    echo "  (github.com -> Settings -> Secrets and variables -> Actions), then re-run." >&2
    exit 2
  fi
  touch "$ENV_FILE"
  chmod 600 "$ENV_FILE"
  # Replace rather than append: re-running after a key rotation must not leave the old line behind,
  # where whichever `docker run -e` came last would silently decide which key is live.
  tmp="$(mktemp)"
  grep -v '^[[:space:]]*MASSIVE_API_KEY=' "$ENV_FILE" > "$tmp" || true
  printf 'MASSIVE_API_KEY=%s\n' "$AMBIENT_KEY" >> "$tmp"
  cat "$tmp" > "$ENV_FILE" # preserve the original file's mode/ownership rather than mv'ing over it
  rm -f "$tmp"
  # Never echo the key. Length + last 4 is enough to tell "it landed" from "it landed truncated".
  echo "install-key: wrote MASSIVE_API_KEY to $ENV_FILE (${#AMBIENT_KEY} chars, ends …${AMBIENT_KEY: -4})"
  exit 0
fi

# ------------------------------------------------------------------------------------------------
# install-units: enable the nightly timer, also without SSH
# ------------------------------------------------------------------------------------------------
# The last step that used to need a laptop. Installs the units from THIS checkout, so dispatching a
# ref installs that ref's units. Idempotent — re-running after a unit changes is the upgrade path.
if [ "${1:-}" = "install-units" ]; then
  units_src="$(dirname "$SCRIPT_DIR")/deploy"
  for unit in scs-harvest.service scs-harvest.timer; do
    if [ ! -f "$units_src/$unit" ]; then
      echo "install-units: $units_src/$unit not found" >&2
      exit 2
    fi
    install -m 644 "$units_src/$unit" "$SYSTEMD_DIR/$unit"
    echo "install-units: installed $SYSTEMD_DIR/$unit"
  done
  if [ -n "${HARVEST_DRY_RUN:-}" ]; then
    echo "install-units: dry run — not touching systemd"
    exit 0
  fi
  systemctl daemon-reload
  systemctl enable --now scs-harvest.timer
  # The timer is the thing to verify, not the service: a service that never fires is the failure
  # mode, and `is-enabled` on a oneshot service says nothing about whether it is scheduled.
  systemctl list-timers scs-harvest.timer --no-pager || true
  exit 0
fi

declare -a PASSTHROUGH=()
IMAGE_TAG=""
MEM_LIMIT="${HARVEST_MEM_LIMIT:-}"

if [ -f "$ENV_FILE" ]; then
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line#"${line%%[![:space:]]*}"}"
    case "$line" in '' | '#'*) continue ;; esac
    case "$line" in *=*) ;; *) continue ;; esac
    key="${line%%=*}"
    key="${key#export }"
    key="$(printf '%s' "$key" | tr -d '[:space:]')"
    value="$(env_value "${line#*=}")"
    case "$key" in
      # Read by this script, not passed to the container.
      IMAGE_TAG) IMAGE_TAG="$value" ;;
      HARVEST_MEM_LIMIT) [ -n "$MEM_LIMIT" ] || MEM_LIMIT="$value" ;;
      # Read by the harvest itself. SCAN_* is here because the reconstruction applies the live
      # scanner's gates: if the tracker's universe is ever retuned, a harvest still using the
      # defaults would rebuild a different universe and nothing would say so.
      # An ambient key (injected by the workflow from Actions secrets) wins over the stored one, so
      # a workflow-triggered run works on a box where install-key has never been run.
      MASSIVE_API_KEY)
        [ -n "$AMBIENT_KEY" ] || PASSTHROUGH+=(-e "$key=$value")
        ;;
      RECON_SUBDIR | CALENDAR_CLOSED_DATES | LOG_LEVEL | JSON_LOGS | TZ)
        PASSTHROUGH+=(-e "$key=$value")
        ;;
      HARVEST_* | SCAN_*) PASSTHROUGH+=(-e "$key=$value") ;;
      *) : ;; # everything else — broker credentials included — stays out of this container
    esac
  done < "$ENV_FILE"
fi

# Appended last so it wins: the .env branch above skips its own passthrough when this is set.
if [ -n "$AMBIENT_KEY" ]; then
  PASSTHROUGH+=(-e "MASSIVE_API_KEY=$AMBIENT_KEY")
fi

# Same tag compose is pinned to, so the harvest runs the deployed code rather than drifting to
# :latest behind the tracker's back.
IMAGE="ghcr.io/bennetwi92/small-cap-stack:${IMAGE_TAG:-latest}"
# 1 GB hard, swap pinned to the same value (memory-swap is the COMBINED limit, so equal values mean
# no swap at all). The box has 4 GB and the tracker + Gateway want ~3.5 of it; swapping a background
# job is how the CX23 thrashes past sshd (#264).
MEM_LIMIT="${MEM_LIMIT:-1g}"

# Container-side paths, set here rather than inherited: they are properties of the image and the
# mount, never of the host's config. HEALTHCHECKS_PING_URL is blanked for the same reason the
# harvest never constructs a Heartbeat — a stalled harvest must not page as a tracker outage.
declare -a CMD=(
  docker run --rm
  --name scs-harvest
  --memory="$MEM_LIMIT"
  --memory-swap="$MEM_LIMIT"
  --oom-score-adj=800
  --cpus=1
  ${PASSTHROUGH[@]+"${PASSTHROUGH[@]}"}
  -e DATA_DIR=/data
  -e DUCKDB_PATH=/data/small_cap_stack.duckdb
  -e HEALTHCHECKS_PING_URL=
  -v "$DATA_VOLUME":/data
  "$IMAGE"
  python -m small_cap_stack.harvest "$@"
)

if [ -n "${HARVEST_DRY_RUN:-}" ]; then
  printf '%s\n' "${CMD[@]}"
  exit 0
fi

exec nice -n 19 ionice -c 3 "${CMD[@]}"
