#!/usr/bin/env bash
# One-time setup: dependencies, environment file, and a connectivity check.
set -euo pipefail

cd "$(dirname "$0")"

say() { printf '\n\033[1m%s\033[0m\n' "$1"; }
fail() { printf '\033[31merror:\033[0m %s\n' "$1" >&2; exit 1; }

say "Checking prerequisites"
command -v python3 >/dev/null 2>&1 || command -v python >/dev/null 2>&1 \
  || fail "python 3.11+ is required"
command -v npm >/dev/null 2>&1 || fail "node 18+ and npm are required"
PYTHON=$(command -v python3 || command -v python)
"$PYTHON" - <<'PY' || fail "python 3.11 or newer is required"
import sys
raise SystemExit(0 if sys.version_info >= (3, 11) else 1)
PY
echo "  python: $("$PYTHON" --version)"
echo "  node:   $(node --version)"

say "Installing backend dependencies"
"$PYTHON" -m venv .venv 2>/dev/null || true
# shellcheck disable=SC1091
if [ -f .venv/bin/activate ]; then source .venv/bin/activate; else source .venv/Scripts/activate; fi
pip install --quiet --upgrade pip
pip install --quiet -r backend/requirements.txt
echo "  done"

say "Installing frontend dependencies"
(cd frontend && npm install --silent)
echo "  done"

say "Configuring environment"
if [ -f .env ]; then
  echo "  .env already exists, leaving it alone"
else
  cp config/.env.example .env
  echo "  wrote .env from config/.env.example"
  echo "  Fill in DATABRICKS_HOST, DATABRICKS_TOKEN and ANTHROPIC_API_KEY before running."
fi

say "Checking Databricks connectivity"
set +e
# shellcheck disable=SC1091
set -a; [ -f .env ] && source .env; set +a
"$PYTHON" - <<'PY'
import os, sys
host = os.getenv("DATABRICKS_HOST", "")
if not host or "your-workspace" in host:
    print("  skipped: DATABRICKS_HOST is not set yet")
    sys.exit(0)
try:
    from databricks.sdk import WorkspaceClient
    warehouses = list(WorkspaceClient().warehouses.list())
    print(f"  connected: {len(warehouses)} SQL warehouse(s) visible")
except Exception as exc:
    print(f"  could not connect: {exc}")
PY
set -e

say "Setup complete"
echo "Next: ./watch.sh    (backend on :8000, frontend on :3000)"
