#!/usr/bin/env bash
set -Eeuo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

worker_id="${ORCH_WORKER_ID:-0}"
topic_id="${ORCH_TOPIC_ID:-}"
topic_title="${ORCH_TOPIC_TITLE:-}"
scope_hint="${ORCH_SCOPE_HINT:-}"
tag="${ORCH_TAG:-init}"
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
mkdir -p "$topic_dir/notes/runs"

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
  --stage "init" \
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
    --stage "init" \
    --state failed \
    --worker-id "$worker_id" \
    --record-path "$record_path" \
    --error-path "$err_file" || true
}
trap 'on_fail ${LINENO}' ERR

overview_path="$topic_dir/overview.md"
framework_path="$topic_dir/framework.md"
open_questions_path="$topic_dir/open_questions.md"

allow_paid_flag=""
if [[ "$allow_paid" == "1" ]]; then
  allow_paid_flag="--allow-paid"
fi

echo "[worker] topic=$topic_id title=$topic_title tag=$tag allow_paid=$allow_paid record=$record_path"

scope_hint_line=""
if [[ -n "$scope_hint" ]]; then
  scope_hint_line="研究边界提示（必须遵循）：$scope_hint"
fi

overview_instructions="$(cat <<EOF
请为主题《$topic_title》生成一份主题概览（中文）。

要求：
1) 必须保留并填写模板中的所有标题（不要新增/删除标题）。
2) 研究边界要写清：明确“包含/不包含”的子主题与相关概念，避免泛化科普。${scope_hint_line}
3) 关键结论要以“可迭代工作版”表达，避免硬断言。
4) 核心假设、风险与反例要具体，并给出如何验证。
5) 下一步行动给出 6-10 条可执行动作，优先：收集一手来源、建立 KPI 面板、梳理供应链与成本结构。
EOF
)"

overview_result="$(python3 scripts/glm_write_file.py \
  --output-path "$overview_path" \
  --overwrite \
  $allow_paid_flag \
  --timeout-sec 240 \
  --max-retries 2 \
  --template-path "templates/topic/overview.md" \
  --input-path "$overview_path" \
  --validate-must-have "## 一句话定义" \
  --validate-must-have "## 研究边界（包含/不包含）" \
  --validate-must-have "## 关键结论（可迭代）" \
  --validate-must-have "## 核心假设（待验证）" \
  --validate-must-have "## 风险与反例（打脸点）" \
  --validate-must-have "## 下一步行动" \
  --validate-min-chars 800 \
  --validate-max-chars 12000 \
  --system "你是一个严谨的研究助理。不要编造具体数字；不确定的地方要写成待验证假设或下一步行动。" \
  --instructions "$overview_instructions" )"

framework_instructions="$(cat <<EOF
请为主题《$topic_title》生成研究框架（中文）。

要求：
1) 必须保留模板中的 7 个维度标题。
2) 每个维度下给出 5-10 个要点（问题清单/关键变量/可收集的数据/建议资料类型）。
3) 投资视角维度要明确：关键变量、关键口径/计量单位/标准（避免混用）、成本结构、竞争格局、催化剂、主要风险。
EOF
)"

framework_result="$(python3 scripts/glm_write_file.py \
  --output-path "$framework_path" \
  --overwrite \
  $allow_paid_flag \
  --timeout-sec 240 \
  --max-retries 2 \
  --template-path "templates/topic/framework.md" \
  --input-path "$framework_path" \
  --validate-must-have "## 维度 1：技术原理与关键瓶颈" \
  --validate-must-have "## 维度 2：历史与里程碑" \
  --validate-must-have "## 维度 3：产业链/生态位" \
  --validate-must-have "## 维度 4：竞争格局与关键玩家" \
  --validate-must-have "## 维度 5：政策/监管/地缘风险" \
  --validate-must-have "## 维度 6：商业模式与成本结构" \
  --validate-must-have "## 维度 7：投资视角（关键变量/估值框架/催化剂）" \
  --validate-min-chars 1200 \
  --validate-max-chars 20000 \
  --system "你是一个严谨的研究框架设计师。输出要可用于长期迭代与投研决策。" \
  --instructions "$framework_instructions" )"

open_questions_instructions="$(cat <<EOF
请为主题《$topic_title》生成未解问题清单（中文）。

