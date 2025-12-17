#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ -z "${BIGMODEL_API_KEY:-}" ]]; then
  echo "Missing BIGMODEL_API_KEY in environment." >&2
  echo "Tip: source $REPO_ROOT/.env (do NOT commit secrets) then run: $0" >&2
  exit 2
fi

SMOKE_DIR="$REPO_ROOT/state/_smoke/glm_router_write_file"
mkdir -p "$SMOKE_DIR"

INPUT="$SMOKE_DIR/input.md"
OUT_MD="$SMOKE_DIR/output.md"
OUT_JSON="$SMOKE_DIR/output.json"

cat >"$INPUT" <<'TXT'
# Input

这是一段用于冒烟测试的输入材料：

- 事实：今天是一次 glm_router_write_file 的测试运行。
- 目标：生成一个小型 Markdown 笔记，包含固定标题，并写入到指定文件路径。
TXT

rm -f "$OUT_MD" "$OUT_JSON"

python3 "$REPO_ROOT/mcp-servers/glm_router/server.py" <<EOF
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","clientInfo":{"name":"smoke-test","version":"0.0"},"capabilities":{}}}
{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"glm_router_write_file","arguments":{"expect":"text","family":"text","system":"你是一个严谨的结构化写作助手。","instructions":"请根据输入材料写一份简短笔记，必须包含以下标题（完全一致）：\\n## TL;DR\\n## Facts\\n## Next actions\\n并在 Facts 中用项目符号列出 2-4 条事实，在 Next actions 中列 2-4 条下一步。不要使用代码块。","input_paths":["$INPUT"],"output_path":"$OUT_MD","overwrite":true,"validate":{"must_have_substrings":["## TL;DR","## Facts","## Next actions"],"min_chars":120,"max_chars":2000},"preview_chars":120,"allow_paid":false,"timeout_sec":60,"max_retries":1,"meta":{"case":"write_md"}}}}
{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"glm_router_write_file","arguments":{"expect":"json","family":"text","instructions":"只输出一个 JSON 对象，字段 ok(boolean), sum(number)。其中 sum=1+2+3。","output_path":"$OUT_JSON","overwrite":true,"preview_chars":0,"allow_paid":false,"timeout_sec":60,"max_retries":1,"meta":{"case":"write_json"}}}}
EOF

python3 - <<PY
from __future__ import annotations

import json
from pathlib import Path

out_md = Path("$OUT_MD")
assert out_md.exists(), f"missing: {out_md}"
t = out_md.read_text(encoding="utf-8", errors="replace")
for h in ("## TL;DR", "## Facts", "## Next actions"):
    assert h in t, f"missing heading: {h}"

out_json = Path("$OUT_JSON")
assert out_json.exists(), f"missing: {out_json}"
data = json.loads(out_json.read_text(encoding="utf-8"))
assert data.get("sum") == 6, f"bad sum: {data}"

print("OK: glm_router_write_file")
PY

