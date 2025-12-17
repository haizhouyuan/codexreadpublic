#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF' >&2
Usage:
  tmux_plan_reminder.sh --plan <path> [--interval-sec 900] [--controller <pane_id>] [--message <text>]

Sends a periodic reminder message into the controller Codex pane (tmux), to reduce the chance of
"lost context" when sessions compress/crash.

Notes:
  - Controller pane is resolved from (in order):
      1) --controller
      2) env CODEX_CONTROLLER_PANE
      3) tmux global env CODEX_CONTROLLER_PANE
  - This script is intended to run inside a tmux pane/session (so tmux can send keys).
EOF
}

plan_path=""
interval_sec=900
controller="${CODEX_CONTROLLER_PANE:-}"
custom_message=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --plan) plan_path="${2:-}"; shift 2 ;;
    --interval-sec) interval_sec="${2:-}"; shift 2 ;;
    --controller) controller="${2:-}"; shift 2 ;;
    --message) custom_message="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown arg: $1" >&2; usage; exit 2 ;;
  esac
done

if [[ -z "$plan_path" ]]; then
  echo "Missing --plan" >&2
  usage
  exit 2
fi

if [[ -z "${TMUX:-}" ]]; then
  echo "Not inside tmux (TMUX is empty). Start this script via tmux." >&2
  exit 2
fi

if [[ -z "$controller" ]]; then
  controller="$(tmux show-environment -g CODEX_CONTROLLER_PANE 2>/dev/null | sed -n 's/^CODEX_CONTROLLER_PANE=//p' | tail -n 1)"
fi
if [[ -z "$controller" ]]; then
  echo "Missing controller pane. Run scripts/tmux_set_controller_pane.sh in the controller pane first." >&2
  exit 2
fi

log_dir="state/tmp/plan_reminder"
mkdir -p "$log_dir"
log_file="$log_dir/reminder_$(date +%Y%m%d).log"

submit_wait_sec="${CODEX_TMUX_SUBMIT_WAIT_SEC_PLAN_REMINDER:-3}"
submit_between_sec="${CODEX_TMUX_SUBMIT_BETWEEN_SEC_PLAN_REMINDER:-1.2}"
submit_attempts="${CODEX_TMUX_SUBMIT_ATTEMPTS_PLAN_REMINDER:-3}"

submit_codex() {
  sleep "$submit_wait_sec" || true
  for ((attempt=1; attempt<=submit_attempts; attempt++)); do
    tmux send-keys -t "$controller" C-m
    sleep "$submit_between_sec" || true
    tmux send-keys -t "$controller" Enter
    sleep "$submit_between_sec" || true
    tmux send-keys -t "$controller" C-j
    sleep 0.2 || true

    last_prompt_line="$(tmux capture-pane -t "$controller" -p 2>/dev/null | grep -E '^›' | tail -n 1 || true)"
    if [[ "$last_prompt_line" =~ ^›[[:space:]]*$ ]]; then
      return 0
    fi
  done
  return 0
}

while true; do
  ts="$(date -Is 2>/dev/null || date)"
  msg="${custom_message:-请查看计划文档：${plan_path}（上下文可能压缩/崩溃，按计划执行并记录）}"

  echo "[$ts] controller=$controller plan=$plan_path" >>"$log_file"

  tmux send-keys -t "$controller" -l "[codex-reminder] $msg"
  submit_codex

  sleep "$interval_sec" || true
done

