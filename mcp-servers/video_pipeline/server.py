#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from pipeline import analyze_video, safe_slug


MCP_PROTOCOL_VERSION = "2025-06-18"
JSONRPC_VERSION = "2.0"

RequestId = Union[str, int]


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _write_message(message: Dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _send_result(request_id: RequestId, result: Any) -> None:
    _write_message({"jsonrpc": JSONRPC_VERSION, "id": request_id, "result": result})


def _send_error(request_id: RequestId, code: int, message: str, data: Any | None = None) -> None:
    err: Dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    _write_message({"jsonrpc": JSONRPC_VERSION, "id": request_id, "error": err})


def _as_object(value: Any) -> Dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    raise ValueError("expected object")


def _content_text(text: str) -> Dict[str, str]:
    return {"type": "text", "text": text}


def _call_result(*, text: str, structured: Any) -> Dict[str, Any]:
    return {"content": [_content_text(text)], "structuredContent": structured}


def _tool(*, name: str, description: str, input_schema: Dict[str, Any], title: Optional[str] = None) -> Dict[str, Any]:
    tool: Dict[str, Any] = {"name": name, "description": description, "inputSchema": input_schema}
    if title is not None:
        tool["title"] = title
    return tool


def _tools_list() -> List[Dict[str, Any]]:
    analyze_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "video_path": {"type": "string", "description": "Local video path."},
            "analysis_id": {"type": "string", "description": "Optional analysis id (used for output dir name)."},
            "out_dir": {"type": "string", "description": "Optional output directory."},
            "lang": {"type": "string", "description": "ASR language code (default: zh)."},
            "asr_model": {"type": "string", "description": "ASR model name (faster-whisper). Default: large-v3."},
            "asr_device": {"type": "string", "description": "ASR device: auto|cuda|cpu (default: auto)."},
            "asr_compute_type": {"type": "string", "description": "ASR compute_type: auto|float16|int8|... (default: auto)."},
            "asr_vad_filter": {"type": "boolean", "description": "Enable VAD filtering for ASR (default: true)."},
            "frame_every_sec": {"type": "number", "minimum": 0.1, "description": "Extract one frame every N seconds."},
            "max_height": {"type": "integer", "minimum": 0, "description": "Cap extracted frame height (0=keep)."},
            "enable_asr": {"type": "boolean"},
            "enable_frames": {"type": "boolean"},
            "enable_ocr": {"type": "boolean"},
            "ocr_mode": {"type": "string", "description": "numeric_only (v1)."},
            "dry_run": {"type": "boolean"},
            "overwrite": {"type": "boolean"},
        },
        "required": ["video_path"],
    }

    return [
        _tool(
            name="analyze_video",
            title="Analyze video",
            description="Run local video pipeline and produce evidence artifacts (evidence.json/evidence_compact.md).",
            input_schema=analyze_schema,
        )
    ]


def handle_initialize(request_id: RequestId, params: Dict[str, Any]) -> None:
    client_protocol = params.get("protocolVersion")
    protocol_version = MCP_PROTOCOL_VERSION if client_protocol in (None, MCP_PROTOCOL_VERSION) else client_protocol
    result = {
        "protocolVersion": protocol_version,
        "serverInfo": {"name": "video_pipeline-mcp", "version": "0.1.0"},
        "capabilities": {"tools": {"listChanged": False}},
        "instructions": "Provides video pipeline tool: analyze_video.",
    }
    _send_result(request_id, result)


def handle_tools_list(request_id: RequestId, params: Dict[str, Any]) -> None:
    _send_result(request_id, {"tools": _tools_list(), "nextCursor": None})