要求：
1) 必须保留模板中的三个小节标题：未知/争议/待验证。
2) Unknowns: 8-12 条（尽量具体：关键 KPI 口径如何统一、成本结构/供应链瓶颈、部署/运维约束、可靠性/合规要求、关键技术成熟度等）。
3) Disputes: 6-10 条（尽量具体：不同技术路线的长期胜负、供需周期与定价权、标准化进程、政策/出口管制影响等）。
4) To verify: 8-12 条，每条写清“要验证什么 + 用什么一手来源/数据验证”。
EOF
)"

open_questions_result="$(python3 scripts/glm_write_file.py \
  --output-path "$open_questions_path" \
  --overwrite \
  $allow_paid_flag \
  --timeout-sec 240 \
  --max-retries 2 \
  --template-path "templates/topic/open_questions.md" \
  --input-path "$open_questions_path" \
  --validate-must-have "## 未知（Unknowns）" \
  --validate-must-have "## 争议（Disputes）" \
  --validate-must-have "## 待验证（To verify）" \
  --validate-min-chars 700 \
  --validate-max-chars 16000 \
  --system "你是一个严谨的问题拆解助手。所有条目要可验证/可行动。" \
  --instructions "$open_questions_instructions" )"

run_stamp="$(date +%Y-%m-%d_%H%M%S 2>/dev/null || date +%Y%m%d_%H%M%S)"
run_state_dir="$state_dir/runs/${run_stamp}_${tag}"
mkdir -p "$run_state_dir"
overview_result_json="$run_state_dir/overview_result.json"
framework_result_json="$run_state_dir/framework_result.json"
open_questions_result_json="$run_state_dir/open_questions_result.json"
printf '%s' "$overview_result" >"$overview_result_json"
printf '%s' "$framework_result" >"$framework_result_json"
printf '%s' "$open_questions_result" >"$open_questions_result_json"

python3 - "$record_path" "$overview_result_json" "$framework_result_json" "$open_questions_result_json" "$topic_id" "$topic_title" "$tag" "$ts_start" "$worker_id" <<'PY'
import json
import sys
from pathlib import Path

record = Path(sys.argv[1])
ov_path = Path(sys.argv[2])
fw_path = Path(sys.argv[3])
oq_path = Path(sys.argv[4])
topic_id = sys.argv[5]
topic_title = sys.argv[6]
tag = sys.argv[7]
ts_start = sys.argv[8]
worker_id = int(sys.argv[9])

record.parent.mkdir(parents=True, exist_ok=True)

ov = json.loads(ov_path.read_text(encoding="utf-8"))
fw = json.loads(fw_path.read_text(encoding="utf-8"))
oq = json.loads(oq_path.read_text(encoding="utf-8"))


def _line(label: str, d: dict) -> str:
    validation = d.get("validation") or {}
    return (
        f"- {label}: path={d.get('output_path')} model={d.get('used_model')} tier={d.get('used_tier')} "
        f"sha256={d.get('sha256')} chars={d.get('chars')} ok={validation.get('ok')}"
    )


content = []
content.append(f"# {topic_id} — init run ({tag})")
content.append("")
content.append(f"- ts: {ts_start}")
content.append(f"- worker_id: {worker_id}")
content.append(f"- topic_title: {topic_title}")
content.append("")
content.append("## Files updated (GLM write-file)")
content.append(_line("overview", ov))
content.append(_line("framework", fw))
content.append(_line("open_questions", oq))
content.append("")
content.append("## Previews (first 200 chars)")
for label, d in (("overview", ov), ("framework", fw), ("open_questions", oq)):
    preview = (d.get("preview") or "").replace("\n", " ").replace("\r", " ")
    content.append(f"- {label}: {preview[:200]}")

record.write_text("\n".join(content).rstrip() + "\n", encoding="utf-8")
print(str(record))
PY

python3 scripts/topic_run_state.py manifest \
  --topic-id "$topic_id" \
  --topic-title "$topic_title" \
  --run-id "$run_id" \
  --stage "init" \
  --stage-state done \
  --worker-id "$worker_id" \
  --record-path "$record_path" \
  --artifact "overview=$overview_result_json" \
  --artifact "framework=$framework_result_json" \
  --artifact "open_questions=$open_questions_result_json" || true

python3 scripts/topic_run_state.py status \
  --topic-id "$topic_id" \
  --topic-title "$topic_title" \
  --run-id "$run_id" \
  --stage "init" \
  --state done \
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

bash scripts/tmux_notify_controller_done.sh --topic "$topic_id" --record "$record_path" --status done || true

echo "[worker] DONE topic=$topic_id record=$record_path"
