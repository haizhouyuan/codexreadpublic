#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DB_PATH="${1:-$REPO_ROOT/state/tasks.smoke.sqlite}"

rm -f "$DB_PATH"

python3 "$REPO_ROOT/mcp-servers/tasks/server.py" --db-path "$DB_PATH" <<'EOF'
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","clientInfo":{"name":"smoke-test","version":"0.0"},"capabilities":{}}}
{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}
{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"create_task","arguments":{"title":"Smoke test task","category":"personal","priority":"high","tags":["smoke"],"topic_id":"demo_topic"}}}
{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"list_tasks","arguments":{"limit":5}}}
EOF

