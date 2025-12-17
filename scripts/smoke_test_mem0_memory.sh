#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${MEM0_MCP_PYTHON:-python3}"

truthy() {
  local v="${1:-}"
  v="$(echo "$v" | tr '[:upper:]' '[:lower:]')"
  [[ "$v" == "1" || "$v" == "true" || "$v" == "yes" || "$v" == "y" || "$v" == "on" ]]
}

if ! truthy "${MEM0_ENABLED:-}"; then
  echo "SKIP: MEM0_ENABLED is not true (set MEM0_ENABLED=true to run this smoke test)." >&2
  exit 0
fi

if ! "$PYTHON_BIN" -c 'from mem0 import Memory' >/dev/null 2>&1; then
  echo "SKIP: mem0 python package not available in: $PYTHON_BIN" >&2
  exit 0
fi

output="$(
  "$PYTHON_BIN" "$REPO_ROOT/mcp-servers/mem0_memory/server.py" <<'EOF'
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","clientInfo":{"name":"smoke-test","version":"0.0"},"capabilities":{}}}
{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}
{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"add_memory","arguments":{"user_id":"mem0_smoke_user","kind":"reflection","topic":"mem0_smoke","content":"我喜欢恐龙和乐高积木。","source":"mem0_smoke_test","tags":["smoke"]}}}
{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"search_memory","arguments":{"user_id":"mem0_smoke_user","query":"我喜欢什么玩具？","topic":"mem0_smoke","k":5}}}
EOF
)"

echo "$output"

if echo "$output" | grep -q '"error"'; then
  echo "FAIL: mem0-memory MCP returned an error response." >&2
  exit 1
fi

echo "OK: mem0-memory smoke test completed."

