#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Dict, Iterable, List, Literal, Optional, Tuple, Union
from uuid import uuid4

from task_store import Task, TaskStore, TaskStoreError

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


def _tool(
    *,
    name: str,
    description: str,
    input_schema: Dict[str, Any],
    title: Optional[str] = None,
    output_schema: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    tool: Dict[str, Any] = {
        "name": name,
        "description": description,
        "inputSchema": input_schema,
    }
    if title is not None:
        tool["title"] = title
    if output_schema is not None:
        tool["outputSchema"] = output_schema
    return tool


def _tools_list() -> List[Dict[str, Any]]:
    create_task_schema = {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "One-line task title."},
            "description": {"type": "string", "description": "Optional task context."},
            "category": {
                "type": "string",
                "description": "investing|tech|parenting|personal|other",
            },
            "priority": {"type": "string", "description": "low|medium|high"},
            "tags": {"type": "array", "items": {"type": "string"}},
            "topic_id": {"type": "string", "description": "Stable topic slug (optional)."},
            "source": {"type": "string", "description": "Task source (optional)."},
        },
        "required": ["title"],
    }

    list_tasks_schema = {
        "type": "object",
        "properties": {
            "status": {"type": "string", "description": "pending|in_progress|done|canceled"},
            "category": {"type": "string"},
            "topic_id": {"type": "string"},
            "tags_any": {"type": "array", "items": {"type": "string"}},
            "order_by": {
                "type": "string",
                "description": "created_at_desc|updated_at_desc|priority_desc",
            },
            "limit": {"type": "integer", "minimum": 1, "maximum": 200},
        },
    }

    update_task_status_schema = {
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "status": {"type": "string", "description": "pending|in_progress|done|canceled"},
        },
        "required": ["id", "status"],
    }

    task_output_schema = {
        "type": "object",
        "properties": {
            "task": {"type": "object"},
        },
        "required": ["task"],
    }

    return [
        _tool(
            name="create_task",
            title="Create task",
            description="Create a new task for the user.",
            input_schema=create_task_schema,
            output_schema=task_output_schema,
        ),
        _tool(
            name="list_tasks",
            title="List tasks",
            description="List tasks with optional filters.",
            input_schema=list_tasks_schema,
            output_schema={"type": "object", "properties": {"tasks": {"type": "array"}}, "required": ["tasks"]},
        ),
        _tool(
            name="update_task_status",
            title="Update task status",
            description="Update a task's status.",
            input_schema=update_task_status_schema,
            output_schema=task_output_schema,
        ),
    ]


def handle_initialize(request_id: RequestId, params: Dict[str, Any]) -> None:
    client_protocol = params.get("protocolVersion")
    protocol_version = MCP_PROTOCOL_VERSION if client_protocol in (None, MCP_PROTOCOL_VERSION) else client_protocol

    result = {
        "protocolVersion": protocol_version,
        "serverInfo": {"name": "tasks-mcp", "version": "0.1.0"},
        "capabilities": {
            "tools": {"listChanged": False},
        },
        "instructions": "Provides task management tools: create_task, list_tasks, update_task_status.",
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


def _call_result(*, text: str, structured: Any) -> Dict[str, Any]:
    return {
        "content": [_content_text(text)],
        "structuredContent": structured,
    }


def handle_tools_call(request_id: RequestId, params: Dict[str, Any], store: TaskStore) -> None:
    try:
        tool_name, args = _parse_call_params(params)
    except ValueError as e:
        _send_error(request_id, -32602, str(e))
        return

    try:
        if tool_name == "create_task":
            task = store.create_task(
                title=args.get("title"),
                description=args.get("description"),
                category=args.get("category"),
                priority=args.get("priority"),
                tags=args.get("tags"),
                topic_id=args.get("topic_id"),
                source=args.get("source"),
            )
            _send_result(
                request_id,
                _call_result(text=f"Created task {task.id}", structured={"task": task.to_dict()}),
            )
            return

        if tool_name == "list_tasks":
            tasks = store.list_tasks(
                status=args.get("status"),
                category=args.get("category"),
                topic_id=args.get("topic_id"),
                tags_any=args.get("tags_any"),
                order_by=args.get("order_by"),
                limit=args.get("limit"),
            )
            _send_result(
                request_id,
                _call_result(
                    text=f"Found {len(tasks)} task(s)",
                    structured={"tasks": [task.to_dict() for task in tasks]},
                ),
            )
            return

        if tool_name == "update_task_status":
            task = store.update_task_status(task_id=args.get("id"), status=args.get("status"))
            _send_result(
                request_id,
                _call_result(text=f"Updated task {task.id} -> {task.status}", structured={"task": task.to_dict()}),
            )
            return

        _send_result(
            request_id,
            {
                "content": [_content_text(f"Unknown tool: {tool_name}")],
                "isError": True,
            },
        )
    except TaskStoreError as e:
        _send_result(
            request_id,
            {
                "content": [_content_text(str(e))],
                "isError": True,
            },
        )
    except Exception as e:  # pragma: no cover
        logging.exception("Unhandled error in tool call")
        _send_error(request_id, -32603, "internal error", {"detail": str(e)})


def main(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(prog="tasks-mcp")
    parser.add_argument(
        "--db-path",
        default=os.environ.get("TASKS_DB_PATH", "state/tasks.sqlite"),
        help="SQLite DB path (default: state/tasks.sqlite or $TASKS_DB_PATH)",
    )
    parser.add_argument(
        "--log-level",
        default=os.environ.get("TASKS_LOG_LEVEL", "INFO"),
        help="Log level (stderr) (default: INFO or $TASKS_LOG_LEVEL)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
        stream=sys.stderr,
    )

    store = TaskStore(db_path=args.db_path)
    store.ensure_schema()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as e:
            logging.warning("invalid json: %s", e)
            continue

        if not isinstance(msg, dict):
            continue

        method = msg.get("method")
        request_id = msg.get("id")
        params = msg.get("params", None)

        # Notifications have no id; ignore them.
        if request_id is None:
            continue

        if method == "initialize":
            try:
                handle_initialize(request_id, _as_object(params))
            except Exception as e:  # pragma: no cover
                _send_error(request_id, -32603, "initialize failed", {"detail": str(e)})
            continue

        if method == "ping":
            _send_result(request_id, {})
            continue

        if method == "tools/list":
            handle_tools_list(request_id, _as_object(params))
            continue

        if method == "tools/call":
            handle_tools_call(request_id, _as_object(params), store)
            continue

        # Graceful no-op responses for methods we don't support.
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

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

