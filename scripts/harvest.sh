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
# The job refuses to start outside 12:30-03:00 ET on its own (harvest_start_et/harvest_stop_et), so
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
# Same idea for the EDGAR contact string (#563), except it is NOT a secret — it is a name and an
# address SEC's fair-access policy asks callers to identify themselves with. So it rides on an
# Actions *variable* rather than a secret, and there is no `install-…` command for it: the nightly
# timer picks it up from .env, a workflow dispatch from here.
AMBIENT_EDGAR_UA="${HARVEST_EDGAR_USER_AGENT:-}"

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
  # `scs-jobs.slice` is the on-demand jobs' envelope (#545). It rides along here so the phone path
  # can install it too — otherwise the only route is SSH, against this repo's whole posture.
  for unit in scs-harvest.slice scs-jobs.slice scs-harvest.service scs-harvest.timer; do
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
  # Start the slice explicitly so its limits are live now, rather than at the next service start.
  # Docker will create the cgroup on its own if this is skipped — but an uninstalled slice has
  # MemoryMax=infinity, which is exactly the silent no-op this change exists to remove.
  systemctl start scs-harvest.slice scs-jobs.slice
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
      # RECON_* rather than RECON_SUBDIR alone: `charts` (#488) also reads RECON_CHARTS_MAX_DATES,
      # and a box that tuned the publish budget in .env must not have the container silently fall
      # back to the default and publish a different window than the operator asked for.
      RECON_* | CALENDAR_CLOSED_DATES | LOG_LEVEL | JSON_LOGS | TZ)
        PASSTHROUGH+=(-e "$key=$value")
        ;;
      # Same ambient-wins rule as the vendor key above, so a dispatch can carry the contact string
      # to a box whose .env has never had one.
      HARVEST_EDGAR_USER_AGENT)
        [ -n "$AMBIENT_EDGAR_UA" ] || PASSTHROUGH+=(-e "$key=$value")
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
if [ -n "$AMBIENT_EDGAR_UA" ]; then
  PASSTHROUGH+=(-e "HARVEST_EDGAR_USER_AGENT=$AMBIENT_EDGAR_UA")
fi

# Same tag compose is pinned to, so the harvest runs the deployed code rather than drifting to
# :latest behind the tracker's back.
IMAGE="ghcr.io/bennetwi92/small-cap-stack:${IMAGE_TAG:-latest}"
# 1 GB hard, swap pinned to the same value (memory-swap is the COMBINED limit, so equal values mean
# no swap at all). The box has 4 GB and the tracker + Gateway want ~3.5 of it; swapping a background
# job is how the CX23 thrashes past sshd (#264).
MEM_LIMIT="${MEM_LIMIT:-1g}"

# ------------------------------------------------------------------------------------------------
# The concurrency lock, and why only the spending commands take it (#455)
# ------------------------------------------------------------------------------------------------
# `--name scs-harvest` IS the lock: `docker run` refuses a name already in use, so the timer and a
# workflow dispatch cannot double-spend a rate-limited budget or race on the checkpoint.
#
# Two refinements now that the timer fires TWICE a day (12:30 and 17:15 ET, RUNBOOK §13):
#
# 1. The second fire is *expected* to find the first still running, and that is success, not
#    failure. Left alone it would exit 125 and leave scs-harvest.service `failed` — which is
#    precisely the signal we look at to tell a broken harvest from a working one. Detect it and
#    exit 0 instead.
# 2. Read-only commands don't take the lock at all. They spend nothing and race on nothing, and
#    being unable to run `status` during a 14-hour harvest is an operational annoyance with no
#    upside. They get a daemon-generated name.
#
# The stale-container sweep handles the leak that would otherwise kill the job silently: `--rm` is
# server-side, so a daemon restart mid-harvest (a deploy, an apt upgrade, a reboot) can leave an
# `Exited` container holding the name — after which every future night exits 125 in under a second,
# with no Restart= and no Healthchecks ping to notice. Only a NON-running container is removed, so
# the lock is never broken by clearing the corpse.
# Scan EVERY argument, not just $1: argparse takes options before the positional, so
# `harvest.sh --limit 1 run` is a valid full phase-2 run. Keying the lock on $1 alone would let
# exactly that invocation spend vendor budget with no lock, no name and no stale sweep, racing the
# timer's own harvest on the checkpoint.
# `charts` (#488) spends no vendor budget and touches no checkpoint, but it read-modify-writes
# /data/dashboard/recon_index.json — and so does the per-session publish hook inside a running
# `run`/`auto`. Interleave the two and one of them writes an index built from a stale snapshot,
# dropping a row and orphaning its payload. This name IS the only cross-process mutex on the box,
# so `charts` takes it too: the lock guards the checkpoint and the dashboard artifacts alike.
# `fundamentals` (#563) spends no vendor budget either — SEC EDGAR is free — but it writes
# `fundamentals` partitions into the recon store, and `run`/`auto` both DELETE those (the dataset is
# in HARVEST_DATASETS, so `discard_partial` clears it before re-harvesting a date). Run the two
# together and the enrichment can land rows for a date whose bars are being rebuilt underneath it.
# It also does real per-date work — a DuckDB read plus an HTTPS call per distinct symbol — so like
# `charts` it takes the lock and the harvest's own memory envelope rather than the read-only one.
SPENDING=""
for arg in "$@"; do
  case "$arg" in auto | daily | run | charts | fundamentals) SPENDING=1 ;; esac
