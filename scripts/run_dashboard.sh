#!/usr/bin/env bash
set -euo pipefail

# Research Dashboard (read-only)
# Default: http://127.0.0.1:8787

PY="${PY:-.venv/bin/python}"

if [[ ! -x "$PY" ]]; then
  echo "Missing python venv at $PY" >&2
  echo "Create it and install deps:" >&2
  echo "  python3 -m venv .venv" >&2
  echo "  .venv/bin/pip install -r apps/dashboard/requirements.txt" >&2
  exit 1
fi

PORT="${CODEXREAD_DASH_PORT:-8787}"
if command -v ss >/dev/null 2>&1; then
  if ss -ltnp 2>/dev/null | grep -qE "LISTEN[[:space:]]+[0-9]+[[:space:]]+[0-9]+[[:space:]]+.*:${PORT}\\b"; then
    echo "Port ${PORT} is already in use:" >&2
    ss -ltnp 2>/dev/null | grep -E "LISTEN[[:space:]]+[0-9]+[[:space:]]+[0-9]+[[:space:]]+.*:${PORT}\\b" >&2 || true
    exit 2
  fi
fi

exec "$PY" apps/dashboard/run.py
