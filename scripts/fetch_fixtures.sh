#!/usr/bin/env bash
# Pull a sanitized sample dataset into data/fixtures/ for offline / cloud (mobile) dev.
#
# Rationale (research/decisions.md — "store raw, compute derived on read"): the VPS is the only host
# with the live IBKR entitlement. It captures raw and pushes a SANITIZED sample to object storage;
# a dev session pulls that sample here and replays the pure gate/bull-flag/stat functions against it
# — no broker connection required.
#
# Location comes from $FIXTURES_URI. Supported forms:
#   https://…/sample.tar.gz   → downloaded + extracted
#   /abs/path or ./rel/path   → copied (a local sample, e.g. an SSHFS/rsync mount)
# If $FIXTURES_URI is unset this is a friendly no-op (the producer side is blocked on #6 / #48).
#
# Network note: pulling over HTTPS needs the Claude Code web-environment network policy to allow
# egress to the object-storage host (see deploy/RUNBOOK.md "Operating from mobile").
set -euo pipefail

DEST="${FIXTURES_DIR:-data/fixtures}"
URI="${FIXTURES_URI:-}"

if [[ -z "$URI" ]]; then
  cat <<'EOF'
fetch-fixtures: FIXTURES_URI is not set — nothing to pull.

  Set it to a sanitized sample location, e.g.:
    export FIXTURES_URI="https://<object-storage-host>/small-cap-stack/sample.tar.gz"
    make fetch-fixtures

  The producer (VPS → object storage push) is tracked in #52 / #48 and needs the VM (#6).
EOF
  exit 0
fi

mkdir -p "$DEST"

case "$URI" in
  https://*|http://*)
    echo "fetch-fixtures: downloading $URI → $DEST/"
    tmp="$(mktemp)"
    trap 'rm -f "$tmp"' EXIT
    curl -fsSL "$URI" -o "$tmp"
    tar -xzf "$tmp" -C "$DEST"
    ;;
  *)
    if [[ -d "$URI" ]]; then
      echo "fetch-fixtures: copying $URI/ → $DEST/"
      cp -a "$URI/." "$DEST/"
    elif [[ -f "$URI" ]]; then
      echo "fetch-fixtures: extracting $URI → $DEST/"
      tar -xzf "$URI" -C "$DEST"
    else
      echo "fetch-fixtures: FIXTURES_URI '$URI' is not an http(s) URL or an existing path." >&2
      exit 1
    fi
    ;;
esac

echo "fetch-fixtures: done. Sample data in $DEST/ (gitignored)."
