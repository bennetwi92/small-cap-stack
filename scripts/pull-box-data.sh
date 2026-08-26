#!/usr/bin/env bash
# Pull the box's Parquet store down to the Mac's analysis layout (#694).
#
#     scripts/pull-box-data.sh            # pull
#     scripts/pull-box-data.sh --dry-run  # show what rsync would transfer, change nothing
#
# ## Why this exists
#
# The spike harnesses replay the whole collected record — `spikes/regime_panel.py` walks every
# session in both stores. The Mac's copy is whatever an earlier session happened to leave behind,
# and it silently lags: at the time of writing it held 51 recon + 30 live sessions against the
# box's 166 + 35. Rebuilding a panel against it does not fail, it just quietly analyses a third of
# the record. So the pull is a script with a partition count at the end, not a remembered rsync.
#
# ## The layout flip, which is the whole reason a script beats a one-liner
#
# The two roots are arranged DIFFERENTLY on the two machines, and getting it wrong silently mixes
# vendor-reconstructed rows into the live store (the thing #430 split them up to prevent):
#
#     box   /data/{opportunities,bars,...}        <- live
#           /data/recon/{opportunities,bars,...}  <- recon, NESTED inside live's root
#
#     Mac   data/live/{opportunities,bars,...}    <- live
#           data/recon/{opportunities,bars,...}   <- recon, a SIBLING of live
#
# So live cannot be pulled with a plain recursive copy of `/data`: that would drag `recon/` along
# inside it. Each dataset is therefore named and pulled individually, which also keeps the transfer
# to the ~73 MB the harnesses actually read instead of the volume's 181 MB.
#
# ## What is deliberately NOT pulled
#
# `dashboard/` (80 MB of published payloads — regenerable, and the dashboard serves them anyway),
# `cache/`, `reports/`, `bars_1m/` and `daily_universe/`. The last two are real harvest datasets but
# nothing on the analysis path reads them: the detector is 5-minute bars, and `daily_universe` is
# the harvest's own prefilter input. Add them here if that ever stops being true.
#
# ## Load on the box
#
# This is a read of ~73 MB over ssh and nothing else — no container, no Python, no store query. It
# is safe to run while the tracker is up, which is not true of most things in deploy/RUNBOOK.md.
set -euo pipefail

RSYNC_FLAGS=(-a)
DRY_RUN=""
case "${1:-}" in
  --dry-run) RSYNC_FLAGS+=(--dry-run); DRY_RUN=1 ;;
  "") ;;
  *) echo "usage: $0 [--dry-run]" >&2; exit 2 ;;
esac

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOST_DOC="$REPO_ROOT/deploy/host.local.md"
SSH_KEY="${SCS_BOX_KEY:-$HOME/.ssh/oracle_scs}"

# The IP is not in the repo — deploy/host.local.md is gitignored precisely so it isn't. Take it
# from the environment, else read it out of that file, else say exactly what to do about it.
HOST="${SCS_BOX_HOST:-}"
if [[ -z "$HOST" && -r "$HOST_DOC" ]]; then
  # shellcheck disable=SC2016  # the backticks are markdown in the file being read, not a subshell
  HOST="$(sed -n 's/^| *Public IPv4 *| *`\([0-9.]*\)` *|.*/\1/p' "$HOST_DOC" | head -1)"
fi
if [[ -z "$HOST" ]]; then
  echo "error: no box host. Set SCS_BOX_HOST=<ip>, or restore deploy/host.local.md" >&2
  echo "       (gitignored; see CLAUDE.md 'Box access')." >&2
  exit 1
fi
if [[ ! -r "$SSH_KEY" ]]; then
  echo "error: ssh key not readable at $SSH_KEY (override with SCS_BOX_KEY)" >&2
  exit 1
fi

# The Docker volume's host-side path. `docker volume inspect` would be more robust than hard-coding
# it, but that is a second round trip for a name that has not changed since the stack was created;
# if it ever does, this is the line to fix:
#   ssh root@<host> docker inspect small-cap-stack-app-1 --format '{{json .Mounts}}'
BOX_DATA="/var/lib/docker/volumes/small-cap-stack_scs-data/_data"

# The four datasets `spikes/regime_panel.py` reads. `fundamentals` is absent from the box's recon
# root until `harvest fundamentals` has run (RUNBOOK §13.2) — rsync of a missing source is an error,
# so each one is probed before it is pulled rather than assumed.
DATASETS=(opportunities bars scanner_hits fundamentals)

SSH_CMD=(ssh -o BatchMode=yes -o ConnectTimeout=15 -i "$SSH_KEY")

pull() {  # pull <remote-dir> <local-dir> <label>
  local remote="$1" local_dir="$2" label="$3"
  if ! "${SSH_CMD[@]}" "root@$HOST" "test -d '$remote'"; then
    echo "  skip $label — not on the box yet"
    return 0
  fi
  mkdir -p "$local_dir"
  # -a preserves times so a re-run is incremental; --delete is NOT used, on purpose: a dataset the
  # box has compacted away is still worth keeping locally, and a half-configured HOST must never be
  # able to empty the Mac's copy.
  rsync "${RSYNC_FLAGS[@]}" -e "${SSH_CMD[*]}" "root@$HOST:$remote/" "$local_dir/"
  echo "  ok   $label"
}

echo "box:  root@$HOST:$BOX_DATA"
echo "into: $REPO_ROOT/data/{live,recon}"
[[ -n "$DRY_RUN" ]] && echo "(dry run — nothing will be written)"
echo

echo "live:"
for ds in "${DATASETS[@]}"; do
  pull "$BOX_DATA/$ds" "$REPO_ROOT/data/live/$ds" "$ds"
done

echo "recon:"
for ds in "${DATASETS[@]}"; do
  pull "$BOX_DATA/recon/$ds" "$REPO_ROOT/data/recon/$ds" "$ds"
done

# The point of the script: say how many sessions landed, so a partial pull is visible rather than
# discovered three steps later as a panel that is quietly missing two thirds of the record.
echo
echo "partitions now local (dt=… directories):"
for root in live recon; do
  for ds in "${DATASETS[@]}"; do
    dir="$REPO_ROOT/data/$root/$ds"
    n=0
    [[ -d "$dir" ]] && n="$(find "$dir" -maxdepth 1 -name 'dt=*' -type d | wc -l | tr -d ' ')"
    printf '  %-6s %-14s %s\n' "$root" "$ds" "$n"
  done
done
