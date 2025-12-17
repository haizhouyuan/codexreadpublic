#!/usr/bin/env python3
from __future__ import annotations

import json
import logging
import os
import sys
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union


MCP_PROTOCOL_VERSION = "2025-06-18"
JSONRPC_VERSION = "2.0"

RequestId = Union[str, int]


try:
    from mem0 import Memory  # type: ignore
except Exception:  # noqa: BLE001
    Memory = None  # type: ignore[assignment]


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


def _tool(
    *,
    name: str,
    description: str,
    input_schema: Dict[str, Any],
    title: Optional[str] = None,
    output_schema: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    tool: Dict[str, Any] = {"name": name, "description": description, "inputSchema": input_schema}
    if title is not None:
        tool["title"] = title
    if output_schema is not None:
        tool["outputSchema"] = output_schema
    return tool


def _tools_list() -> List[Dict[str, Any]]:
    add_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "user_id": {"type": "string"},
            "kind": {"type": "string"},
            "topic": {"type": "string"},
            "content": {"type": "string"},
            "source": {"type": "string"},
            "agent_id": {"type": "string"},
            "related_entities": {"type": "array", "items": {"type": "string"}},
            "tags": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["user_id", "content"],
    }

    search_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "user_id": {"type": "string"},
            "query": {"type": "string"},
            "agent_id": {"type": "string"},
            "topic": {"type": "string"},
            "k": {"type": "integer", "minimum": 1, "maximum": 50},
        },
        "required": ["user_id", "query"],
    }

    return [
        _tool(
            name="add_memory",
            title="Add memory",
            description="Add a long-term memory item to mem0/OpenMemory.",
            input_schema=add_schema,
            output_schema={"type": "object", "properties": {"id": {"type": "string"}}, "required": []},
        ),
        _tool(
            name="search_memory",
            title="Search memory",
            description="Search long-term memories in mem0/OpenMemory.",
            input_schema=search_schema,
            output_schema={"type": "object", "properties": {"memories": {"type": "array"}}, "required": ["memories"]},
        ),
    ]


