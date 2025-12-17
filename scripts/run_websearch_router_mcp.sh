#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

export WEBSEARCH_ROUTER_REPO_ROOT="$REPO_ROOT"

ENV_FILE="$REPO_ROOT/.env"
if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ENV_FILE"
  set +a
fi

exec python3 "$REPO_ROOT/mcp-servers/websearch_router/server.py" "$@"

