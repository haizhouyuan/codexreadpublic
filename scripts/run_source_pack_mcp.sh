#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

export SOURCE_PACK_REPO_ROOT="$REPO_ROOT"

ENV_FILE="$REPO_ROOT/.env"
if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ENV_FILE"
  set +a
fi

PY="$REPO_ROOT/.venv/bin/python"
if [[ ! -x "$PY" ]]; then
  PY="python3"
fi

exec "$PY" "$REPO_ROOT/mcp-servers/source_pack/server.py" "$@"

