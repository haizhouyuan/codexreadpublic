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
    cmd = ["bash", str(repo_root / "scripts" / "run_websearch_router_mcp.sh")]
    req_lines = [
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-06-18", "clientInfo": {"name": "websearch_client", "version": "0.1"}, "capabilities": {}},
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
        raise SystemExit(f"websearch_router MCP failed (code={cp.returncode}): {cp.stderr.strip()}")

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
    parser = argparse.ArgumentParser(description="Call websearch_router_search via stdio and print structured JSON.")
    parser.add_argument("--query", required=True)
    parser.add_argument("--max-results", type=int, default=5)
    parser.add_argument("--min-results", type=int, default=None)
    parser.add_argument("--language", default="auto", choices=["auto", "en", "zh-hans", "zh-hant"])
    parser.add_argument("--recency", default="noLimit", choices=["noLimit", "oneDay", "oneWeek", "oneMonth", "oneYear"])
    parser.add_argument("--domain-filter", default=None)
    parser.add_argument("--allow-paid", action="store_true")
    parser.add_argument("--timeout-sec", type=float, default=30.0)
    parser.add_argument("--no-cache", action="store_true", help="Disable cache for this request.")
    args = parser.parse_args(argv)

    repo_root = _repo_root()
    tool_args: Dict[str, Any] = {
        "query": args.query,
        "max_results": int(args.max_results),
        "language": args.language,
        "recency": args.recency,
        "allow_paid": bool(args.allow_paid),
        "timeout_sec": float(args.timeout_sec),
        "use_cache": not bool(args.no_cache),
    }
    if args.min_results is not None:
        tool_args["min_results"] = int(args.min_results)
    if args.domain_filter:
        tool_args["domain_filter"] = str(args.domain_filter)

    structured = _run_stdio_mcp_call(repo_root, tool_name="websearch_router_search", tool_args=tool_args, timeout_sec=args.timeout_sec)
    sys.stdout.write(json.dumps(structured, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

