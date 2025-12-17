# video_pipeline MCP server (Python)

Implements `video-pipeline-mcp-spec.md`.

## What it does

- Extract audio + frames locally (ffmpeg)
- Optional ASR (faster-whisper)
- Optional OCR (paddleocr)
- Produce `evidence.json` + `evidence_compact.md` for Codex to write digests/reports

## Run

From repo root:

```bash
python3 mcp-servers/video_pipeline/server.py
```

If you installed dependencies into a venv, run with the venv python:

```bash
.venv/bin/python mcp-servers/video_pipeline/server.py
```

## Codex config (example)

Add to your `$CODEX_HOME/config.toml` (default `~/.codex/config.toml`):

```toml
[mcp_servers.video_pipeline]
command = "python3"
args = ["mcp-servers/video_pipeline/server.py"]
cwd = "/vol1/1000/projects/codexread"
startup_timeout_sec = 60
tool_timeout_sec = 3600
enabled = true
```

## Dependencies (local compute)

Required:
- `ffmpeg`

Optional (recommended on RTX 3090):
- `faster-whisper`
- `paddleocr`

This server will **degrade gracefully** when optional deps are missing (it will skip ASR/OCR and emit warnings in the artifacts).

## Notes

- `analyze_video` accepts optional ASR tuning args: `asr_model`, `asr_device`, `asr_compute_type`.
  - Defaults: `asr_device=auto`, `asr_compute_type=auto` (GPU → `cuda+float16`, CPU → `cpu+int8`)
