#!/usr/bin/env bash
set -Eeuo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

worker_id="${ORCH_WORKER_ID:-0}"
topic_id="${ORCH_TOPIC_ID:-}"
topic_title="${ORCH_TOPIC_TITLE:-$topic_id}"
tag="${ORCH_TAG:-decision_package}"
allow_paid="${ORCH_ALLOW_PAID:-0}"
record_path="${ORCH_RECORD_PATH:-}"

ticker="${ORCH_TICKER:-}"
company_name="${ORCH_NAME:-}"
source_url_override="${ORCH_SOURCE_URL:-}"

if [[ -z "$topic_id" ]]; then
  echo "Missing ORCH_TOPIC_ID" >&2
  exit 2
fi
if [[ -z "$ticker" ]]; then
  echo "Missing ORCH_TICKER" >&2
  exit 2
fi

state_dir="state/tmux_orch/workers/$worker_id"
mkdir -p "$state_dir"
status_file="$state_dir/status.json"

topic_dir="archives/topics/$topic_id"
if [[ ! -d "$topic_dir" ]]; then
  python3 scripts/new_topic.py "$topic_id"
fi
mkdir -p "$topic_dir/notes/runs" "$topic_dir/digests" "archives/investing/decisions"

if [[ -z "$record_path" ]]; then
  run_id="$(date +%Y-%m-%d_%H%M 2>/dev/null || date +%Y%m%d_%H%M)"
  safe_ticker="$(echo "$ticker" | tr '[:lower:]' '[:upper:]' | tr -cd 'A-Z0-9._-')"
  record_path="$topic_dir/notes/runs/${run_id}_${tag}_${safe_ticker}.md"
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

stage_state="running"
exit_error_msg=""

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

  if [[ -n "$record_path" && ! -f "$record_path" ]]; then
    mkdir -p "$(dirname "$record_path")"
    {
      echo "# $topic_id — $tag run (failed)"
      echo
      echo "- ts: $ts_start"
      echo "- failed_at: $ts_fail"
      echo "- worker_id: $worker_id"
      echo "- topic_title: $topic_title"
      echo "- ticker: $ticker"
      if [[ -n "$company_name" ]]; then
        echo "- name: $company_name"
      fi
      echo
      echo "## Error"
      echo "- error_path: $err_file"
      echo "- exit_code: $exit_code"
      echo "- line: $lineno"
      echo "- command: ${BASH_COMMAND:-}"
      if [[ -n "${run_state_dir:-}" ]]; then
        echo
        echo "## Run State Dir"
        echo "- path: $run_state_dir"
      fi
    } >"$record_path"
  fi

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

  stage_state="failed"
}
trap 'on_fail ${LINENO}' ERR

on_exit() {
  local code="$?"
  if [[ "${stage_state:-running}" != "running" ]]; then
    return 0
  fi
  if [[ "$code" == "0" ]]; then
    return 0
  fi

  local ts_fail
  ts_fail="$(date -Is 2>/dev/null || date)"
  local err_stamp
  err_stamp="$(date +%Y%m%d_%H%M%S 2>/dev/null || date +%Y%m%d_%H%M%S)"
  local err_dir="$state_dir/errors"
  mkdir -p "$err_dir"
  local err_file="$err_dir/${run_id}_${tag}_${err_stamp}_exit.txt"

  {
    echo "ts: $ts_fail"
    echo "worker_id: $worker_id"
    echo "topic_id: $topic_id"
    echo "topic_title: $topic_title"
    echo "tag: $tag"
    echo "run_id: $run_id"
    echo "record_path: $record_path"
    echo "exit_code: $code"
    if [[ -n "$exit_error_msg" ]]; then
      echo "message: $exit_error_msg"
    fi
    echo "pwd: $(pwd)"
  } >"$err_file"

  if [[ -n "$record_path" && ! -f "$record_path" ]]; then
    mkdir -p "$(dirname "$record_path")"
    {
      echo "# $topic_id — $tag run (failed)"
      echo
      echo "- ts: $ts_start"
      echo "- failed_at: $ts_fail"
      echo "- worker_id: $worker_id"
      echo "- topic_title: $topic_title"
      echo "- ticker: $ticker"
      if [[ -n "$company_name" ]]; then
        echo "- name: $company_name"
      fi
      echo
      echo "## Error"
      echo "- error_path: $err_file"
      echo "- exit_code: $code"
      if [[ -n "$exit_error_msg" ]]; then
        echo "- message: $exit_error_msg"
      fi
      if [[ -n "${run_state_dir:-}" ]]; then
        echo
        echo "## Run State Dir"
        echo "- path: $run_state_dir"
      fi
    } >"$record_path"
  fi

  python3 - "$status_file" "$worker_id" "$topic_id" "$topic_title" "$tag" "$record_path" "$err_file" "$ts_fail" "$code" "$exit_error_msg" <<'PY'
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
    message,
) = sys.argv[1:11]

