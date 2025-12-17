#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Dict, Optional

from mcp_streamable_http_client import McpHttpError, mcp_http_call_tool, mcp_http_initialize


REPO_ROOT = Path(__file__).resolve().parents[1]


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _die(msg: str, code: int = 2) -> None:
    print(msg, file=sys.stderr)
    raise SystemExit(code)


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _pick_answer(result: Dict[str, Any]) -> str:
    for key in ("answer", "text", "final_answer", "result", "output"):
        v = result.get(key)
        if isinstance(v, str) and v.strip():
            return v
    return ""


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Call a chatgptMCP HTTP tool (ChatGPT/Gemini web) and write answer to markdown.")
    ap.add_argument("--mcp-url", default="", help="MCP HTTP URL (default: $CHATGPT_MCP_URL or http://127.0.0.1:18701/mcp)")
    ap.add_argument("--tool", default="chatgpt_web_ask_pro_extended", help="Tool name to call.")
    ap.add_argument("--wait-tool", default="", help="Optional wait tool (e.g. chatgpt_web_wait / gemini_web_wait).")
    ap.add_argument("--question", default="", help="Question text (or use --question-file).")
    ap.add_argument("--question-file", default="", help="Read question from file.")
    ap.add_argument("--conversation-url", default="", help="Continue an existing conversation url (optional).")
    ap.add_argument("--timeout-seconds", type=int, default=1200)
    ap.add_argument("--min-chars", type=int, default=0, help="If answer shorter than this, call wait-tool (requires conversation_url).")
    ap.add_argument("--out", default="", help="Write markdown to this path (optional).")
    args = ap.parse_args(argv)

    url = str(args.mcp_url).strip() or os.environ.get("CHATGPT_MCP_URL") or "http://127.0.0.1:18701/mcp"
    tool_name = str(args.tool).strip()
    if not tool_name:
        _die("tool is required")

    question = str(args.question).strip()
    qf = str(args.question_file).strip()
    if qf:
        qpath = Path(qf).expanduser()
        if not qpath.is_absolute():
            qpath = (REPO_ROOT / qpath).resolve(strict=False)
        if not qpath.exists():
            _die(f"question_file not found: {qpath}")
        question = _read_text(qpath).strip()
    if not question:
        _die("question is required (provide --question or --question-file)")

    try:
        session = mcp_http_initialize(url, client_name="codexread_chatgpt_mcp_ask", client_version="0.1", timeout_sec=30.0)
    except Exception as exc:
        _die(f"failed to initialize MCP at {url}: {exc}")

    tool_args: Dict[str, Any] = {"question": question, "timeout_seconds": int(args.timeout_seconds)}
    conv = str(args.conversation_url).strip()
    if conv:
        tool_args["conversation_url"] = conv

    try:
        result = mcp_http_call_tool(session, tool_name=tool_name, tool_args=tool_args, timeout_sec=float(args.timeout_seconds) + 30.0)
    except McpHttpError as exc:
        _die(f"tool call failed ({tool_name}): {exc}")

    answer = _pick_answer(result)
    conv_url = str(result.get("conversation_url") or "").strip()

    min_chars = int(args.min_chars)
    wait_tool = str(args.wait_tool).strip()
    if min_chars > 0 and wait_tool and conv_url and len(answer.strip()) < min_chars:
        time.sleep(5)
        try:
            waited = mcp_http_call_tool(
                session,
                tool_name=wait_tool,
                tool_args={"conversation_url": conv_url, "timeout_seconds": 900, "min_chars": min_chars},
                timeout_sec=930.0,
            )
            answer2 = _pick_answer(waited).strip()
            if len(answer2) > len(answer.strip()):
                answer = answer2
        except Exception:
            pass

    md = []
    md.append("# chatgptMCP response")
    md.append("")
    md.append(f"- ts: {_now_iso()}")
    md.append(f"- tool: `{tool_name}`")
    if conv_url:
        md.append(f"- conversation_url: {conv_url}")
    md.append("")
    md.append("## Answer")
    md.append("")
    md.append(answer.strip())
    md_text = "\n".join(md).rstrip() + "\n"

    out_raw = str(args.out).strip()
    if out_raw:
        out_path = Path(out_raw).expanduser()
        if not out_path.is_absolute():
            out_path = (REPO_ROOT / out_path).resolve(strict=False)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(md_text, encoding="utf-8")
        print(str(out_path))
        return 0

    sys.stdout.write(md_text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

