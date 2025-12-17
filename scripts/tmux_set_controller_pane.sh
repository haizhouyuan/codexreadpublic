#!/usr/bin/env bash
set -euo pipefail

# Sets a tmux *global environment variable* so worker panes can notify this "controller" pane.
# Run this in the pane you want to receive notifications.

if [[ -z "${TMUX:-}" ]]; then
  echo "Not inside tmux (TMUX is empty). Attach tmux and re-run." >&2
  exit 2
fi

pane_id="$(tmux display-message -p '#{pane_id}')"
session_name="$(tmux display-message -p '#S')"
window_index="$(tmux display-message -p '#I')"
pane_index="$(tmux display-message -p '#P')"

tmux set-environment -g CODEX_CONTROLLER_PANE "$pane_id"
tmux set-environment -g CODEX_CONTROLLER_PANE_FQ "$session_name:$window_index.$pane_index"

echo "CODEX_CONTROLLER_PANE=$pane_id"
echo "CODEX_CONTROLLER_PANE_FQ=$session_name:$window_index.$pane_index"