payload = {
    "worker_id": int(worker_id),
    "status": "failed",
    "topic_id": topic_id,
    "topic_title": topic_title,
    "tag": tag,
    "record_path": record_path,
    "error_path": error_path,
    "error": (message or f"exit_code={exit_code}").strip(),
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
    --error-path "$err_file" >/dev/null 2>&1 || true

  stage_state="failed"
}
trap on_exit EXIT

allow_paid_flag=""
if [[ "$allow_paid" == "1" ]]; then
  allow_paid_flag="--allow-paid"
fi

run_stamp="$(date +%Y-%m-%d_%H%M%S 2>/dev/null || date +%Y%m%d_%H%M%S)"
run_state_dir="$state_dir/runs/${run_stamp}_${tag}_$(echo "$ticker" | tr '[:upper:]' '[:lower:]')"
mkdir -p "$run_state_dir"

search_json="$run_state_dir/sec_search.json"

websearch_err="$run_state_dir/sec_search.stderr.txt"

sec_url=""
if [[ -n "$source_url_override" ]]; then
  sec_url="$source_url_override"
else
  websearch_ok=0
  for tsec in 45 90; do
    if python3 scripts/websearch_client.py \
      --query "${ticker} form 10-k site:sec.gov" \
      --max-results 8 \
      --language en \
      --recency oneYear \
      $allow_paid_flag \
      --timeout-sec "$tsec" \
      >"$search_json" 2>"$websearch_err"; then
      websearch_ok=1
      break
    fi
    sleep 2 || true
  done
  if [[ "$websearch_ok" != "1" ]]; then
    echo "websearch_client failed (see $websearch_err)" >&2
  fi

  sec_url="$(
  python3 - "$search_json" <<'PY'
import json
import re
import sys
from pathlib import Path

p = Path(sys.argv[1])
try:
    data = json.loads(p.read_text(encoding="utf-8"))
except Exception:
    sys.stdout.write("")
    raise SystemExit(0)
results = data.get("results") or []
best = ""
for r in results:
    url = str(r.get("url") or "").strip()
    if not url:
        continue
    if "sec.gov" not in url:
        continue
    if "/Archives/edgar/data/" in url and re.search(r"\.htm(l)?$", url):
        best = url
        break
if not best:
    # fallback: any sec.gov link
    for r in results:
        url = str(r.get("url") or "").strip()
        if "sec.gov" in url:
            best = url
            break
sys.stdout.write(best)
PY
)"
fi

if [[ -z "$sec_url" ]]; then
  exit_error_msg="No SEC URL found for ticker=$ticker"
  echo "$exit_error_msg" >&2
  exit 3
fi

pack_json="$run_state_dir/source_pack.json"
python3 scripts/source_pack_client.py \
  --url "$sec_url" \
  --topic "$topic_id" \
  --min-chars 2000 \
  $allow_paid_flag \
  --timeout-sec 90 \
  >"$pack_json"

text_path="$(
python3 - "$pack_json" <<'PY'
import json
import sys
from pathlib import Path

data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
tp = str(data.get("text_path") or "").strip()
sys.stdout.write(tp)
PY
)"

if [[ -z "$text_path" || ! -f "$text_path" ]]; then
  exit_error_msg="source_pack missing text_path (pack_json=$pack_json)"
  echo "$exit_error_msg" >&2
  exit 4
fi

sec_date="$(
python3 - "$sec_url" <<'PY'
import re
import sys

