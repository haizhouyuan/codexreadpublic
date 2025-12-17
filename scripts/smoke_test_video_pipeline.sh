#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_DIR="$REPO_ROOT/state/tmp"
VIDEO="$TMP_DIR/video_test.mp4"

mkdir -p "$TMP_DIR"

# Create a tiny local test video (2s) with audio using ffmpeg.
ffmpeg -y \
  -f lavfi -i testsrc=duration=2:size=320x240:rate=25 \
  -f lavfi -i sine=frequency=1000:duration=2 \
  -c:v libx264 -pix_fmt yuv420p \
  -c:a aac -shortest \
  -loglevel error \
  "$VIDEO"

# Dry-run only (no heavy deps required).
python3 "$REPO_ROOT/mcp-servers/video_pipeline/server.py" <<EOF
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","clientInfo":{"name":"smoke-test","version":"0.0"},"capabilities":{}}}
{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}
{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"analyze_video","arguments":{"video_path":"$VIDEO","dry_run":true}}}
EOF

