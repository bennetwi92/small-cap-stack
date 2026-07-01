#!/usr/bin/env bash
# Off-box backup of the scs-data volume to Backblaze B2 via restic (#48).
#
# Incremental + encrypted + deduplicated: each run uploads only new Parquet partitions,
# prunes per the retention policy, and verifies the repo. Config is read from a root-only
# env file (default /etc/scs-backup.env) — see deploy/scs-backup.env.example. Pings a
# Healthchecks.io check on start / success / failure so a silent backup stall is alerted.
#
# Run by the scs-backup.timer nightly, or manually: sudo scripts/backup.sh
set -euo pipefail

ENV_FILE="${SCS_BACKUP_ENV:-/etc/scs-backup.env}"
if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a
fi

# restic reads RESTIC_REPOSITORY / RESTIC_PASSWORD / B2_ACCOUNT_ID / B2_ACCOUNT_KEY from env.
DATA_PATH="${DATA_PATH:-/var/lib/docker/volumes/small-cap-stack_scs-data/_data}"
HC="${HEALTHCHECKS_BACKUP_URL:-}"

# systemd oneshot units don't set $HOME, so restic can't find its default cache — pin it.
export RESTIC_CACHE_DIR="${RESTIC_CACHE_DIR:-/var/cache/restic}"
mkdir -p "$RESTIC_CACHE_DIR"

hc() { [ -n "$HC" ] && curl -fsS -m 10 --retry 3 "${HC}${1:-}" -o /dev/null 2>/dev/null || true; }

trap 'hc /fail' ERR
hc /start

if [ ! -d "$DATA_PATH" ]; then
  echo "data path not found: $DATA_PATH" >&2
  exit 1
fi

restic unlock || true  # clear a stale lock from a previously-killed run (single timer, safe)
restic backup "$DATA_PATH" --tag scs-data --host small-cap-stack
restic forget --tag scs-data --keep-daily 7 --keep-weekly 5 --keep-monthly 4 --prune
restic check  # structural integrity (no data egress)

hc  # success
echo "backup complete: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