url = sys.argv[1]
m = re.search(r"/([a-z0-9_-]+)-(\\d{8})\\.htm", url, re.I)
if m:
    sys.stdout.write(m.group(2))
    raise SystemExit(0)
m = re.search(r"(\\d{8})", url)
sys.stdout.write(m.group(1) if m else "")
PY
)"

digest_stem="$(echo "${ticker}" | tr '[:upper:]' '[:lower:]')_sec10k"
if [[ -n "$sec_date" ]]; then
  digest_stem="${digest_stem}_${sec_date}"
fi
digest_filename="${digest_stem}.md"
digest_path="$topic_dir/digests/$digest_filename"

max_input_bytes_per_file=480000
digest_input_path="$text_path"
digest_input_note=""
text_bytes="$(wc -c <"$text_path" 2>/dev/null | tr -d ' ' || echo 0)"
if [[ "$text_bytes" =~ ^[0-9]+$ ]] && (( text_bytes > max_input_bytes_per_file )); then
  truncated_path="$run_state_dir/source_text_truncated.md"
  python3 - "$text_path" "$truncated_path" "$max_input_bytes_per_file" <<'PY'
import sys
from pathlib import Path

src = Path(sys.argv[1])
dst = Path(sys.argv[2])
max_bytes = int(sys.argv[3])

data = src.read_bytes()
if len(data) <= max_bytes:
    dst.write_bytes(data)
else:
    # Ensure valid UTF-8 by decoding with ignore.
    text = data[:max_bytes].decode("utf-8", errors="ignore")
    dst.write_text(text, encoding="utf-8")
print(str(dst))
PY
  digest_input_path="$truncated_path"
  digest_input_note=$'\n补充说明：输入材料过长，已在本地截断后再生成 digest；若关键数据缺失，请标记 unverified 并在核验动作中说明需要查看 SEC 原文的哪个 Item/表。'
fi

digest_instructions="$(cat <<EOF
请基于输入材料（SEC/官方披露正文）为主题《$topic_title》生成一份 digest（中文），并严格按 templates/digest.md 输出（含 frontmatter + Claim Ledger 表）。

硬性要求：
1) 不要编造具体数字；拿不到就写 unverified，并在“建议核验动作”里写清楚应去 SEC 文档的哪个部分核对（例如 Item 7, MD&A / 财务报表附注等）。
2) frontmatter 必填：
   - title：写清楚公司 + 文档类型（例如 "${ticker} Form 10-K 摘要"）
   - source_type：official
   - source_url：$sec_url
   - source_path：$text_path（或对应 source_pack 目录）
   - published_at：尽量从文档中提取（拿不到可留空）
   - topic_id：$topic_id
   - entities：至少包含 ticker 与公司名（若未知则只填 ticker）
3) Claim Ledger 至少 8 条。每条必须填 claim_id（稳定且不含空格），并在“来源/证据”里标注 [Level A] + 指向 source_url（可附段落关键词）。
4) 核验状态枚举：unverified|partially_verified|verified|falsified。
5) 影响范围/置信度枚举：high|medium|low。
$digest_input_note
EOF
)"

digest_result_json="$run_state_dir/digest_result.json"
set +e
digest_result="$(python3 scripts/glm_write_file.py \
  --output-path "$digest_path" \
  --overwrite \
  $allow_paid_flag \
  --timeout-sec 600 \
  --max-retries 4 \
  --template-path "templates/digest.md" \
  --input-path "$digest_input_path" \
  --max-input-bytes-per-file "$max_input_bytes_per_file" \
  --validate-must-have "## Claim Ledger" \
  --validate-must-have "| claim_id |" \
  --validate-min-chars 1200 \
  --validate-max-chars 28000 \
  --system "你是严谨的投研分析助手。不要编造数字；不确定就标注 unverified，并给出核验路径。" \
  --instructions "$digest_instructions" 2>&1 )"
digest_code="$?"
set -e
printf '%s' "$digest_result" >"$digest_result_json"
if [[ "$digest_code" != "0" ]]; then
  exit_error_msg="digest generation failed (code=$digest_code). see $digest_result_json"
  echo "$exit_error_msg" >&2
  exit 5
fi
if [[ ! -f "$digest_path" ]]; then
  exit_error_msg="digest file not created: $digest_path (see $digest_result_json)"
  echo "$exit_error_msg" >&2
  exit 6
