#!/usr/bin/env bash
set -Eeuo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

worker_id="${ORCH_WORKER_ID:-0}"
topic_id="${ORCH_TOPIC_ID:-}"
topic_title="${ORCH_TOPIC_TITLE:-}"
tag="${ORCH_TAG:-investing}"
allow_paid="${ORCH_ALLOW_PAID:-0}"
record_path="${ORCH_RECORD_PATH:-}"

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
  python3 scripts/new_topic.py "$topic_id"
fi
mkdir -p "$topic_dir/notes/runs" "$topic_dir/companies" "$topic_dir/digests"

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
    if [[ -n "$record_path" && -f "$record_path" ]]; then
      echo "---- record tail (last 80 lines) ----"
      tail -n 80 "$record_path" || true
      echo
    fi
  } >"$err_file"

  python3 - "$status_file" "$worker_id" "$topic_id" "$topic_title" "$tag" "$record_path" "$err_file" "$ts_fail" "$exit_code" "$lineno" "${BASH_COMMAND:-}" <<'PY'
import json
import sys
from pathlib import Path

(
    status_file,
    worker_id,
    topic_id,
    topic_title,
    tag,
    record_path,
    error_path,
    ts_fail,
    exit_code,
    lineno,
    command,
) = sys.argv[1:12]

payload = {
    "worker_id": int(worker_id),
    "status": "failed",
    "topic_id": topic_id,
    "topic_title": topic_title,
    "tag": tag,
    "record_path": record_path,
    "error_path": error_path,
    "error": f"exit_code={exit_code} line={lineno} cmd={command}",
    "ts": ts_fail,
}

Path(status_file).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
PY

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

investing_path="$topic_dir/investing.md"

investing_instructions="$(cat <<EOF
请为主题《$topic_title》生成“可投资收敛页 investing.md”（中文），并严格按模板输出。

硬性要求：
1) 保留并填写模板中的所有标题（不要新增/删除标题）。
2) “细分赛道拆分”至少 3 层：赛道 → 细分 → 关键变量（每层至少 6 条要点）。
3) “公司池”表格必须 >= 12 行有效公司；每行必须填：公司、ticker（若无公开 ticker 写 N/A）、市场、暴露/投资假设（可证伪）、证据等级（先填 Level C 也可）、status、priority、关键缺口（需任务化）。
4) status 至少 1 行为 thesis_candidate；priority 至少 3 行为 high。
5) 关键数字不要编造；拿不到就写 unverified 并把“怎么拿到”写进关键缺口。

输出目标：
- 让 Investability Gate（公司池≥10 + thesis_candidate≥1）能通过。
EOF
)"

run_stamp="$(date +%Y-%m-%d_%H%M%S 2>/dev/null || date +%Y%m%d_%H%M%S)"
run_state_dir="$state_dir/runs/${run_stamp}_${tag}"
mkdir -p "$run_state_dir"

investing_result_json="$run_state_dir/investing_result.json"
investing_result="$(python3 scripts/glm_write_file.py \
  --output-path "$investing_path" \
  --overwrite \
  $allow_paid_flag \
  --timeout-sec 360 \
  --max-retries 4 \
  --template-path "templates/topic/investing.md" \
  --input-path "$investing_path" \
  --validate-must-have "## 细分赛道拆分" \
  --validate-must-have "## 公司池" \
  --validate-must-have "| ticker |" \
  --validate-must-have "| status |" \
  --validate-min-chars 1200 \
  --validate-max-chars 28000 \
  --system "你是严谨的投研助手。不要编造具体数字；不确定就标注 unverified 并给出核验任务。" \
  --instructions "$investing_instructions" )"
printf '%s' "$investing_result" >"$investing_result_json"

tasks_json="$run_state_dir/investing_tasks.json"
python3 scripts/topic_investing_create_tasks.py \
  --topic-id "$topic_id" \
  --investing-path "$investing_path" \
  --tasks-db "state/tasks.sqlite" \
  --limit 6 \
  --tag "$run_id" \
  --out "$tasks_json" >/dev/null || true

gate_json="$run_state_dir/investability_gate.json"
python3 scripts/investability_gate_check.py "$topic_id" --json --out "$gate_json" >/dev/null || true

chatgpt_prompt="$run_state_dir/chatgpt_audit_prompt.txt"
python3 - "$investing_path" "$topic_id" "$topic_title" >"$chatgpt_prompt" <<'PY'
import sys
from pathlib import Path

inv_path = Path(sys.argv[1])
topic_id = sys.argv[2]
topic_title = sys.argv[3]
text = inv_path.read_text(encoding="utf-8", errors="replace")