def handle_initialize(request_id: RequestId, params: Dict[str, Any]) -> None:
    client_protocol = params.get("protocolVersion")
    protocol_version = MCP_PROTOCOL_VERSION if client_protocol in (None, MCP_PROTOCOL_VERSION) else client_protocol
    result = {
        "protocolVersion": protocol_version,
        "serverInfo": {"name": "mem0-memory-mcp", "version": "0.1.0"},
        "capabilities": {"tools": {"listChanged": False}},
        "instructions": "Provides mem0/OpenMemory tools: add_memory, search_memory.",
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


def _truthy_env(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _default_agent_id() -> str:
    return (os.environ.get("MEM0_AGENT_ID") or "codexread").strip()


def _origin_project() -> str:
    return (os.environ.get("MEM0_ORIGIN_PROJECT") or "codexread").strip()


def _expand_env(obj: Any) -> Any:
    if isinstance(obj, str):
        return os.path.expandvars(obj)
    if isinstance(obj, list):
        return [_expand_env(x) for x in obj]
    if isinstance(obj, dict):
        return {str(k): _expand_env(v) for k, v in obj.items()}
    return obj


@dataclass(frozen=True)
class Mem0Runtime:
    memory: Any
    lock: threading.Lock


_RUNTIME: Mem0Runtime | None = None


def _get_runtime() -> Mem0Runtime:
    global _RUNTIME
    if _RUNTIME is not None:
        return _RUNTIME

    if not _truthy_env("MEM0_ENABLED", False):
        raise RuntimeError("mem0 is disabled (set MEM0_ENABLED=true)")
    if Memory is None:
        raise RuntimeError("mem0 library not installed (missing `from mem0 import Memory`)")

    config_path = (os.environ.get("MEM0_CONFIG_PATH") or "").strip()
    memory_obj: Any | None = None

    if config_path:
        cfg_path = Path(config_path).expanduser()
        if not cfg_path.exists():
            raise RuntimeError(f"mem0 config not found: {cfg_path}")

        # Prefer in-process YAML load so we can expand ${ENV} reliably across mem0 versions.
        try:
            import yaml  # type: ignore

            raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
            raw = _expand_env(raw)
            factory_cfg = getattr(Memory, "from_config", None)
            if callable(factory_cfg):
                memory_obj = factory_cfg(raw)
        except Exception:
            memory_obj = None

        if memory_obj is None:
            factory_file = getattr(Memory, "from_config_file", None)
            if not callable(factory_file):
                raise RuntimeError("mem0 does not support from_config/from_config_file in this environment")
            memory_obj = factory_file(str(cfg_path))

    if memory_obj is None:
        memory_obj = Memory()  # type: ignore[call-arg]

    _RUNTIME = Mem0Runtime(memory=memory_obj, lock=threading.Lock())
    return _RUNTIME


def _mem0_add(memory: Any, *, text: str, user_id: str, agent_id: str, metadata: Dict[str, Any]) -> Any:
    """
    mem0 API compatibility:
    - Newer mem0 supports `agent_id` and `infer`.
    - Older versions may not support some kwargs.
    """
    try:
        return memory.add(text, user_id=user_id, agent_id=agent_id, metadata=metadata, infer=False)
    except TypeError:
        pass
    try:
        return memory.add(text, user_id=user_id, agent_id=agent_id, metadata=metadata)
    except TypeError:
        pass
    try:
        return memory.add(text, user_id=user_id, metadata=metadata, infer=False)
    except TypeError:
        return memory.add(text, user_id=user_id, metadata=metadata)


def _mem0_search(
    memory: Any,
    *,
    query: str,
    user_id: str,
    agent_id: str,
    filters: Dict[str, Any] | None,
    k: int,
) -> Any:
    try:
        return memory.search(query, user_id=user_id, agent_id=agent_id, filters=filters, limit=k)
    except TypeError:
        pass
    try:
        return memory.search(query, user_id=user_id, agent_id=agent_id, filters=filters, k=k)
    except TypeError:
        pass
    try:
        return memory.search(query, user_id=user_id, filters=filters, limit=k)
    except TypeError:
        return memory.search(query, user_id=user_id, limit=k)


def handle_tools_call(request_id: RequestId, params: Dict[str, Any]) -> None:
    try:
        tool_name, args = _parse_call_params(params)
    except ValueError as e:
        _send_error(request_id, -32602, str(e))
        return

    try:
        rt = _get_runtime()
    except Exception as e:
        _send_error(request_id, -32603, str(e))
        return

    if tool_name == "add_memory":
        user_id = str(args.get("user_id", "")).strip()
        content = str(args.get("content", "")).strip()
        if not user_id or not content:
            _send_error(request_id, -32602, "user_id and content are required")
            return

        kind = str(args.get("kind", "")).strip() or None
        topic = str(args.get("topic", "")).strip() or None
        source = str(args.get("source", "")).strip() or None
        agent_id = str(args.get("agent_id", "")).strip() or _default_agent_id()
        related_entities = args.get("related_entities") if isinstance(args.get("related_entities"), list) else None
        tags = args.get("tags") if isinstance(args.get("tags"), list) else None

        metadata: Dict[str, Any] = {
            "agent_id": agent_id,
            "written_at": _now_iso(),
            "origin": "codexread",
            "origin_project": _origin_project(),
        }
        if kind:
            metadata["kind"] = kind
        if topic:
            metadata["topic"] = topic
        if source:
            metadata["source"] = source
        if related_entities:
            metadata["related_entities"] = [str(x) for x in related_entities if str(x).strip()]
        if tags:
            metadata["tags"] = [str(x) for x in tags if str(x).strip()]

        try:
            with rt.lock:
                result = _mem0_add(rt.memory, text=content, user_id=user_id, agent_id=agent_id, metadata=metadata)
        except Exception as e:
            logging.exception("mem0 add failed")
            _send_error(request_id, -32603, "add_memory failed", {"detail": str(e)})
            return

        memory_id: str | None = None
        if isinstance(result, dict):
            memory_id = str(result.get("id") or result.get("memory_id") or result.get("memoryId") or "").strip() or None
            if memory_id is None:
                raw_items = result.get("results")
                if isinstance(raw_items, list) and raw_items:
                    first = raw_items[0] if isinstance(raw_items[0], dict) else {}
                    memory_id = (
                        str(first.get("id") or first.get("memory_id") or first.get("memoryId") or "").strip() or None
                    )
        elif isinstance(result, list) and result:
            first = result[0] if isinstance(result[0], dict) else {}
            memory_id = str(first.get("id") or first.get("memory_id") or first.get("memoryId") or "").strip() or None
        elif isinstance(result, str):
            memory_id = result.strip() or None

        structured = {"id": memory_id, "status": "ok"}
        _send_result(request_id, _call_result(text="Memory added", structured=structured))
        return

    if tool_name == "search_memory":
        user_id = str(args.get("user_id", "")).strip()
        query = str(args.get("query", "")).strip()
        if not user_id or not query:
            _send_error(request_id, -32602, "user_id and query are required")
            return

        k = int(args.get("k", 10) or 10)
        k = max(1, min(50, k))
        agent_id_raw = str(args.get("agent_id", "")).strip()
        agent_id = agent_id_raw or _default_agent_id()
        topic = str(args.get("topic", "")).strip() or None

        filters: Dict[str, Any] = {"agent_id": agent_id} if agent_id else {}
        if topic:
            filters["topic"] = topic

        try:
            with rt.lock:
                results = _mem0_search(
                    rt.memory,
                    query=query,
                    user_id=user_id,
                    agent_id=agent_id,
                    filters=filters,
                    k=k,
                )
        except Exception as e:
            logging.exception("mem0 search failed")
            _send_error(request_id, -32603, "search_memory failed", {"detail": str(e)})
            return

        items: List[dict] = []
        if isinstance(results, dict):
            raw = results.get("results") or results.get("memories") or []
            items = raw if isinstance(raw, list) else []
        elif isinstance(results, list):
            items = results

        memories: List[Dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            text = (item.get("memory") or item.get("text") or item.get("content") or item.get("data") or "").strip()
            if not text:
                continue
            md = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            score = item.get("score")
            memories.append(
                {
                    "id": item.get("id") or item.get("memory_id") or item.get("memoryId"),
                    "user_id": user_id,
                    "kind": md.get("kind"),
                    "topic": md.get("topic"),
                    "content": text,
                    "source": md.get("source"),
                    "related_entities": md.get("related_entities"),
                    "tags": md.get("tags"),
                    "score": score,
                }
            )

        structured = {"memories": memories}
        _send_result(request_id, _call_result(text=f"Found {len(memories)} memory item(s)", structured=structured))
        return

    _send_result(request_id, {"content": [_content_text(f"Unknown tool: {tool_name}")], "isError": True})


def main(argv: List[str]) -> int:
    logging.basicConfig(level=logging.INFO)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception:
            continue

        if not isinstance(msg, dict):
            continue

        method = msg.get("method")
        if not isinstance(method, str):
            continue
        request_id = msg.get("id")
        params = _as_object(msg.get("params", {}))

        if request_id is None:
            continue

        try:
            if method == "initialize":
                handle_initialize(request_id, params)
            elif method == "tools/list":
                handle_tools_list(request_id, params)
            elif method == "tools/call":
                handle_tools_call(request_id, params)
            else:
                _send_error(request_id, -32601, f"Method not found: {method}")
        except Exception as e:
            _send_error(request_id, -32603, "Internal error", {"detail": str(e)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