fi

python3 scripts/topic_ingest_digest.py "$topic_id" "$digest_path" --timeline >/dev/null || true

claim_tasks_json="$run_state_dir/claim_tasks.json"
python3 scripts/claim_ledger_to_tasks.py --topic "$topic_id" --db "state/tasks.sqlite" --max-per-digest 3 --max-total 12 \
  >"$claim_tasks_json" || true

claim_id="$(
python3 - "$digest_path" <<'PY'
import sys
from pathlib import Path

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8", errors="replace")
if "\n## Claim Ledger" not in text and "\n## Claim Ledger（" not in text:
    sys.stdout.write("")
    raise SystemExit(0)

lines = text.splitlines()

def is_row(ln: str) -> bool:
    s = ln.strip()
    return s.startswith("|") and s.endswith("|")

claim_idx = None
for i, ln in enumerate(lines):
    if ln.strip().startswith("## Claim Ledger"):
        claim_idx = i
        break
if claim_idx is None:
    sys.stdout.write("")
    raise SystemExit(0)

header_i = None
header = None
for j in range(claim_idx + 1, min(len(lines), claim_idx + 120)):
    if is_row(lines[j]) and "claim" in lines[j].lower():
        header_i = j
        header = [c.strip().lower().replace(" ", "") for c in lines[j].strip().strip("|").split("|")]
        break

if header_i is None or not header:
    sys.stdout.write("")
    raise SystemExit(0)

cid_col = None
for k, h in enumerate(header):
    if h in ("claim_id", "claimid"):
        cid_col = k
        break
if cid_col is None:
    sys.stdout.write("")
    raise SystemExit(0)

for ln in lines[header_i + 2 :]:
    if not is_row(ln):
        break
    cells = [c.strip() for c in ln.strip().strip("|").split("|")]
    if cid_col >= len(cells):
        continue
    cid = cells[cid_col].strip("`").strip()
    if cid:
        sys.stdout.write(cid)
        raise SystemExit(0)
sys.stdout.write("")
PY
)"

if [[ -z "$claim_id" ]]; then
  exit_error_msg="Failed to extract claim_id from digest: $digest_path"
  echo "$exit_error_msg" >&2
  exit 7
fi

decision_path="$(python3 scripts/new_decision_package.py --ticker "$ticker" --name "$company_name" --topic-id "$topic_id" --overwrite)"

ref_str="topic=${topic_id}; digest=${digest_filename}; claim_id=${claim_id}"
company_card_path="$topic_dir/companies/$(echo "$ticker" | tr '[:upper:]' '[:lower:]').md"

body_inputs=()
if [[ -f "$company_card_path" ]]; then
  body_inputs+=(--input-path "$company_card_path")
fi
body_inputs+=(--input-path "$digest_path")
body_inputs+=(--input-path "$topic_dir/investing.md")

decision_body_path="$run_state_dir/decision_body.md"
decision_body_instructions="$(cat <<EOF
请基于输入材料（company card + digest + investing.md）起草一份“投资决策包”的正文（中文），不要输出 frontmatter；正文必须严格对齐 templates/decision_package.md 的结构与表头（便于 decision_gate_check.py 机器验收）。

硬性要求（不可违背）：
1) 必须包含并保留以下一级标题（完全一致，行首开始）：
   - ## Thesis
   - ## Evidence Map（强制引用）
   - ## Bull / Base / Bear
   - ## Trade Plan（规则化）
   - ## Monitoring Plan
   - ## Open Gaps & Tasks
   - ## Decision Log
2) 必须包含 Thesis 子结构（完全一致）：
   - ### 一句话 Thesis
   - ### 可证伪假设（3 条）
   并在“可证伪假设（3 条）”下输出表格，表头必须为：
   | # | 假设 | Pass 条件（可观测） | Fail 条件（证伪） | 截止时间 | 关键证据指针（ref） |
