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

have_any_key=0
for k in BRAVE_API_KEY braveapikey TAVILY_API_KEY tavilyApiKey TONGXIAO_API_KEY BIGMODEL_API_KEY DASHSCOPE_API_KEY WEBSEARCH_API_KEY; do
  if [[ -n "${!k:-}" ]]; then
    have_any_key=1
  fi
done

if [[ "$have_any_key" != "1" ]]; then
  echo "SKIP: no websearch keys in env/.env"
  exit 0
fi

python3 "$REPO_ROOT/mcp-servers/websearch_router/server.py" <<'EOF'
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","clientInfo":{"name":"smoke-test","version":"0.0"},"capabilities":{}}}
{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}
{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"websearch_router_search","arguments":{"query":"ASHRAE TC 9.9 water-cooled servers whitepaper PDF","max_results":5,"min_results":5,"allow_paid":false,"timeout_sec":30}}}
{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"websearch_router_search","arguments":{"query":"数据中心 液冷 白皮书 ASHRAE TC 9.9","max_results":5,"min_results":5,"allow_paid":false,"timeout_sec":30}}}
EOF
