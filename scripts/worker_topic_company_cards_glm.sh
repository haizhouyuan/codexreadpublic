#!/usr/bin/env bash
set -Eeuo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

worker_id="${ORCH_WORKER_ID:-0}"
topic_id="${ORCH_TOPIC_ID:-}"
topic_title="${ORCH_TOPIC_TITLE:-}"
tag="${ORCH_TAG:-company_cards}"
allow_paid="${ORCH_ALLOW_PAID:-0}"
record_path="${ORCH_RECORD_PATH:-}"
company_limit="${ORCH_COMPANY_LIMIT:-10}"

if [[ -z "$topic_id" ]]; then
  echo "Missing ORCH_TOPIC_ID" >&2
  exit 2
fi
if [[ -z "$topic_title" ]]; then
  echo "Missing ORCH_TOPIC_TITLE" >&2
  exit 2
fi

state_dir="state/tmux_orch/workers/$worker_id"
mkdir -p "$state_dir"
status_file="$state_dir/status.json"

topic_dir="archives/topics/$topic_id"
if [[ ! -d "$topic_dir" ]]; then
  echo "Missing topic dir: $topic_dir" >&2
  exit 2
fi
mkdir -p "$topic_dir/notes/runs" "$topic_dir/companies"

if [[ -z "$record_path" ]]; then
  run_id="$(date +%Y-%m-%d_%H%M 2>/dev/null || date +%Y%m%d_%H%M)"
  record_path="$topic_dir/notes/runs/${run_id}_${tag}.md"
fi
run_id="$(basename "$record_path" .md)"

ts_start="$(date -Is 2>/dev/null || date)"
python3 - <<PY
import json
from pathlib import Path
Path("$status_file").write_text(json.dumps({
  "worker_id": int("$worker_id"),
  "status": "running",
  "topic_id": "$topic_id",
  "topic_title": "$topic_title",
  "tag": "$tag",
  "record_path": "$record_path",
  "ts": "$ts_start",
}, ensure_ascii=False), encoding="utf-8")
PY

python3 scripts/topic_run_state.py status \
  --topic-id "$topic_id" \
  --topic-title "$topic_title" \
  --run-id "$run_id" \
  --stage "$tag" \
  --state running \
  --worker-id "$worker_id" \
  --record-path "$record_path" || true

on_fail() {
  local exit_code="$?"
  local lineno="${1:-}"
  set +e
  local ts_fail
  ts_fail="$(date -Is 2>/dev/null || date)"
  local err_stamp
  err_stamp="$(date +%Y%m%d_%H%M%S 2>/dev/null || date +%Y%m%d_%H%M%S)"
  local err_dir="$state_dir/errors"
  mkdir -p "$err_dir"
  local err_file="$err_dir/${run_id}_${tag}_${err_stamp}.txt"

  {
    echo "ts: $ts_fail"
    echo "worker_id: $worker_id"
    echo "topic_id: $topic_id"
    echo "topic_title: $topic_title"
    echo "tag: $tag"
    echo "run_id: $run_id"
    echo "record_path: $record_path"
    echo "exit_code: $exit_code"
    echo "line: $lineno"
    echo "command: ${BASH_COMMAND:-}"
    echo "pwd: $(pwd)"
    echo
  } >"$err_file"

  python3 scripts/topic_run_state.py status \
    --topic-id "$topic_id" \
    --topic-title "$topic_title" \
    --run-id "$run_id" \
    --stage "$tag" \
    --state failed \
    --worker-id "$worker_id" \
    --record-path "$record_path" \
    --error-path "$err_file" || true
}
trap 'on_fail ${LINENO}' ERR

allow_paid_flag=""
if [[ "$allow_paid" == "1" ]]; then
  allow_paid_flag="--allow-paid"
fi

pool_json="$state_dir/runs/${run_id}_${tag}_company_pool.json"
mkdir -p "$state_dir/runs"
python3 scripts/topic_investing_parse.py --topic-id "$topic_id" --out "$pool_json" >/dev/null

cards_dir="$topic_dir/companies"
mkdir -p "$cards_dir"

run_stamp="$(date +%Y-%m-%d_%H%M%S 2>/dev/null || date +%Y%m%d_%H%M%S)"
run_state_dir="$state_dir/runs/${run_stamp}_${tag}"
mkdir -p "$run_state_dir"

cards_result_json="$run_state_dir/company_cards_results.json"
allow_paid_flag=""
if [[ "$allow_paid" == "1" ]]; then
  allow_paid_flag="--allow-paid"
fi

python3 scripts/topic_generate_company_cards.py \
  --topic-id "$topic_id" \
  --topic-title "$topic_title" \
  --limit "$company_limit" \
  $allow_paid_flag \
  --out "$cards_result_json" >/dev/null

ok_cards="$(python3 - "$cards_result_json" <<'PY'
import json
import sys
from pathlib import Path

p = Path(sys.argv[1])
try:
    data = json.loads(p.read_text(encoding="utf-8"))
except Exception:
    print("0")
    sys.exit(0)
stats = data.get("stats") or {}
print(int(stats.get("ok") or 0))
PY
)"

stage_state="done"
if [[ "${ok_cards}" == "0" ]]; then
  stage_state="failed"