3) Evidence Map 必须输出表格，表头必须为（完全一致）：
   | # | ref（topic/digest/claim_id） | Level（A/B/C） | 证据摘要（只写可核验内容） | 核验状态（unverified/verified/…） | 备注 |
   且至少 2 行有效证据；第 1 行的 ref 必须为：\`$ref_str\`，Level 必须为 Level A。
4) Open Gaps & Tasks 必须输出表格（完全一致）：
   | 缺口 | 影响 | 建议动作 | 对应 tasks（id/链接） |
   允许 tasks 列先留空（status=draft），但“建议动作”必须可执行且指向一手来源/核验路径。
5) 不要编造具体财务数字；拿不到就写 unverified，并在 Open Gaps 表里写明“去哪里核验”（例如 SEC 10-K/10-Q 的具体 Item/附注）。
6) 输出整体控制在 12000–24000 字符以内；要点化、可执行。

请从一行标题开始（完全一致）：
# 决策包：$ticker — ${company_name:-$ticker}
EOF
)"

decision_body_result_json="$run_state_dir/decision_body_result.json"
decision_body_result="$(python3 scripts/glm_write_file.py \
  --output-path "$decision_body_path" \
  --overwrite \
  $allow_paid_flag \
  --timeout-sec 700 \
  --max-retries 4 \
  "${body_inputs[@]}" \
  --validate-must-have "## Evidence Map（强制引用）" \
  --validate-must-have "| # | ref（topic/digest/claim_id） | Level（A/B/C） |" \
  --validate-must-have "| 缺口 | 影响 | 建议动作 | 对应 tasks（id/链接） |" \
  --validate-must-have "| # | 假设 | Pass 条件（可观测） | Fail 条件（证伪） | 截止时间 | 关键证据指针（ref） |" \
  --validate-must-have "$ref_str" \
  --validate-must-have "## Open Gaps & Tasks" \
  --validate-min-chars 2500 \
  --validate-max-chars 26000 \
  --system "你是严谨的投资研究负责人。不要编造具体数字；引用必须可追溯到证据指针。" \
  --instructions "$decision_body_instructions" )"
printf '%s' "$decision_body_result" >"$decision_body_result_json"

python3 - "$decision_path" "$decision_body_path" <<'PY'
import re
import sys
from datetime import date
from pathlib import Path

decision_path = Path(sys.argv[1])
body_path = Path(sys.argv[2])

raw = decision_path.read_text(encoding="utf-8", errors="replace")
lines = raw.splitlines(keepends=True)
if not lines or lines[0].strip() != "---":
    raise SystemExit("decision package missing frontmatter markers")

end = None
for i in range(1, len(lines)):
    if lines[i].strip() == "---":
        end = i
        break
if end is None:
    raise SystemExit("decision package frontmatter missing closing marker")

fm_lines = lines[: end + 1]
fm_text = "".join(fm_lines)
today = date.today().isoformat()
fm_text = re.sub(r'^(updated_at:\\s*\")[^\"]*(\"\\s*)$', rf'\\1{today}\\2', fm_text, flags=re.M)

body = body_path.read_text(encoding="utf-8", errors="replace").lstrip("\n")
decision_path.write_text(fm_text + "\n" + body.rstrip() + "\n", encoding="utf-8")
print(str(decision_path))
PY

gate_json="$run_state_dir/decision_gate.json"
python3 scripts/decision_gate_check.py "$decision_path" --json --out "$gate_json" >/dev/null || true

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

stage_state="done"
if [[ "$gate_ok" != "1" ]]; then
  stage_state="failed"
fi

chatgpt_prompt="$run_state_dir/chatgpt_decision_audit_prompt.txt"
python3 - "$decision_path" "$topic_id" "$topic_title" "$ticker" >"$chatgpt_prompt" <<'PY'
import sys
from pathlib import Path

decision_path = Path(sys.argv[1])
topic_id = sys.argv[2]
topic_title = sys.argv[3]
ticker = sys.argv[4]
text = decision_path.read_text(encoding="utf-8", errors="replace")

prompt = f"""你是资深投研负责人/审计员。请审计下面这份投资决策包（用于进入 reviewed/active 之前）。\n\n要求：\n1) 检查 Evidence Map 是否足够支撑 thesis（证据等级是否合理、是否缺关键反证）。\n2) 指出最可能导致错误决策的 5 个关键缺口（按影响排序），并给出每个缺口最优的一手来源核验路径。\n3) 评估 Bull/Base/Bear 是否覆盖关键情景与口径，是否存在逻辑跳跃。\n4) 给出“是否建议进入 reviewed（可以）/继续 draft（不建议）”的结论，并给出理由。\n\n输出 Markdown，结构：\n- Audit summary\n- Evidence critique\n- Missing / risky assumptions\n- Top verification actions\n- Recommendation (reviewed? yes/no)\n\n决策包原文（topic_id={topic_id}，topic_title={topic_title}，ticker={ticker}）：\n\n---\n{text}\n---\n"""

sys.stdout.write(prompt)
PY

chatgpt_audit_md="$run_state_dir/chatgpt_decision_audit.md"
chatgpt_cmd=(
  python3 scripts/chatgpt_mcp_ask.py
  --tool "chatgpt_web_ask_pro_extended"
  --question-file "$chatgpt_prompt"
  --timeout-seconds 2400
  --out "$chatgpt_audit_md"
)
if command -v timeout >/dev/null 2>&1; then
  timeout 2700 "${chatgpt_cmd[@]}" >/dev/null || true
else
  "${chatgpt_cmd[@]}" >/dev/null || true
fi

python3 - "$record_path" "$topic_id" "$topic_title" "$ticker" "$company_name" "$sec_url" "$digest_path" "$decision_path" "$gate_json" "$chatgpt_audit_md" "$ts_start" "$worker_id" "$tag" <<'PY'
import json
import sys
from pathlib import Path

record = Path(sys.argv[1])
topic_id = sys.argv[2]
topic_title = sys.argv[3]
ticker = sys.argv[4]
name = sys.argv[5]
sec_url = sys.argv[6]
digest_path = Path(sys.argv[7])
decision_path = Path(sys.argv[8])
gate_json = Path(sys.argv[9])
audit_md = Path(sys.argv[10])
ts_start = sys.argv[11]
worker_id = int(sys.argv[12])
tag = sys.argv[13]

gate = {}
try:
    gate = json.loads(gate_json.read_text(encoding="utf-8"))
except Exception:
    gate = {}

results = gate.get("results") or []
one = results[0] if results else {}
ok = bool(one.get("ok"))
errors = one.get("errors") or []
warnings = one.get("warnings") or []
stats = one.get("stats") or {}

lines = []
lines.append(f"# {topic_id} — {tag} run")
lines.append("")
lines.append(f"- ts: {ts_start}")
lines.append(f"- worker_id: {worker_id}")
lines.append(f"- topic_title: {topic_title}")
lines.append(f"- ticker: {ticker}")
if name:
    lines.append(f"- name: {name}")
lines.append("")
lines.append("## Inputs")
lines.append(f"- sec_url: {sec_url}")
lines.append("")
lines.append("## Outputs")
lines.append(f"- digest: {digest_path}")
lines.append(f"- decision_package: {decision_path}")
lines.append("")
lines.append("## Decision Gate")
lines.append(f"- ok: {str(ok).lower()}")
if stats:
    lines.append(f"- stats: {json.dumps(stats, ensure_ascii=False)}")
for e in errors[:10]:
    lines.append(f"- ERROR: {e}")
for w in warnings[:10]:
    lines.append(f"- WARN: {w}")
if audit_md.exists():
    lines.append("")
    lines.append("## ChatGPT audit")
    lines.append(f"- path: {audit_md}")

record.parent.mkdir(parents=True, exist_ok=True)
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
  --artifact "sec_search=$search_json" \
  --artifact "source_pack=$pack_json" \
  --artifact "digest_result=$digest_result_json" \
  --artifact "decision_body_result=$decision_body_result_json" \
  --artifact "decision_gate=$gate_json" || true

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
  "status": "done" if "$stage_state" == "done" else "$stage_state",
  "topic_id": "$topic_id",
  "topic_title": "$topic_title",
  "tag": "$tag",
  "record_path": "$record_path",
  "ts": "$ts_done",
}, ensure_ascii=False), encoding="utf-8")
PY

bash scripts/tmux_notify_controller_done.sh --topic "$topic_id" --record "$record_path" --status "$stage_state" || true

echo "[worker] DONE topic=$topic_id tag=$tag ticker=$ticker state=$stage_state record=$record_path"
