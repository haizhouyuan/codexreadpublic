#!/usr/bin/env bash
set -Eeuo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

worker_id="${ORCH_WORKER_ID:-0}"
topic_id="${ORCH_TOPIC_ID:-}"
topic_title="${ORCH_TOPIC_TITLE:-$topic_id}"
tag="${ORCH_TAG:-video_handoff_v2}"
record_path="${ORCH_RECORD_PATH:-}"

sleep_between="${ORCH_SLEEP_BETWEEN:-25}"
timeout_seconds="${ORCH_TIMEOUT_SECONDS:-1800}"
max_transcript_chars="${ORCH_MAX_TRANSCRIPT_CHARS:-20000}"
no_gemini="${ORCH_NO_GEMINI:-0}"
chatgpt_mcp_url="${ORCH_CHATGPT_MCP_URL:-}"

if [[ -z "$topic_id" ]]; then
  echo "Missing ORCH_TOPIC_ID" >&2
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
  run_stamp="$(date +%Y-%m-%d_%H%M 2>/dev/null || date +%Y%m%d_%H%M)"
  record_path="$topic_dir/notes/runs/${run_stamp}_${tag}.md"
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

  stage_state="failed"
}
trap 'on_fail ${LINENO}' ERR

args=(python3 scripts/topic_video_handoff_rerun.py
  --topic-id "$topic_id"
  --record-path "$record_path"
  --sleep-between "$sleep_between"
  --timeout-seconds "$timeout_seconds"
  --max-transcript-chars "$max_transcript_chars"
)
if [[ "$no_gemini" == "1" ]]; then
  args+=(--no-gemini)
fi
if [[ -n "$chatgpt_mcp_url" ]]; then
  args+=(--chatgpt-mcp-url "$chatgpt_mcp_url")
fi

"${args[@]}"

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
  "error_path": None,
  "ts": "$(date -Is 2>/dev/null || date)",
}, ensure_ascii=False), encoding="utf-8")
PY

python3 scripts/topic_run_state.py status \
  --topic-id "$topic_id" \
  --topic-title "$topic_title" \
  --run-id "$run_id" \
  --stage "$tag" \
  --state done \
  --worker-id "$worker_id" \
  --record-path "$record_path" || true

bash scripts/tmux_notify_controller_done.sh --topic "$topic_id" --record "$record_path" --status done || true
exit 0

