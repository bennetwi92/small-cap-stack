#!/usr/bin/env bash
# Swap file + sysctl for the box (#320). Idempotent — safe to re-run. Run as root.
#
# The CX22 (2 vCPU / 4 GB) had no swap, so any memory spike was an immediate host-wide OOM hunt
# with the kernel picking the victim — proven 2026-07-17, when a global OOM dropped the IBKR
# connection mid-session. 2 GB of swap turns that class of incident from *kill* into *slow*;
# vm.swappiness=10 keeps it an emergency cushion, not a hot path. Both persist across reboot
# (/etc/fstab + /etc/sysctl.d). See deploy/RUNBOOK.md §9 and issue #320.
set -euo pipefail

SWAPFILE=/swapfile
SIZE=2G

if swapon --show=NAME --noheadings | grep -qx "$SWAPFILE"; then
  echo "swap already active: $SWAPFILE"
else
  if [ ! -f "$SWAPFILE" ]; then
    fallocate -l "$SIZE" "$SWAPFILE"
    chmod 600 "$SWAPFILE"
    mkswap "$SWAPFILE"
  fi
  swapon "$SWAPFILE"
fi

grep -q "^$SWAPFILE " /etc/fstab || echo "$SWAPFILE none swap sw 0 0" >>/etc/fstab

cat >/etc/sysctl.d/99-scs-swap.conf <<'SYSCTL'
# Swap is an emergency cushion, not a hot path (#320): strongly prefer reclaiming page cache
# over swapping anonymous pages, so the tracker's latency only degrades under real pressure.
vm.swappiness = 10
SYSCTL
sysctl --system >/dev/null

echo "--- verification ---"
swapon --show
sysctl vm.swappiness
grep "$SWAPFILE" /etc/fstab