elif [[ "${ok_cards}" -lt "${company_limit}" ]]; then
  stage_state="partial"
fi

audit_prompt="$run_state_dir/chatgpt_company_cards_audit_prompt.txt"
chatgpt_audit_md="$run_state_dir/chatgpt_company_cards_audit.md"
if [[ "${ok_cards}" -ge 1 ]]; then
  python3 - "$cards_result_json" "$topic_id" "$topic_title" >"$audit_prompt" <<'PY'
import json
import sys
from pathlib import Path

res_path = Path(sys.argv[1])
topic_id = sys.argv[2]
topic_title = sys.argv[3]

data = json.loads(res_path.read_text(encoding="utf-8"))
items = [r for r in (data.get("results") or []) if r.get("ok")]
items = items[:2]

snippets = []
for it in items:
    p = Path(it.get("output_path") or "")
    if not p.exists():
        continue
    text = p.read_text(encoding="utf-8", errors="replace")
    snippets.append(f"\n\n---\nFILE: {p}\n---\n{text}\n")

prompt = (
    f"你是资深投研负责人/审计员。请对下面 2 份 Company Card 做审计：\n\n"
    f"1) 是否明显编造数字/事实（若有请指出具体段落）；\n"
    f"2) 是否缺关键结构（表格/结论/监控 KPI）；\n"
    f"3) 这两家公司在本 topic（{topic_id} {topic_title}）里的投资映射是否合理；\n"
    f"4) 给出下一步最重要的核验任务（5-10 条）。\n\n"
    f"请输出 Markdown（短而有用）。\n\n"
    + "".join(snippets)
)
sys.stdout.write(prompt)
PY

  chatgpt_cmd=(
    python3 scripts/chatgpt_mcp_ask.py
    --tool "chatgpt_web_ask_pro_extended"
    --question-file "$audit_prompt"
    --timeout-seconds 1800
    --out "$chatgpt_audit_md"
  )
  if command -v timeout >/dev/null 2>&1; then
    timeout 2100 "${chatgpt_cmd[@]}" >/dev/null || true
  else
    "${chatgpt_cmd[@]}" >/dev/null || true
  fi
fi

python3 - "$record_path" "$cards_result_json" "$chatgpt_audit_md" "$topic_id" "$topic_title" "$tag" "$ts_start" "$worker_id" <<'PY'
import json
import sys
from pathlib import Path

record = Path(sys.argv[1])
cards_json = Path(sys.argv[2])
audit_md = Path(sys.argv[3])
topic_id = sys.argv[4]
topic_title = sys.argv[5]
tag = sys.argv[6]
ts_start = sys.argv[7]
worker_id = int(sys.argv[8])

record.parent.mkdir(parents=True, exist_ok=True)
data = json.loads(cards_json.read_text(encoding="utf-8")) if cards_json.exists() else {}
results = data.get("results") or []
ok = [r for r in results if r.get("ok")]
bad = [r for r in results if not r.get("ok")]

lines = []
lines.append(f"# {topic_id} — {tag} run")
lines.append("")
lines.append(f"- ts: {ts_start}")
lines.append(f"- worker_id: {worker_id}")
lines.append(f"- topic_title: {topic_title}")
lines.append("")
lines.append("## Company cards")
lines.append(f"- ok: {len(ok)}")
lines.append(f"- failed: {len(bad)}")
for r in ok[:10]:
    lines.append(f"- {r.get('ticker') or ''} {r.get('company')}: {r.get('output_path')}")
for r in bad[:5]:
    err = (r.get("error") or "").replace("\n", " ").replace("\r", " ")
    lines.append(f"- FAIL {r.get('ticker') or ''} {r.get('company')}: {err[:160]}")
if audit_md.exists():
    lines.append("")
    lines.append("## ChatGPT audit")
    lines.append(f"- path: {audit_md}")

record.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
print(str(record))
PY

python3 scripts/topic_run_state.py manifest \
  --topic-id "$topic_id" \
  --topic-title "$topic_title" \
  --run-id "$run_id" \
  --stage "$tag" \
  --stage-state "$stage_state" \
  --worker-id "$worker_id" \
  --record-path "$record_path" \
  --artifact "company_cards=$cards_result_json" || true

python3 scripts/topic_run_state.py status \
  --topic-id "$topic_id" \
  --topic-title "$topic_title" \
  --run-id "$run_id" \
  --stage "$tag" \
  --state "$stage_state" \
  --worker-id "$worker_id" \
  --record-path "$record_path" || true

ts_done="$(date -Is 2>/dev/null || date)"
python3 - <<PY
import json
from pathlib import Path
Path("$status_file").write_text(json.dumps({
  "worker_id": int("$worker_id"),
  "status": "done",
  "topic_id": "$topic_id",
  "topic_title": "$topic_title",
  "tag": "$tag",
  "record_path": "$record_path",
  "ts": "$ts_done",
}, ensure_ascii=False), encoding="utf-8")
PY

bash scripts/tmux_notify_controller_done.sh --topic "$topic_id" --record "$record_path" --status "$stage_state" || true

echo "[worker] DONE topic=$topic_id record=$record_path"
