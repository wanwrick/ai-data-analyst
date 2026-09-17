#!/usr/bin/env bash
# Build the frontend and deploy to Databricks Apps.
#
#   ./deploy.sh --create     create the app, then deploy
#   ./deploy.sh              deploy to the existing app
set -euo pipefail

cd "$(dirname "$0")"

APP_NAME="${APP_NAME:-ai-data-analyst}"
CREATE=false
[ "${1:-}" = "--create" ] && CREATE=true

say() { printf '\n\033[1m%s\033[0m\n' "$1"; }
fail() { printf '\033[31merror:\033[0m %s\n' "$1" >&2; exit 1; }

command -v databricks >/dev/null 2>&1 \
  || fail "databricks CLI not found. See https://docs.databricks.com/dev-tools/cli/"

say "Verifying authentication"
databricks current-user me >/dev/null 2>&1 \
  || fail "not authenticated. Run: databricks auth login"

say "Building frontend"
(cd frontend && npm run build)
[ -d frontend/dist ] || fail "frontend build produced no dist/ directory"

if [ "$CREATE" = true ]; then
  say "Creating app: $APP_NAME"
  databricks apps create "$APP_NAME" || echo "  app already exists, continuing"
fi

say "Syncing source"
TARGET="/Workspace/Users/$(databricks current-user me --output json | python -c 'import json,sys; print(json.load(sys.stdin)["userName"])')/$APP_NAME"
databricks sync --full . "$TARGET" \
  --exclude '.venv/**' \
  --exclude 'frontend/node_modules/**' \
  --exclude '.git/**' \
  --exclude '.env'

say "Deploying"
databricks apps deploy "$APP_NAME" --source-code-path "$TARGET"

say "Deployed"
databricks apps get "$APP_NAME" --output json | python -c 'import json,sys; print("  url:", json.load(sys.stdin).get("url","(pending)"))'
