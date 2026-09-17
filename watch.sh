#!/usr/bin/env bash
# Run backend and frontend together with reload. Ctrl-C stops both.
set -euo pipefail

cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  echo "No .venv found. Run ./setup.sh first." >&2
  exit 1
fi

# shellcheck disable=SC1091
if [ -f .venv/bin/activate ]; then source .venv/bin/activate; else source .venv/Scripts/activate; fi
set -a; [ -f .env ] && source .env; set +a

pids=()
cleanup() {
  trap - INT TERM EXIT
  for pid in "${pids[@]:-}"; do
    kill "$pid" 2>/dev/null || true
  done
  wait 2>/dev/null || true
}
trap cleanup INT TERM EXIT

echo "backend  -> http://localhost:8000  (docs at /docs)"
(cd backend && uvicorn app:app --reload --host 0.0.0.0 --port 8000) &
pids+=($!)

echo "frontend -> http://localhost:3000"
(cd frontend && npm run dev -- --host) &
pids+=($!)

wait -n