prompt = f"""你是资深投研负责人/审计员。请审计下面这份 topic 收敛页 investing.md（用于投资决策前的候选池收敛）。重点检查：\n\n1) 细分赛道拆分是否合理（是否缺关键环节/关键变量/关键口径）。\n2) 公司池是否覆盖主要玩家（中美+关键供应链），ticker/市场是否合理；明显错误请指出。\n3) 是否至少 1 个 thesis_candidate；如果不合理，请建议替换并给出理由。\n4) 关键缺口是否写得可执行（下一步能用什么一手来源验证）；请列出你认为最关键的 5-10 条核验任务。\n5) 给出一个“优先级排序建议”（接下来先研究哪 3 家公司，为什么）。\n\n请输出 Markdown，结构：\n- Audit summary\n- Missing / suspicious items\n- Suggested thesis candidate\n- Top verification tasks\n- Next actions\n\n下面是 investing.md 原文（topic_id={topic_id}，topic_title={topic_title}）：\n\n---\n{text}\n---\n"""

sys.stdout.write(prompt)
PY

chatgpt_audit_md="$run_state_dir/chatgpt_audit.md"
chatgpt_cmd=(
  python3 scripts/chatgpt_mcp_ask.py
  --tool "chatgpt_web_ask_pro_extended"
  --question-file "$chatgpt_prompt"
  --timeout-seconds 1800
  --out "$chatgpt_audit_md"
)
if command -v timeout >/dev/null 2>&1; then
  timeout 2100 "${chatgpt_cmd[@]}" >/dev/null || true
else
  "${chatgpt_cmd[@]}" >/dev/null || true
fi

gate_ok="$(python3 - "$gate_json" <<'PY'
import json, sys
from pathlib import Path
p=Path(sys.argv[1])
try:
  data=json.loads(p.read_text(encoding='utf-8'))
except Exception:
  print("0"); sys.exit(0)
results=data.get("results") or []
ok=bool(results and results[0].get("ok"))
print("1" if ok else "0")
PY
)"

python3 - "$record_path" "$investing_result_json" "$tasks_json" "$gate_json" "$chatgpt_audit_md" "$topic_id" "$topic_title" "$tag" "$ts_start" "$worker_id" <<'PY'
import json
import sys
from pathlib import Path

record = Path(sys.argv[1])
inv_json = Path(sys.argv[2])
tasks_json = Path(sys.argv[3])
gate_json = Path(sys.argv[4])
audit_md = Path(sys.argv[5])
topic_id = sys.argv[6]
topic_title = sys.argv[7]
tag = sys.argv[8]
ts_start = sys.argv[9]
worker_id = int(sys.argv[10])

record.parent.mkdir(parents=True, exist_ok=True)

inv = json.loads(inv_json.read_text(encoding="utf-8"))
tasks = json.loads(tasks_json.read_text(encoding="utf-8")) if tasks_json.exists() else {}
gate = json.loads(gate_json.read_text(encoding="utf-8")) if gate_json.exists() else {}

created = tasks.get("created") or []
gate_res = (gate.get("results") or [{}])[0]

lines = []
lines.append(f"# {topic_id} — {tag} run")
lines.append("")
lines.append(f"- ts: {ts_start}")
lines.append(f"- worker_id: {worker_id}")
lines.append(f"- topic_title: {topic_title}")
lines.append("")
lines.append("## Files updated (GLM write-file)")
validation = inv.get("validation") or {}
lines.append(
    f"- investing.md: path={inv.get('output_path')} model={inv.get('used_model')} tier={inv.get('used_tier')} "
    f"sha256={inv.get('sha256')} chars={inv.get('chars')} ok={validation.get('ok')}"
)
lines.append("")
lines.append("## Tasks created (from gaps)")
lines.append(f"- created: {len(created)}")
for t in created[:10]:
    lines.append(f"- {t.get('id')} {t.get('title')}")
lines.append("")
lines.append("## Investability Gate")
lines.append(f"- ok: {gate_res.get('ok')}")
for e in gate_res.get("errors") or []:
    lines.append(f"- ERROR: {e}")
for w in gate_res.get("warnings") or []:
    lines.append(f"- WARN: {w}")
lines.append("")
if audit_md.exists():
    lines.append("## ChatGPT audit")
    lines.append(f"- path: {audit_md}")

preview = (inv.get("preview") or "").replace("\n", " ").replace("\r", " ")
if preview:
    lines.append("")
    lines.append("## Preview (first 200 chars)")
    lines.append(f"- investing: {preview[:200]}")

record.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
print(str(record))
PY

stage_state="partial"
if [[ "$gate_ok" == "1" ]]; then
  stage_state="done"
fi

python3 scripts/topic_run_state.py manifest \
  --topic-id "$topic_id" \
  --topic-title "$topic_title" \
  --run-id "$run_id" \
  --stage "$tag" \
  --stage-state "$stage_state" \
  --worker-id "$worker_id" \
  --record-path "$record_path" \
  --artifact "investing=$investing_result_json" \
  --artifact "tasks=$tasks_json" \
  --artifact "gate=$gate_json" || true

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

echo "[worker] DONE topic=$topic_id record=$record_path stage=$stage_state"