done

declare -a NAME=()
if [ -n "$SPENDING" ]; then
  if [ -z "${HARVEST_DRY_RUN:-}" ]; then
    state="$(docker inspect -f '{{.State.Running}}' scs-harvest 2>/dev/null || true)"
    if [ "$state" = "true" ]; then
      # Exit NON-ZERO. This branch is not reached by the timer — systemd merges a second `start`
      # job into the running one, so ExecStart is never re-executed and the 17:15 fire is a no-op
      # at the systemd layer, not here. The only caller that gets here is a human or the `harvest`
      # workflow, and for them "a harvest was already running so I did nothing" must be a RED run.
      # Exiting 0 would render a phone-dispatched smoke test as a green tick that proved nothing.
      echo "a harvest is already running (container scs-harvest) — refusing to start a second" >&2
      exit 1
    fi
    if [ -n "$state" ]; then
      # `--rm` is server-side, so a daemon restart mid-harvest (a deploy, an apt upgrade, a reboot)
      # can leave an Exited container holding the name — after which every future night exits 125
      # in under a second, with no Restart= and no Healthchecks ping to notice. Only a NON-running
      # container is removed, so clearing the corpse never breaks the lock.
      echo "removing a leaked scs-harvest container (state: not running)" >&2
      docker rm scs-harvest >/dev/null 2>&1 || true
    fi
  fi
  NAME=(--name scs-harvest)
fi

# Read-only commands (status/sweep/prefilter) stay OUT of the harvest's cgroup slice and take a
# smaller cap. They spend no vendor budget and hold no lock, so they may run beside a live harvest
# — but the slice's MemoryMax is only 200 MB above the harvest's own limit and has MemorySwapMax=0,
# so joining it would mean a `sweep` during a 900 MB harvest could push the slice over and have the
# kernel OOM-kill the night it was only meant to look at.
#
# `charts` and `fundamentals` are deliberately NOT in that set. They hold the lock (see above) so
# they can never run beside a harvest, and unlike the other three they do real work — DuckDB +
# polars + the detector over a day's bars, per date. 512 MB with no slice, during market hours, on a
# 4 GB box already carrying the app (2 GB) and the Gateway, is the shape of #264. They get the
# harvest's own envelope instead.
declare -a CGROUP=(--cgroup-parent=scs-harvest.slice)
RO_MEM_LIMIT="512m"
if [ -z "$SPENDING" ]; then
  CGROUP=()
  MEM_LIMIT="$RO_MEM_LIMIT"
fi

# Container-side paths, set here rather than inherited: they are properties of the image and the
# mount, never of the host's config. HEALTHCHECKS_PING_URL is blanked for the same reason the
# harvest never constructs a Heartbeat — a stalled harvest must not page as a tracker outage.
declare -a CMD=(
  docker run --rm
  ${NAME[@]+"${NAME[@]}"}
  # The container is created by the DAEMON, so without this it lands in Docker's own scope under
  # system.slice and every limit on scs-harvest.service applies to this client process instead of
  # to the harvest (#452). Docker here uses the systemd cgroup driver on cgroup v2, so the parent
  # must be a `.slice` — a `system.slice/foo.service` path is rejected. The slice's MemoryMax is
  # what makes a mis-set HARVEST_MEM_LIMIT harmless. Empty for read-only commands (see above).
  ${CGROUP[@]+"${CGROUP[@]}"}
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

# No `nice`/`ionice` prefix: it would deprioritise this shell and the docker client, never the
# container, which is a child of the daemon. CPUWeight/IOWeight on the slice are the real controls.
exec "${CMD[@]}"
