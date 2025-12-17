#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  scripts/codex_exec_glm.sh [options] -- <prompt>
  scripts/codex_exec_glm.sh [options] --prompt-file <path>

Runs `codex exec` using BigModel (GLM) as the model provider (OpenAI-compat),
without touching your existing ChatGPT login state.

Options:
  --env-file <path>       Load env vars from file (default: ./.env if present)
  --model <name>          GLM model (default: glm-4.5-flash)
  --base-url <url>        BigModel OpenAI-compat base URL (default: BIGMODEL_API_BASE or https://open.bigmodel.cn/api/paas/v4)
  --sandbox <mode>        Pass through to `codex exec --sandbox <mode>` (default: unset)
  --json                  Output JSONL event stream (`codex exec --json`)
  --prompt-file <path>    Read prompt from file
  --keep-patsnap          Do not disable noisy `mcp_servers.patsnap`
  -h, --help              Show this help

Notes:
  - Keys are read from env only: BIGMODEL_API_KEY must be set (e.g. via .env).
  - On some Linux environments, command execution under restricted sandboxes can fail
    with landlock errors; use `--sandbox danger-full-access` if you need to run shell commands.
  - Codex may warn that `wire_api="chat"` is deprecated; BigModel currently does NOT
    support the OpenAI Responses endpoint (wire_api="responses"), so this script
    intentionally stays on `chat` for now. For long-term stability, prefer this repo's
    `glm_router` MCP (`glm_router_write_file`) for bulk/long outputs.
USAGE
}

env_file="./.env"
model="glm-4.5-flash"
base_url="${BIGMODEL_API_BASE:-https://open.bigmodel.cn/api/paas/v4}"
sandbox_mode=""
use_json=0
prompt_file=""
disable_patsnap=1

while [[ $# -gt 0 ]]; do
  case "$1" in
    --env-file)
      env_file="${2:-}"
      shift 2
      ;;
    --model)
      model="${2:-}"
      shift 2
      ;;
    --base-url)
      base_url="${2:-}"
      shift 2
      ;;
    --sandbox)
      sandbox_mode="${2:-}"
      shift 2
      ;;
    --json)
      use_json=1
      shift
      ;;
    --prompt-file)
      prompt_file="${2:-}"
      shift 2
      ;;
    --keep-patsnap)
      disable_patsnap=0
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    --)
      shift
      break
      ;;
    *)
      break
      ;;
  esac
done

prompt=""
if [[ -n "$prompt_file" ]]; then
  if [[ ! -f "$prompt_file" ]]; then
    echo "[codex_exec_glm] error: prompt file not found: $prompt_file" >&2
    exit 2
  fi
  prompt="$(cat "$prompt_file")"
else
  if [[ $# -gt 0 ]]; then
    prompt="$*"
  fi
fi

if [[ -z "${prompt//[[:space:]]/}" ]]; then
  usage >&2
  exit 2
fi

if [[ -f "$env_file" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$env_file"
  set +a
fi

if [[ -z "${BIGMODEL_API_KEY:-}" ]]; then
  echo "[codex_exec_glm] error: BIGMODEL_API_KEY is not set (env or .env)" >&2
  exit 2
fi

cmd=(npx -y @openai/codex exec)
if [[ -n "$sandbox_mode" ]]; then
  cmd+=(--sandbox "$sandbox_mode")
fi
if [[ "$use_json" -eq 1 ]]; then
  cmd+=(--json)
fi

if [[ "$disable_patsnap" -eq 1 ]]; then
  cmd+=(-c 'mcp_servers.patsnap.enabled=false')
fi

cmd+=(
  -c "model=\"$model\""
  -c 'model_provider="bigmodel"'
  -c 'model_providers.bigmodel.name="BigModel"'
  -c "model_providers.bigmodel.base_url=\"$base_url\""
  -c 'model_providers.bigmodel.env_key="BIGMODEL_API_KEY"'
  -c 'model_providers.bigmodel.wire_api="chat"'
)

cmd+=("$prompt")

exec "${cmd[@]}"