def _parse_call_params(params: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    name = params.get("name")
    if not isinstance(name, str) or not name:
        raise ValueError("tools/call.params.name must be a non-empty string")
    args_raw = params.get("arguments", {})
    args = _as_object(args_raw)
    return name, args


def _default_out_dir(analysis_id: str) -> Path:
    return Path("state") / "video-analyses" / analysis_id


def _truthy_env(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _is_within_dir(path: Path, base_dir: Path) -> bool:
    try:
        path = path.resolve()
        base_dir = base_dir.resolve()
    except Exception:
        return False
    return path == base_dir or base_dir in path.parents


def handle_tools_call(request_id: RequestId, params: Dict[str, Any]) -> None:
    try:
        tool_name, args = _parse_call_params(params)
    except ValueError as e:
        _send_error(request_id, -32602, str(e))
        return

    if tool_name != "analyze_video":
        _send_result(request_id, {"content": [_content_text(f"Unknown tool: {tool_name}")], "isError": True})
        return

    video_path = Path(str(args.get("video_path", "")).strip())
    if not str(video_path):
        _send_error(request_id, -32602, "video_path is required")
        return

    analysis_id_raw = str(args.get("analysis_id", "")).strip()
    if analysis_id_raw:
        analysis_id = safe_slug(analysis_id_raw)
    else:
        base = safe_slug(video_path.stem)
        analysis_id = f"{_now_iso()[:10]}_{base}"

    out_dir_raw = str(args.get("out_dir", "")).strip()
    out_dir = Path(out_dir_raw).expanduser() if out_dir_raw else _default_out_dir(analysis_id)
    base_dir = Path("state") / "video-analyses"
    allow_outside = _truthy_env("VIDEO_PIPELINE_ALLOW_OUTSIDE_STATE")
    if not allow_outside and not _is_within_dir(out_dir, base_dir):
        _send_error(request_id, -32602, f"out_dir must be under {base_dir} (set VIDEO_PIPELINE_ALLOW_OUTSIDE_STATE=1 to override)")
        return

    lang = str(args.get("lang", "zh") or "zh")
    asr_model = str(args.get("asr_model", "large-v3") or "large-v3")
    asr_device = str(args.get("asr_device", "auto") or "auto")
    asr_compute_type = str(args.get("asr_compute_type", "auto") or "auto")
    asr_vad_filter = bool(args.get("asr_vad_filter", True))
    frame_every_sec = float(args.get("frame_every_sec", 5.0) or 5.0)
    max_height = int(args.get("max_height", 1080) or 1080)
    enable_asr = bool(args.get("enable_asr", True))
    enable_frames = bool(args.get("enable_frames", True))
    enable_ocr = bool(args.get("enable_ocr", True))
    ocr_mode = str(args.get("ocr_mode", "numeric_only") or "numeric_only")
    dry_run = bool(args.get("dry_run", False))
    overwrite = bool(args.get("overwrite", False))

    try:
        evidence = analyze_video(
            video_path=video_path,
            out_dir=out_dir,
            analysis_id=analysis_id,
            lang=lang,
            frame_every_sec=frame_every_sec,
            max_height=max_height,
            enable_asr=enable_asr,
            enable_frames=enable_frames,
            enable_ocr=enable_ocr,
            ocr_mode=ocr_mode,
            dry_run=dry_run,
            overwrite=overwrite,
            asr_model=asr_model,
            asr_device=asr_device,
            asr_compute_type=asr_compute_type,
            asr_vad_filter=asr_vad_filter,
        )
    except FileNotFoundError:
        _send_error(request_id, -32602, f"video not found: {video_path}")
        return
    except Exception as e:
        logging.exception("analyze_video failed")
        _send_error(request_id, -32603, "analyze_video failed", {"detail": str(e)})
        return

    artifacts = evidence.get("artifacts", {})
    evidence_json = str(out_dir / "evidence.json")
    evidence_compact_md = str(out_dir / "evidence_compact.md")
    text = (
        f"Video analyzed: {analysis_id}\n"
        f"- out_dir: {out_dir}\n"
        f"- evidence.json: {evidence_json}\n"
        f"- evidence_compact.md: {evidence_compact_md}\n"
    )
    structured = {
        "analysis_id": analysis_id,
        "out_dir": str(out_dir),
        "artifacts": {
            "evidence_json": artifacts.get("evidence_json", evidence_json),
            "evidence_compact_md": artifacts.get("evidence_compact_md", evidence_compact_md),
            "transcript_json": artifacts.get("transcript_json"),
            "frames_dir": artifacts.get("frames_dir"),
            "ocr_jsonl": artifacts.get("ocr_jsonl"),
            "key_metrics_csv": artifacts.get("key_metrics_csv"),
        },
        "stats": evidence.get("stats", {}),
        "warnings": evidence.get("warnings", []),
    }
    _send_result(request_id, _call_result(text=text, structured=structured))


def main(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(prog="video_pipeline-mcp")
    parser.add_argument(
        "--log-level",
        default=os.environ.get("VIDEO_PIPELINE_LOG_LEVEL", "INFO"),
        help="Log level (stderr) (default: INFO or $VIDEO_PIPELINE_LOG_LEVEL)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
        stream=sys.stderr,
    )

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(msg, dict):
            continue

        method = msg.get("method")
        request_id = msg.get("id")
        params = msg.get("params", None)

        # Notifications have no id; ignore them.
        if request_id is None:
            continue

        try:
            if method == "initialize":
                handle_initialize(request_id, _as_object(params))
                continue
            if method == "ping":
                _send_result(request_id, {})
                continue
            if method == "tools/list":
                handle_tools_list(request_id, _as_object(params))
                continue
            if method == "tools/call":
                handle_tools_call(request_id, _as_object(params))
                continue

            # No-op for unused methods.
            if method == "resources/list":
                _send_result(request_id, {"resources": [], "nextCursor": None})
                continue
            if method == "resources/templates/list":
                _send_result(request_id, {"resourceTemplates": [], "nextCursor": None})
                continue
            if method == "prompts/list":
                _send_result(request_id, {"prompts": [], "nextCursor": None})
                continue

            _send_error(request_id, -32601, f"method not found: {method}")
        except Exception as e:  # pragma: no cover
            logging.exception("Unhandled error")
            _send_error(request_id, -32603, "internal error", {"detail": str(e)})

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
