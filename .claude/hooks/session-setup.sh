#!/usr/bin/env bash
# SessionStart hook: make a fresh Claude Code (web/mobile) container productive on turn one.
#
# The cloud container is ephemeral and re-clones the repo each session, so `.venv` is missing
# on start. This ensures `make check` (ruff + mypy + pytest) works without a manual `make setup`.
# Idempotent: rebuilds the venv only when it's absent or when dependencies have changed.
# Never blocks the session — on failure it warns and exits 0.
set -uo pipefail

# Resolve the repo root from this script's location (hooks run with cwd = project dir, but be safe).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT" || { echo "session-setup: cannot cd to repo root" >&2; exit 0; }

VENV="${VENV:-.venv}"
STAMP="$VENV/.deps-stamp"

# Hash the dependency declaration so a changed pyproject triggers a reinstall.
deps_hash() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum pyproject.toml | awk '{print $1}'
  else
    shasum -a 256 pyproject.toml | awk '{print $1}'
  fi
}

want="$(deps_hash 2>/dev/null || echo unknown)"

if [[ -x "$VENV/bin/python" && -f "$STAMP" && "$(cat "$STAMP" 2>/dev/null)" == "$want" ]]; then
  echo "session-setup: .venv present and dependencies unchanged — ready (make check)."
  exit 0
fi

echo "session-setup: provisioning .venv (make setup)…" >&2
if make setup >/tmp/session-setup.log 2>&1; then
  echo "$want" > "$STAMP"
  echo "session-setup: .venv ready — run 'make check' for lint + types + tests."
else
  echo "session-setup: 'make setup' failed; see /tmp/session-setup.log. Continuing anyway." >&2
fi
exit 0
