#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF' >&2
Usage:
  tmux_notify_controller_done.sh --topic <topic_id> --record <path> [--status <text>] [--controller <pane_id>]

Notes:
  - Controller pane must be set via either:
      * env: CODEX_CONTROLLER_PANE
      * tmux global env: CODEX_CONTROLLER_PANE (recommended)
  - If the controller pane is running Codex CLI, it will send a plain message:
      [codex-worker] ...
    If Codex is currently busy/running, the message may queue in the UI and will be processed
    after the current run finishes — this is expected.
  - Otherwise it will inject a shell echo command for a persistent log line.
  - Tuning (optional):
      * CODEX_TMUX_SUBMIT_WAIT_SEC_CODEX (default: 5)  - wait after paste before submit
      * CODEX_TMUX_SUBMIT_ATTEMPTS (default: 4)        - submit retry attempts
      * CODEX_TMUX_SUBMIT_BETWEEN_SEC (default: 0.6)   - sleep between attempts
EOF
}

topic=""
record=""
status="done"
controller="${CODEX_CONTROLLER_PANE:-}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --topic) topic="${2:-}"; shift 2 ;;
    --record) record="${2:-}"; shift 2 ;;
    --status) status="${2:-}"; shift 2 ;;
    --controller) controller="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown arg: $1" >&2; usage; exit 2 ;;
  esac
done

if [[ -z "$topic" ]]; then
  echo "Missing --topic" >&2
  usage
  exit 2
fi
if [[ -z "$record" ]]; then
  echo "Missing --record" >&2
  usage
  exit 2
fi

if [[ -z "$controller" ]]; then
  if [[ -n "${TMUX:-}" ]]; then
    # Parse: CODEX_CONTROLLER_PANE=%38
    controller="$(tmux show-environment -g CODEX_CONTROLLER_PANE 2>/dev/null | sed -n 's/^CODEX_CONTROLLER_PANE=//p' | tail -n 1)"
  fi
fi
if [[ -z "$controller" ]]; then
  echo "Missing controller pane. Run scripts/tmux_set_controller_pane.sh in the controller pane first." >&2
  exit 2
fi

worker_fq=""
if [[ -n "${TMUX:-}" ]]; then
  worker_fq="$(tmux display-message -p '#S:#I.#P')"
fi

ts="$(date -Is 2>/dev/null || date)"
msg="WORKER_${status^^} topic=${topic} worker=${worker_fq:-unknown} record=${record} ts=${ts}"

# Decide controller mode: Codex CLI (send plain message) vs shell (send echo).
controller_pid="$(tmux display-message -p -t "$controller" '#{pane_pid}' 2>/dev/null || true)"
is_codex=0
if [[ -n "$controller_pid" ]]; then
  if ps -o cmd= --ppid "$controller_pid" 2>/dev/null | grep -qi 'codex'; then
    is_codex=1
  fi
fi

# Default: Codex TUIs sometimes need a short delay after paste before "Enter" is recognized as submit.
# User can override both via env.
submit_wait_sec_codex="${CODEX_TMUX_SUBMIT_WAIT_SEC_CODEX:-${CODEX_TMUX_SUBMIT_WAIT_SEC:-5}}"
submit_wait_sec_shell="${CODEX_TMUX_SUBMIT_WAIT_SEC_SHELL:-${CODEX_TMUX_SUBMIT_WAIT_SEC:-0.1}}"
submit_attempts="${CODEX_TMUX_SUBMIT_ATTEMPTS:-4}"
submit_check_sec="${CODEX_TMUX_SUBMIT_CHECK_SEC:-0.2}"
submit_between_sec="${CODEX_TMUX_SUBMIT_BETWEEN_SEC:-0.6}"

submit_codex() {
  # Some TUIs differentiate between carriage-return and keypad-enter.
  # We send a small set of submit key events; if it didn't submit, retry.
  #
  # "Submitted" heuristic:
  #   - In Codex CLI the composer line starts with "›". When submitted, the last "›" line should be empty.
  sleep "$submit_wait_sec_codex" || true
  for ((attempt=1; attempt<=submit_attempts; attempt++)); do
    tmux send-keys -t "$controller" C-m Enter C-j
    sleep "$submit_check_sec" || true

    last_prompt_line="$(tmux capture-pane -t "$controller" -p 2>/dev/null | grep -E '^›' | tail -n 1 || true)"
    if [[ "$last_prompt_line" =~ ^›[[:space:]]*$ ]]; then
      return 0
    fi

    sleep "$submit_between_sec" || true
  done

  # Best-effort: leave as draft if still not submitted.
  return 0
}

submit_shell() {
  sleep "$submit_wait_sec_shell" || true
  tmux send-keys -t "$controller" C-m
}

if [[ "$is_codex" == "1" ]]; then
  tmux send-keys -t "$controller" -l "[codex-worker] $msg"
  submit_codex
else
  tmux send-keys -t "$controller" -l "echo '[codex-worker] $msg'"
  submit_shell
fi

# 2) Also show in tmux status line (best-effort)
tmux display-message -d 5000 "[codex-worker] $msg" || true
