#!/usr/bin/env python3
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Iterator, Optional, Tuple


JSONRPCMessage = Dict[str, Any]


@dataclass(frozen=True)
class McpHttpSession:
    url: str
    session_id: Optional[str]
    protocol_version: Optional[str]


class McpHttpError(RuntimeError):
    pass


def _read_header(headers: Dict[str, str], name: str) -> Optional[str]:
    for k, v in headers.items():
        if k.lower() == name.lower():
            return v
    return None


def _iter_sse_events(fp: Any) -> Iterator[Dict[str, str]]:
    """
    Minimal SSE parser for Starlette's EventSourceResponse output.

    Yields dict with keys: event, data, id (optional), retry (optional).
    """
    event: Dict[str, str] = {}
    data_lines: list[str] = []
    while True:
        raw = fp.readline()
        if not raw:
            break
        try:
            line = raw.decode("utf-8", errors="replace")
        except Exception:
            line = str(raw)
        line = line.rstrip("\r\n")
        if not line:
            if data_lines:
                event["data"] = "\n".join(data_lines)
            if event:
                yield dict(event)
            event = {}
            data_lines = []
            continue

        if line.startswith(":"):
            continue

        if ":" in line:
            field, value = line.split(":", 1)
            value = value.lstrip(" ")
        else:
            field, value = line, ""

        field = field.strip()
        if field == "data":
            data_lines.append(value)
        else:
            event[field] = value

    if data_lines:
        event["data"] = "\n".join(data_lines)
    if event:
        yield event


