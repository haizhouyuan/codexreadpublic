#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ -z "${BIGMODEL_API_KEY:-}" ]]; then
  echo "Missing BIGMODEL_API_KEY in environment." >&2
  echo "Tip: source $REPO_ROOT/.env (do NOT commit secrets) then run: $0" >&2
  exit 2
fi

python3 "$REPO_ROOT/mcp-servers/glm_router/server.py" <<'EOF'
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","clientInfo":{"name":"smoke-test","version":"0.0"},"capabilities":{}}}
{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}
{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"glm_router_chat","arguments":{"expect":"json","family":"text","system":"你是一个严格的 JSON 生成器。只输出 JSON，不要 Markdown。","user":"只输出一个 JSON 对象，字段 ok(boolean), sum(number)。其中 sum=1+2+3。","allow_paid":false,"timeout_sec":60,"meta":{"case":"text_json"}}}}
{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"glm_router_chat","arguments":{"expect":"json","family":"vision","system":"你是一个严格的 JSON 生成器。只输出合法 JSON，不要 Markdown。所有 key 必须用双引号。所有字符串值必须用双引号。布尔值只能是 true/false。","user":"只输出一个 JSON 对象，严格遵守 JSON 语法：{\\n  \\\"ok\\\": true,\\n  \\\"objects\\\": \\\"...\\\"\\n}\\nobjects 用逗号分隔列出你看到的关键对象。","image_url":"https://cloudcovert-1305175928.cos.ap-guangzhou.myqcloud.com/%E5%9B%BE%E7%89%87grounding.PNG","allow_paid":false,"timeout_sec":60,"meta":{"case":"vision_json"}}}}
EOF
