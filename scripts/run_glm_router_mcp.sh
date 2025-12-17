#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Make path safety deterministic even if launched from other cwd.
export GLM_ROUTER_REPO_ROOT="$REPO_ROOT"

# Load local secrets/config for this repo only (do not commit).
# Expected: BIGMODEL_API_KEY (+ optional BIGMODEL_API_BASE, GLM_ROUTER_*).
ENV_FILE="$REPO_ROOT/.env"
if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ENV_FILE"
  set +a
fi

exec python3 "$REPO_ROOT/mcp-servers/glm_router/server.py" "$@"
