#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

if ! command -v tmux >/dev/null 2>&1; then
  echo "Missing tmux in PATH." >&2
  exit 2
fi

prefix="orchsmoke_${$}"
session="${prefix}-0"

cleanup() {
  tmux kill-session -t "$session" >/dev/null 2>&1 || true
}
trap cleanup EXIT

TMUX_ORCH_REPO_ROOT="$REPO_ROOT" python3 "$REPO_ROOT/mcp-servers/tmux_orchestrator/server.py" <<EOF
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","clientInfo":{"name":"smoke-test","version":"0.0"},"capabilities":{}}}
{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"ensure_workers","arguments":{"n":1,"session_prefix":"$prefix"}}}
{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"tail_worker","arguments":{"worker_id":0,"lines":5,"session_prefix":"$prefix"}}}
{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"get_worker_status","arguments":{"worker_id":0}}}
EOF

tmux has-session -t "$session" >/dev/null 2>&1
echo "OK: tmux_orchestrator (ensure/tail/status)"