def _jsonrpc_post(
    url: str,
    *,
    message: JSONRPCMessage,
    headers: Dict[str, str],
    timeout_sec: float,
) -> Tuple[int, Dict[str, str], bytes]:
    req = urllib.request.Request(
        url,
        data=json.dumps(message, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=float(timeout_sec)) as resp:
            status = int(getattr(resp, "status", 200))
            resp_headers = {k: v for k, v in resp.headers.items()}
            body = resp.read() if status != 202 else b""
            return status, resp_headers, body
    except urllib.error.HTTPError as e:
        status = int(getattr(e, "code", 500))
        resp_headers = {k: v for k, v in (e.headers.items() if e.headers else [])}
        body = e.read() if hasattr(e, "read") else b""
        return status, resp_headers, body


def _jsonrpc_post_stream(
    url: str,
    *,
    message: JSONRPCMessage,
    headers: Dict[str, str],
    timeout_sec: float,
) -> Tuple[int, Dict[str, str], Any]:
    req = urllib.request.Request(
        url,
        data=json.dumps(message, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        resp = urllib.request.urlopen(req, timeout=float(timeout_sec))
        status = int(getattr(resp, "status", 200))
        resp_headers = {k: v for k, v in resp.headers.items()}
        return status, resp_headers, resp
    except urllib.error.HTTPError as e:
        status = int(getattr(e, "code", 500))
        resp_headers = {k: v for k, v in (e.headers.items() if e.headers else [])}
        return status, resp_headers, e


def _parse_jsonrpc_from_sse(fp: Any, *, want_id: Any) -> JSONRPCMessage:
    for ev in _iter_sse_events(fp):
        if ev.get("event") and ev.get("event") != "message":
            continue
        data = (ev.get("data") or "").strip()
        if not data:
            continue
        try:
            msg = json.loads(data)
        except Exception:
            continue
        if not isinstance(msg, dict):
            continue
        if want_id is None:
            return msg
        if msg.get("id") == want_id:
            return msg
    raise McpHttpError("SSE stream ended without a JSON-RPC response.")


def _prepare_headers(session: McpHttpSession | None) -> Dict[str, str]:
    headers: Dict[str, str] = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    if session and session.session_id:
        headers["mcp-session-id"] = session.session_id
    if session and session.protocol_version:
        headers["mcp-protocol-version"] = session.protocol_version
    return headers


def mcp_http_initialize(
    url: str,
    *,
    client_name: str,
    client_version: str,
    protocol_version: str = "2025-06-18",
    timeout_sec: float = 30.0,
) -> McpHttpSession:
    message: JSONRPCMessage = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": protocol_version,
            "clientInfo": {"name": client_name, "version": client_version},
            "capabilities": {},
        },
    }
    status, resp_headers, fp = _jsonrpc_post_stream(url, message=message, headers=_prepare_headers(None), timeout_sec=timeout_sec)
    if status != 200:
        raise McpHttpError(f"initialize failed: HTTP {status}")
    ctype = (_read_header(resp_headers, "content-type") or "").lower()
    if ctype.startswith("application/json"):
        raw = fp.read()
        msg = json.loads(raw.decode("utf-8", errors="replace"))
    else:
        msg = _parse_jsonrpc_from_sse(fp, want_id=1)

    if not isinstance(msg, dict) or msg.get("id") != 1:
        raise McpHttpError(f"initialize bad response: {str(msg)[:300]}")
    if "error" in msg:
        raise McpHttpError(f"initialize error: {json.dumps(msg['error'], ensure_ascii=False)[:500]}")

    session_id = _read_header(resp_headers, "mcp-session-id")
    negotiated = None
    try:
        result = msg.get("result") or {}
        negotiated = str(result.get("protocolVersion") or "").strip() or None
    except Exception:
        negotiated = None

    session = McpHttpSession(url=url, session_id=session_id, protocol_version=negotiated or protocol_version)

    # Best-effort notifications/initialized (no response expected).
    notif: JSONRPCMessage = {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}
    _jsonrpc_post(url, message=notif, headers=_prepare_headers(session), timeout_sec=timeout_sec)
    return session


def mcp_http_call_tool(
    session: McpHttpSession,
    *,
    tool_name: str,
    tool_args: Dict[str, Any],
    timeout_sec: float = 600.0,
) -> Dict[str, Any]:
    req_id = int(time.time() * 1000) % 10_000_000
    message: JSONRPCMessage = {
        "jsonrpc": "2.0",
        "id": req_id,
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": tool_args},
    }
    status, resp_headers, fp = _jsonrpc_post_stream(session.url, message=message, headers=_prepare_headers(session), timeout_sec=timeout_sec)
    if status == 202:
        raise McpHttpError("tools/call returned 202 Accepted (unexpected for request)")
    if status != 200:
        raw = fp.read() if hasattr(fp, "read") else b""
        snippet = raw.decode("utf-8", errors="replace")[:500].strip()
        raise McpHttpError(f"tools/call failed: HTTP {status} {snippet}")

    ctype = (_read_header(resp_headers, "content-type") or "").lower()
    if ctype.startswith("application/json"):
        raw = fp.read()
        msg = json.loads(raw.decode("utf-8", errors="replace"))
    else:
        msg = _parse_jsonrpc_from_sse(fp, want_id=req_id)

    if not isinstance(msg, dict) or msg.get("id") != req_id:
        raise McpHttpError(f"tools/call bad response: {str(msg)[:300]}")
    if "error" in msg:
        raise McpHttpError(f"tools/call error: {json.dumps(msg['error'], ensure_ascii=False)[:500]}")

    result = msg.get("result") or {}
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        return structured

    # Some MCP servers return tool failures as `{isError: true, content: [...]}` without structuredContent.
    # Surface that error text instead of raising a generic "missing structuredContent".
    if bool(result.get("isError")) and isinstance(result.get("content"), list):
        parts: list[str] = []
        for item in result.get("content") or []:
            if isinstance(item, dict) and item.get("type") == "text":
                text = str(item.get("text") or "").strip()
                if text:
                    parts.append(text)
        if parts:
            snippet = "\n".join(parts)[:1200]
            raise McpHttpError(f"tools/call tool error: {snippet}")

    raise McpHttpError(f"tools/call missing structuredContent: {json.dumps(result, ensure_ascii=False)[:500]}")
