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


def _run_stdio_mcp_call(repo_root: Path, *, tool_name: str, tool_args: Dict[str, Any], timeout_sec: float) -> Dict[str, Any]:
    cmd = ["bash", str(repo_root / "scripts" / "run_source_pack_mcp.sh")]
    req_lines = [
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-06-18", "clientInfo": {"name": "source_pack_client", "version": "0.1"}, "capabilities": {}},
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
    cp = subprocess.run(
        cmd,
        input="\n".join(req_lines),
        text=True,
        cwd=str(repo_root),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=max(5.0, float(timeout_sec) + 30.0),
        check=False,
    )
    if cp.returncode != 0:
        raise SystemExit(f"source_pack MCP failed (code={cp.returncode}): {cp.stderr.strip()}")

    messages: List[Dict[str, Any]] = []
    for line in cp.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            messages.append(json.loads(line))
        except Exception:
            continue

    resp = next((m for m in messages if m.get("id") == 2), None)
    if resp is None:
        raise SystemExit(f"Missing response for tool call. stderr={cp.stderr.strip()}")
    if "error" in resp:
        raise SystemExit(json.dumps(resp["error"], ensure_ascii=False))

    result = resp.get("result") or {}
    structured = result.get("structuredContent")
    if not isinstance(structured, dict):
        raise SystemExit(f"Bad tool result: {json.dumps(result, ensure_ascii=False)[:500]}")
    structured["_elapsed_sec"] = round(time.time() - started, 3)
    return structured


def main(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(description="Call source_pack_fetch via stdio and print structured JSON.")
    parser.add_argument("--url", required=True)
    parser.add_argument("--topic", default=None, help="Optional topic_id for grouping.")
    parser.add_argument("--pack-id", default=None)
    parser.add_argument("--out-dir", default=None)
    parser.add_argument("--allow-paid", action="store_true")
    parser.add_argument("--fetchers", default=None, help="Comma-separated fetcher list, e.g. local,jina_reader.")
    parser.add_argument("--timeout-sec", type=float, default=60.0)
    parser.add_argument("--min-chars", type=int, default=800)
    args = parser.parse_args(argv)

    tool_args: Dict[str, Any] = {
        "url": args.url,
        "allow_paid": bool(args.allow_paid),
        "timeout_sec": float(args.timeout_sec),
        "min_chars": int(args.min_chars),
    }
    if args.topic:
        tool_args["topic_id"] = str(args.topic)
    if args.pack_id:
        tool_args["pack_id"] = str(args.pack_id)
    if args.out_dir:
        tool_args["out_dir"] = str(args.out_dir)
    if args.fetchers:
        fetchers = [p.strip() for p in str(args.fetchers).split(",") if p.strip()]
        if fetchers:
            tool_args["fetchers"] = fetchers

    repo_root = _repo_root()
    structured = _run_stdio_mcp_call(repo_root, tool_name="source_pack_fetch", tool_args=tool_args, timeout_sec=args.timeout_sec)
    sys.stdout.write(json.dumps(structured, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

