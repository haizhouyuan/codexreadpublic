#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _build_payload(args: argparse.Namespace) -> Dict[str, Any]:
    validate: Dict[str, Any] = {}
    if args.validate_must_have:
        validate["must_have_substrings"] = list(args.validate_must_have)
    if args.validate_min_chars is not None:
        validate["min_chars"] = int(args.validate_min_chars)
    if args.validate_max_chars is not None:
        validate["max_chars"] = int(args.validate_max_chars)

    payload: Dict[str, Any] = {
        "expect": args.expect,
        "family": args.family,
        "instructions": args.instructions,
        "output_path": args.output_path,
        "overwrite": bool(args.overwrite),
        "allow_paid": bool(args.allow_paid),
        "timeout_sec": float(args.timeout_sec),
        "max_retries": int(args.max_retries),
        "preview_chars": int(args.preview_chars),
    }
    if args.system:
        payload["system"] = args.system
    if args.template_path:
        payload["template_path"] = args.template_path
    if args.input_path:
        payload["input_paths"] = list(args.input_path)
    if validate:
        payload["validate"] = validate
    if args.max_input_bytes_per_file is not None:
        payload["max_input_bytes_per_file"] = int(args.max_input_bytes_per_file)
    if args.meta_json:
        payload["meta"] = json.loads(args.meta_json)
    return payload


def _run_stdio_mcp_call(repo_root: Path, *, tool_name: str, tool_args: Dict[str, Any], timeout_sec: float) -> Dict[str, Any]:
    cmd = ["bash", str(repo_root / "scripts" / "run_glm_router_mcp.sh")]
    req_lines = [
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-06-18", "clientInfo": {"name": "glm_write_file", "version": "0.1"}, "capabilities": {}},
            },
            ensure_ascii=False,
        ),
        json.dumps(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": tool_name, "arguments": tool_args}},
            ensure_ascii=False,
        ),
        "",
    ]
    started = time.time()
    tool_max_retries = int(tool_args.get("max_retries") or 0)
    # Overall subprocess budget should cover multiple model retries + backoff inside glm_router.
    # We deliberately over-approximate to avoid killing long paid-model calls.
    overall_timeout = max(5.0, float(timeout_sec) * (tool_max_retries + 1) + 60.0)
    cp = subprocess.run(
        cmd,
        input="\n".join(req_lines),
        text=True,
        cwd=str(repo_root),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=overall_timeout,
    )

    # Parse JSON-RPC responses from stdout.
    messages: List[Dict[str, Any]] = []
    for line in cp.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            messages.append(json.loads(line))
        except Exception:
            continue

    resp2 = next((m for m in messages if m.get("id") == 2), None)
    if resp2 is None:
        raise SystemExit(f"Missing response for tool call. stderr={cp.stderr.strip()}")
    if "error" in resp2:
        raise SystemExit(json.dumps(resp2["error"], ensure_ascii=False))

    result = resp2.get("result") or {}
    structured = result.get("structuredContent")
    if not isinstance(structured, dict):
        raise SystemExit(f"Bad tool result: {json.dumps(result, ensure_ascii=False)[:500]}")

    structured["_elapsed_sec"] = round(time.time() - started, 3)
    return structured


def main(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(description="Call glm_router_write_file via stdio and print structured JSON result.")
    parser.add_argument("--expect", default="text", choices=["text", "json"])
    parser.add_argument("--family", default="text", choices=["text", "vision", "auto"])
    parser.add_argument("--system", default=None)
    parser.add_argument("--instructions", required=True)
    parser.add_argument("--template-path", default=None)
    parser.add_argument("--input-path", action="append", default=[])
    parser.add_argument("--output-path", required=True)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--allow-paid", action="store_true")
    parser.add_argument("--timeout-sec", type=float, default=120.0)
    parser.add_argument("--max-retries", type=int, default=1)
    parser.add_argument("--preview-chars", type=int, default=200)
    parser.add_argument("--max-input-bytes-per-file", type=int, default=None)
    parser.add_argument("--validate-must-have", action="append", default=[])
    parser.add_argument("--validate-min-chars", type=int, default=None)
    parser.add_argument("--validate-max-chars", type=int, default=None)
    parser.add_argument("--meta-json", default=None, help="Optional JSON object string for meta.")
    args = parser.parse_args(argv)

    repo_root = _repo_root()
    tool_args = _build_payload(args)
    structured = _run_stdio_mcp_call(repo_root, tool_name="glm_router_write_file", tool_args=tool_args, timeout_sec=args.timeout_sec)
    sys.stdout.write(json.dumps(structured, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
