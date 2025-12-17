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

have_paid=0
for k in TAVILY_API_KEY tavilyApiKey BIGMODEL_API_KEY; do
  if [[ -n "${!k:-}" ]]; then
    have_paid=1
  fi
done
smoke_paid="${SOURCE_PACK_SMOKE_PAID:-0}"

cat <<'EOF' | "$PY" "$REPO_ROOT/mcp-servers/source_pack/server.py"
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","clientInfo":{"name":"smoke-test","version":"0.0"},"capabilities":{}}}
{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}
{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"source_pack_fetch","arguments":{"url":"https://www.ashrae.org/File%20Library/Technical%20Resources/Bookstore/WhitePaper_TC099-WaterCooledServers.pdf","topic_id":"_smoke","allow_paid":false,"min_chars":5000,"timeout_sec":45}}}
EOF

if [[ "$have_paid" == "1" ]]; then
  cat <<'EOF' | "$PY" "$REPO_ROOT/mcp-servers/source_pack/server.py"
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","clientInfo":{"name":"smoke-test","version":"0.0"},"capabilities":{}}}
{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"source_pack_fetch","arguments":{"url":"https://techcommunity.microsoft.com/blog/azureinfrastructureblog/liquid-cooling-in-air-cooled-data-centers-on-microsoft-azure/4268822","topic_id":"_smoke","allow_paid":true,"min_chars":5000,"timeout_sec":60}}}
EOF
fi

if [[ "$have_paid" == "1" && "$smoke_paid" == "1" ]]; then
  # Optional (may consume quota/paid credits): run each paid-ish fetcher once.
  cat <<'EOF' | "$PY" "$REPO_ROOT/mcp-servers/source_pack/server.py"
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","clientInfo":{"name":"smoke-test","version":"0.0"},"capabilities":{}}}
{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"source_pack_fetch","arguments":{"url":"https://techcommunity.microsoft.com/blog/azureinfrastructureblog/liquid-cooling-in-air-cooled-data-centers-on-microsoft-azure/4268822","topic_id":"_smoke","allow_paid":true,"fetchers":["tavily_extract"],"min_chars":0,"timeout_sec":60}}}
{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"source_pack_fetch","arguments":{"url":"https://techcommunity.microsoft.com/blog/azureinfrastructureblog/liquid-cooling-in-air-cooled-data-centers-on-microsoft-azure/4268822","topic_id":"_smoke","allow_paid":true,"fetchers":["bigmodel_reader"],"min_chars":0,"timeout_sec":60}}}
EOF
fi
