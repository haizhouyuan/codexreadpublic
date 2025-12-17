#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import random
import socket
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple, Union

MCP_PROTOCOL_VERSION = "2025-06-18"
JSONRPC_VERSION = "2.0"

RequestId = Union[str, int]

DEFAULT_API_BASE = "https://open.bigmodel.cn/api/paas/v4"
DEFAULT_TIMEOUT_SEC = 60.0
DEFAULT_MAX_INPUT_BYTES_PER_FILE = 200_000
DEFAULT_PREVIEW_CHARS = 200
MAX_PREVIEW_CHARS = 2000
MAX_RETRIES_MAX = 5


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _env_bool(name: str, default: bool) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    if raw == "":
        return default
    return raw not in ("0", "false", "no", "off")


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


def _as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    raise ValueError("expected list")


def _content_text(text: str) -> Dict[str, str]:
    return {"type": "text", "text": text}


def _tool(
    *,
    name: str,
    title: str,
    description: str,
    input_schema: Dict[str, Any],
    output_schema: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        "name": name,
        "title": title,
        "description": description,
        "inputSchema": input_schema,
        "outputSchema": output_schema,
    }


def _tools_list() -> List[Dict[str, Any]]:
    chat_input_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "expect": {"type": "string", "description": "text|json (default: text)"},
            "family": {"type": "string", "description": "auto|text|vision (default: auto)"},
            "system": {"type": "string", "description": "Optional system prompt."},
            "user": {"type": "string", "description": "User prompt (required unless messages is provided)."},
            "image_url": {"type": "string", "description": "Optional image url for vision prompts."},
            "messages": {"type": "array", "items": {"type": "object"}, "description": "Optional OpenAI chat messages."},
            "allow_paid": {"type": "boolean", "description": "Allow paid fallback (default from env)."},
            "timeout_sec": {"type": "number", "minimum": 1, "maximum": 600, "description": "HTTP timeout seconds."},
            "meta": {"type": "object", "description": "Opaque metadata echoed back."},
        },
    }

    chat_output_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "text": {"type": "string"},
            "json": {},
            "used_model": {"type": "string"},
            "used_tier": {"type": "string", "description": "free|paid"},
            "attempts": {"type": "array"},
            "meta": {"type": "object"},
        },
        "required": ["text", "used_model", "used_tier", "attempts"],
    }

    write_validate_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "must_have_substrings": {"type": "array", "items": {"type": "string"}},
            "min_chars": {"type": "integer", "minimum": 0},
            "max_chars": {"type": "integer", "minimum": 1},
        },
    }

    write_input_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "expect": {"type": "string", "description": "text|json (default: text)"},
            "family": {"type": "string", "description": "text|vision|auto (default: text)"},
            "system": {"type": "string", "description": "Optional system prompt."},
            "instructions": {"type": "string", "description": "Writing instructions (required)."},
            "input_paths": {"type": "array", "items": {"type": "string"}, "description": "Optional input file paths."},
            "template_path": {"type": "string", "description": "Optional template file path."},
            "output_path": {"type": "string", "description": "Output file path (required)."},
            "overwrite": {"type": "boolean", "description": "Overwrite existing output_path (default: false)."},
            "validate": write_validate_schema,
            "preview_chars": {"type": "integer", "minimum": 0, "maximum": MAX_PREVIEW_CHARS},
            "max_input_bytes_per_file": {"type": "integer", "minimum": 1},
            "allow_paid": {"type": "boolean", "description": "Allow paid fallback (default from env)."},
            "timeout_sec": {"type": "number", "minimum": 1, "maximum": 600, "description": "HTTP timeout seconds."},
            "max_retries": {"type": "integer", "minimum": 0, "maximum": MAX_RETRIES_MAX},
            "meta": {"type": "object", "description": "Opaque metadata echoed back."},
        },
    }

    write_output_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "output_path": {"type": "string"},
            "bytes": {"type": "integer"},
            "sha256": {"type": "string"},
            "chars": {"type": "integer"},
            "used_model": {"type": "string"},
            "used_tier": {"type": "string", "description": "free|paid"},
            "attempts": {"type": "array"},
            "validation": {"type": "object"},
            "preview": {"type": "string"},
            "meta": {"type": "object"},
        },
        "required": ["output_path", "bytes", "sha256", "chars", "used_model", "used_tier", "attempts", "validation"],
    }

    return [
        _tool(
            name="glm_router_chat",
            title="GLM Router Chat",
            description="Call GLM via BigModel API with free→paid fallback; optionally parse JSON output.",
            input_schema=chat_input_schema,
            output_schema=chat_output_schema,
        ),
        _tool(
            name="glm_router_write_file",
            title="GLM Router Write File",
            description="Read local files, generate long text/JSON via GLM, write to output_path; returns only metadata+preview.",
            input_schema=write_input_schema,
            output_schema=write_output_schema,
        )
    ]


def handle_initialize(request_id: RequestId, params: Dict[str, Any]) -> None:
    client_protocol = params.get("protocolVersion")
    protocol_version = MCP_PROTOCOL_VERSION if client_protocol in (None, MCP_PROTOCOL_VERSION) else client_protocol
    _send_result(
        request_id,
        {
            "protocolVersion": protocol_version,
            "serverInfo": {"name": "glm-router-mcp", "version": "0.2.0"},
            "capabilities": {"tools": {"listChanged": False}},
            "instructions": "Provides glm_router_chat and glm_router_write_file (free→paid fallback) for low-cost structured processing.",
        },
    )


def handle_tools_list(request_id: RequestId, _params: Dict[str, Any]) -> None:
    _send_result(request_id, {"tools": _tools_list(), "nextCursor": None})


def _parse_call_params(params: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    name = params.get("name")
    if not isinstance(name, str) or not name:
        raise ValueError("tools/call.params.name must be a non-empty string")
    args_raw = params.get("arguments", {})
    args = _as_object(args_raw)
    return name, args


def _call_result(*, text: str, structured: Any, is_error: bool = False) -> Dict[str, Any]:
    out: Dict[str, Any] = {"content": [_content_text(text)], "structuredContent": structured}
    if is_error:
        out["isError"] = True
    return out


def _get_api_base(cli_value: str | None) -> str:
    value = (cli_value or os.environ.get("BIGMODEL_API_BASE") or DEFAULT_API_BASE).strip()
    return value or DEFAULT_API_BASE


def _get_api_key() -> str:
    value = (os.environ.get("BIGMODEL_API_KEY") or "").strip()
    if not value:
        raise RuntimeError("Missing BIGMODEL_API_KEY in environment")
    return value


def _http_post_json(url: str, *, api_key: str, payload: Dict[str, Any], timeout_sec: float) -> Tuple[int, str]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url=url,
        method="POST",
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "codexread-glm-router/0.1",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return int(getattr(resp, "status", 200)), body
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return int(getattr(e, "code", 0) or 0), body
    except (urllib.error.URLError, TimeoutError, socket.timeout) as e:
        # Treat transport timeouts/errors as non-200 for fallback handling.
        return 0, json.dumps({"error": "transport_error", "detail": str(e)}, ensure_ascii=False)


def _call_chat_completions(
    *,
    api_base: str,
    api_key: str,
    model: str,
    messages: List[Dict[str, Any]],
    timeout_sec: float,
) -> Tuple[int, Dict[str, Any], float]:
    payload: Dict[str, Any] = {"model": model, "messages": messages}
    started = time.time()
    status, body = _http_post_json(
        f"{api_base.rstrip('/')}/chat/completions",
        api_key=api_key,
        payload=payload,
        timeout_sec=timeout_sec,
    )
    elapsed_ms = (time.time() - started) * 1000.0
    try:
        return status, json.loads(body), elapsed_ms
    except json.JSONDecodeError:
        return status, {"_raw": body}, elapsed_ms


def _extract_assistant_content(data: Dict[str, Any]) -> str:
    try:
        choice0 = (data.get("choices") or [None])[0] or {}
        msg = choice0.get("message") or {}
        return str(msg.get("content") or "")
    except Exception:
        return ""


def _messages_has_image(messages: List[Dict[str, Any]]) -> bool:
    for m in messages:
        content = m.get("content")
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") in ("image_url", "image"):
                    return True
    return False


def _strip_code_fences(text: str) -> str:
    t = str(text).strip()
    if not t.startswith("```"):
        return t
    lines = t.splitlines()
    if len(lines) < 2:
        return t
    if lines[0].strip().startswith("```") and lines[-1].strip().startswith("```"):
        return "\n".join(lines[1:-1]).strip()
    return t


def _extract_json_candidate(text: str) -> str:
    t = _strip_code_fences(text)
    t2 = t.strip()
    if t2.startswith("{") and t2.endswith("}"):
        return t2
    if t2.startswith("[") and t2.endswith("]"):
        return t2
    start_obj = t2.find("{")
    end_obj = t2.rfind("}")
    if start_obj != -1 and end_obj != -1 and end_obj > start_obj:
        return t2[start_obj : end_obj + 1]
    start_arr = t2.find("[")
    end_arr = t2.rfind("]")
    if start_arr != -1 and end_arr != -1 and end_arr > start_arr:
        return t2[start_arr : end_arr + 1]
    return t2


def _parse_json_output(text: str) -> Tuple[Any | None, str | None]:
    candidate = _extract_json_candidate(text)
    try:
        return json.loads(candidate), None
    except Exception as e:
        return None, f"json_parse_failed: {e}"


def _repo_root() -> Path:
    root = (os.environ.get("GLM_ROUTER_REPO_ROOT") or "").strip()
    return Path(root).resolve(strict=False) if root else Path.cwd().resolve(strict=False)


def _split_csv(raw: str) -> List[str]:
    return [p.strip() for p in raw.split(",") if p.strip()]


def _is_within(path: Path, parent: Path) -> bool:
    return path == parent or parent in path.parents


def _resolve_repo_path(path_str: str, *, repo_root: Path, allow_outside_repo: bool) -> Path:
    if not isinstance(path_str, str) or not path_str.strip():
        raise ValueError("path must be a non-empty string")
    p0 = Path(path_str)
    p = p0 if p0.is_absolute() else (repo_root / p0)
    resolved = p.resolve(strict=False)
    if not allow_outside_repo and not _is_within(resolved, repo_root):
        raise ValueError(f"path must be under repo root: {repo_root}")
    return resolved


def _allowed_write_bases(repo_root: Path) -> List[Path]:
    raw = (os.environ.get("GLM_ROUTER_WRITE_BASE_DIRS") or "").strip()
    bases = _split_csv(raw) if raw else ["archives", "exports", "state"]
    out: List[Path] = []
    for b in bases:
        out.append((repo_root / b).resolve(strict=False))
    return out


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_text_file(path: Path, *, max_bytes: int) -> str:
    data = path.read_bytes()
    if len(data) > max_bytes:
        raise ValueError(f"input file too large: {path} bytes={len(data)} max_bytes={max_bytes}")
    return data.decode("utf-8", errors="replace")


def _write_bytes_atomic(path: Path, data: bytes, *, overwrite: bool) -> None:
    if path.exists() and not overwrite:
        raise ValueError(f"output_path exists and overwrite=false: {path}")
    os.makedirs(path.parent, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp.{os.getpid()}.{int(time.time() * 1000)}")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _preview_text(text: str, *, limit: int) -> str:
    t = str(text).strip()
    if limit <= 0:
        return ""
    if len(t) <= limit:
        return t
    return t[: max(0, limit - 3)] + "..."


def _validate_text_output(text: str, validate: Dict[str, Any] | None) -> Tuple[bool, Dict[str, Any], str | None]:
    t = str(text or "")
    info: Dict[str, Any] = {"ok": True}
    if not t.strip():
        info["ok"] = False
        return False, info, "empty_content"

    if validate is None:
        return True, info, None

    must_have = validate.get("must_have_substrings")
    if must_have is not None:
        if not isinstance(must_have, list):
            raise ValueError("validate.must_have_substrings must be array")
        missing: List[str] = []
        for s in must_have:
            if isinstance(s, str) and s and s not in t:
                missing.append(s)
        if missing:
            info["ok"] = False
            info["missing_substrings"] = missing
            return False, info, f"missing_substrings: {missing[:5]}"

    min_chars = validate.get("min_chars")
    if min_chars is not None:
        if not isinstance(min_chars, int) or min_chars < 0:
            raise ValueError("validate.min_chars must be integer >= 0")
        info["min_chars"] = min_chars
        if len(t) < min_chars:
            info["ok"] = False
            info["chars"] = len(t)
            return False, info, f"too_short: chars={len(t)} min_chars={min_chars}"

    max_chars = validate.get("max_chars")
    if max_chars is not None:
        if not isinstance(max_chars, int) or max_chars < 1:
            raise ValueError("validate.max_chars must be integer >= 1")
        info["max_chars"] = max_chars
        if len(t) > max_chars:
            info["ok"] = False
            info["chars"] = len(t)
            return False, info, f"too_long: chars={len(t)} max_chars={max_chars}"

    return True, info, None


def _build_write_file_messages(
    *,
    expect: str,
    system: str | None,
    instructions: str,
    template_text: str | None,
    inputs: List[Tuple[str, str]],
) -> List[Dict[str, Any]]:
    sys_parts: List[str] = []
    if system and system.strip():
        sys_parts.append(system.strip())
    sys_parts.append("你是一个严谨的文档生成器。只输出最终结果本身，不要前后解释。不要使用 Markdown 代码块包裹输出。")
    if expect == "json":
        sys_parts.append(
            "你必须只输出严格合法的 JSON（RFC 8259）：所有 key 必须双引号；字符串值必须双引号；布尔值只能 true/false；不要输出多余文本。"
        )
    system_final = "\n\n".join(sys_parts)

    blocks: List[str] = [instructions.strip()]

    if template_text:
        blocks.append("【模板】\n" + template_text.strip())

    if inputs:
        parts: List[str] = ["【输入材料】"]
        for rel_path, content in inputs:
            parts.append(f"--- path: {rel_path} ---\n{content.strip()}\n")
        blocks.append("\n".join(parts).strip())

    user_final = "\n\n".join([b for b in blocks if b.strip()])
    return [{"role": "system", "content": system_final}, {"role": "user", "content": user_final}]


def _append_call_log(record: Dict[str, Any]) -> None:
    path = (os.environ.get("GLM_ROUTER_CALL_LOG") or "").strip()
    if not path:
        return
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        logging.exception("failed to write GLM_ROUTER_CALL_LOG")


def _build_messages(args: Dict[str, Any]) -> List[Dict[str, Any]]:
    messages_raw = args.get("messages")
    if messages_raw is not None:
        messages = _as_list(messages_raw)
        out: List[Dict[str, Any]] = []
        for idx, m in enumerate(messages):
            if not isinstance(m, dict):
                raise ValueError(f"messages[{idx}] must be an object")
            out.append(m)
        if not out:
            raise ValueError("messages is empty")
        return out

    user = args.get("user")
    if not isinstance(user, str) or not user.strip():
        raise ValueError("user must be a non-empty string when messages is not provided")
    system = args.get("system")
    if system is not None and not isinstance(system, str):
        raise ValueError("system must be a string")
    image_url = args.get("image_url")
    if image_url is not None and not isinstance(image_url, str):
        raise ValueError("image_url must be a string")

    out2: List[Dict[str, Any]] = []
    if isinstance(system, str) and system.strip():
        out2.append({"role": "system", "content": system})
    if isinstance(image_url, str) and image_url.strip():
        out2.append(
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": image_url}},
                    {"type": "text", "text": user},
                ],
            }
        )
    else:
        out2.append({"role": "user", "content": user})
    return out2


def _route_models(*, family: str, has_image: bool) -> List[Tuple[str, str]]:
    # returns [(model, tier)]
    family_norm = family.strip().lower() if family else "auto"
    is_vision = family_norm == "vision" or (family_norm == "auto" and has_image)
    if is_vision:
        return [("glm-4.6v-flash", "free"), ("glm-4.6v", "paid")]
    return [("glm-4.5-flash", "free"), ("glm-4.6", "paid")]


def _allow_paid(args: Dict[str, Any]) -> bool:
    allow_paid_raw = args.get("allow_paid")
    if allow_paid_raw is None:
        return _env_bool("GLM_ROUTER_ALLOW_PAID_DEFAULT", False)
    if isinstance(allow_paid_raw, bool):
        return allow_paid_raw
    raise ValueError("allow_paid must be boolean")


def _expect(args: Dict[str, Any]) -> str:
    value = (args.get("expect") or "text")
    if not isinstance(value, str):
        raise ValueError("expect must be string")
    v = value.strip().lower()
    if v not in ("text", "json"):
        raise ValueError("expect must be text|json")
    return v


def _timeout_sec(args: Dict[str, Any]) -> float:
    raw = args.get("timeout_sec", None)
    if raw is None:
        return DEFAULT_TIMEOUT_SEC
    if isinstance(raw, (int, float)):
        return float(raw)
    raise ValueError("timeout_sec must be number")


def handle_glm_router_chat(request_id: RequestId, args: Dict[str, Any], *, api_base: str) -> Dict[str, Any]:
    api_key = _get_api_key()
    expect = _expect(args)
    allow_paid = _allow_paid(args)
    timeout_sec = _timeout_sec(args)
    family = args.get("family", "auto")
    if family is not None and not isinstance(family, str):
        raise ValueError("family must be string")
    family_s = str(family or "auto")

    messages = _build_messages(args)
    has_image = _messages_has_image(messages)

    attempts: List[Dict[str, Any]] = []
    used_text = ""
    used_json: Any | None = None
    used_model = ""
    used_tier = ""

    started = time.time()
    include_prompts = _env_bool("GLM_ROUTER_CALL_LOG_INCLUDE_PROMPTS", False)
    include_answers = _env_bool("GLM_ROUTER_CALL_LOG_INCLUDE_ANSWERS", False)

    for model, tier in _route_models(family=family_s, has_image=has_image):
        if tier == "paid" and not allow_paid:
            attempts.append(
                {
                    "model": model,
                    "tier": tier,
                    "skipped": True,
                    "reason": "allow_paid=false",
                }
            )
            continue

        status, data, elapsed_ms = _call_chat_completions(
            api_base=api_base, api_key=api_key, model=model, messages=messages, timeout_sec=timeout_sec
        )
        text = _extract_assistant_content(data)

        attempt: Dict[str, Any] = {
            "model": model,
            "tier": tier,
            "http_status": status,
            "elapsed_ms": round(elapsed_ms, 1),
        }

        if status != 200:
            attempt["ok"] = False
            attempt["error"] = "http_non_200"
            attempts.append(attempt)
            continue

        if expect == "json":
            parsed, err = _parse_json_output(text)
            if err or parsed is None:
                attempt["ok"] = False
                attempt["error"] = err or "json_parse_failed"
                attempt["preview"] = (text or "").strip()[:200]
                attempts.append(attempt)
                continue
            used_text = text
            used_json = parsed
            used_model = model
            used_tier = tier
            attempt["ok"] = True
            attempts.append(attempt)
            break

        if not str(text).strip():
            attempt["ok"] = False
            attempt["error"] = "empty_content"
            attempts.append(attempt)
            continue

        used_text = text
        used_model = model
        used_tier = tier
        attempt["ok"] = True
        attempts.append(attempt)
        break

    elapsed_total_ms = round((time.time() - started) * 1000.0, 1)

    log_record: Dict[str, Any] = {
        "ts": _now_iso(),
        "request_id": request_id,
        "expect": expect,
        "family": family_s,
        "allow_paid": allow_paid,
        "api_base": api_base,
        "used_model": used_model or None,
        "used_tier": used_tier or None,
        "attempts": attempts,
        "elapsed_ms": elapsed_total_ms,
    }
    if include_prompts:
        log_record["messages"] = messages
    if include_answers:
        log_record["answer_text"] = used_text
        if used_json is not None:
            log_record["answer_json"] = used_json
    _append_call_log(log_record)

    if not used_model:
        raise RuntimeError(f"All model attempts failed. attempts={attempts}")

    structured: Dict[str, Any] = {
        "text": used_text,
        "json": used_json,
        "used_model": used_model,
        "used_tier": used_tier,
        "attempts": attempts,
        "meta": args.get("meta") if isinstance(args.get("meta"), dict) else None,
    }
    return _call_result(text=used_text, structured=structured)


def _as_str_list(value: Any, *, field: str) -> List[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{field} must be array")
    out: List[str] = []
    for idx, v in enumerate(value):
        if not isinstance(v, str) or not v.strip():
            raise ValueError(f"{field}[{idx}] must be non-empty string")
        out.append(v)
    return out


def _max_retries(args: Dict[str, Any]) -> int:
    raw = args.get("max_retries", 0)
    if raw is None:
        return 0
    if not isinstance(raw, int) or raw < 0 or raw > MAX_RETRIES_MAX:
        raise ValueError(f"max_retries must be integer in [0,{MAX_RETRIES_MAX}]")
    return raw


def handle_glm_router_write_file(request_id: RequestId, args: Dict[str, Any], *, api_base: str) -> Dict[str, Any]:
    api_key = _get_api_key()
    expect = _expect(args)
    allow_paid = _allow_paid(args)
    timeout_sec = _timeout_sec(args)
    max_retries = _max_retries(args)

    family = args.get("family", "text")
    if family is not None and not isinstance(family, str):
        raise ValueError("family must be string")
    family_s = str(family or "text")

    system = args.get("system")
    if system is not None and not isinstance(system, str):
        raise ValueError("system must be string")

    instructions = args.get("instructions")
    if not isinstance(instructions, str) or not instructions.strip():
        raise ValueError("instructions must be a non-empty string")

    output_path_raw = args.get("output_path")
    if not isinstance(output_path_raw, str) or not output_path_raw.strip():
        raise ValueError("output_path must be a non-empty string")

    overwrite_raw = args.get("overwrite", False)
    if not isinstance(overwrite_raw, bool):
        raise ValueError("overwrite must be boolean")
    overwrite = overwrite_raw

    preview_chars = args.get("preview_chars", DEFAULT_PREVIEW_CHARS)
    if not isinstance(preview_chars, int) or preview_chars < 0 or preview_chars > MAX_PREVIEW_CHARS:
        raise ValueError(f"preview_chars must be integer in [0,{MAX_PREVIEW_CHARS}]")

    max_input_bytes = args.get("max_input_bytes_per_file", DEFAULT_MAX_INPUT_BYTES_PER_FILE)
    if not isinstance(max_input_bytes, int) or max_input_bytes < 1:
        raise ValueError("max_input_bytes_per_file must be integer >= 1")

    validate_raw = args.get("validate")
    validate: Dict[str, Any] | None
    if validate_raw is None:
        validate = None
    elif isinstance(validate_raw, dict):
        validate = validate_raw
    else:
        raise ValueError("validate must be object")

    repo_root = _repo_root()
    allow_outside_read = _env_bool("GLM_ROUTER_ALLOW_OUTSIDE_REPO_READ", False)
    allow_outside_write = _env_bool("GLM_ROUTER_ALLOW_OUTSIDE_REPO_WRITE", False)

    output_path = _resolve_repo_path(output_path_raw, repo_root=repo_root, allow_outside_repo=allow_outside_write)
    allowed_bases = _allowed_write_bases(repo_root)
    if not any(_is_within(output_path, b) for b in allowed_bases):
        raise ValueError(f"output_path must be under allowed dirs: {', '.join(str(b) for b in allowed_bases)}")

    input_paths = _as_str_list(args.get("input_paths"), field="input_paths")
    template_path_raw = args.get("template_path")
    if template_path_raw is not None and not isinstance(template_path_raw, str):
        raise ValueError("template_path must be string")

    template_text: str | None = None
    if isinstance(template_path_raw, str) and template_path_raw.strip():
        template_path = _resolve_repo_path(template_path_raw, repo_root=repo_root, allow_outside_repo=allow_outside_read)
        if not template_path.exists():
            raise ValueError(f"template_path not found: {template_path}")
        template_text = _read_text_file(template_path, max_bytes=max_input_bytes)

    inputs: List[Tuple[str, str]] = []
    for p_raw in input_paths:
        p = _resolve_repo_path(p_raw, repo_root=repo_root, allow_outside_repo=allow_outside_read)
        if not p.exists():
            raise ValueError(f"input_paths not found: {p}")
        rel = str(p.relative_to(repo_root)) if _is_within(p, repo_root) else str(p)
        inputs.append((rel, _read_text_file(p, max_bytes=max_input_bytes)))

    messages0 = _build_write_file_messages(
        expect=expect,
        system=system,
        instructions=instructions,
        template_text=template_text,
        inputs=inputs,
    )
    has_image = _messages_has_image(messages0)

    attempts: List[Dict[str, Any]] = []
    used_model = ""
    used_tier = ""
    final_text = ""
    final_validation: Dict[str, Any] = {"ok": False}

    started = time.time()

    def _is_retryable_http_status(status: int) -> bool:
        return status in (0, 429, 500, 502, 503, 504)

    def _backoff_seconds(retry_idx: int) -> float:
        base = float((os.environ.get("GLM_ROUTER_HTTP_BACKOFF_BASE_SECONDS") or "5").strip() or "5")
        cap = float((os.environ.get("GLM_ROUTER_HTTP_BACKOFF_MAX_SECONDS") or "60").strip() or "60")
        jitter = 0.25 + (random.random() * 0.75)
        return min(cap, base * (2**retry_idx) * jitter)

    for model, tier in _route_models(family=family_s, has_image=has_image):
        if tier == "paid" and not allow_paid:
            attempts.append({"model": model, "tier": tier, "skipped": True, "reason": "allow_paid=false"})
            continue

        messages = list(messages0)
        for retry_idx in range(max_retries + 1):
            status, data, elapsed_ms = _call_chat_completions(
                api_base=api_base, api_key=api_key, model=model, messages=messages, timeout_sec=timeout_sec
            )
            text = _extract_assistant_content(data)

            attempt: Dict[str, Any] = {
                "model": model,
                "tier": tier,
                "retry_idx": retry_idx,
                "http_status": status,
                "elapsed_ms": round(elapsed_ms, 1),
            }

            if status != 200:
                status_i = int(status or 0)
                if retry_idx < max_retries and _is_retryable_http_status(status_i):
                    sleep_sec = round(_backoff_seconds(retry_idx), 2)
                    attempt["ok"] = False
                    attempt["error"] = "http_non_200_retryable"
                    attempt["sleep_sec"] = sleep_sec
                    attempts.append(attempt)
                    time.sleep(float(sleep_sec))
                    continue
                attempt["ok"] = False
                attempt["error"] = "http_non_200"
                attempts.append(attempt)
                break

            if expect == "json":
                _parsed, err = _parse_json_output(text)
                if err:
                    attempt["ok"] = False
                    attempt["error"] = err
                    attempt["preview"] = _preview_text(text, limit=200)
                    attempts.append(attempt)
                    if retry_idx < max_retries:
                        messages = list(messages0) + [
                            {
                                "role": "user",
                                "content": f"你的上一次输出未通过 JSON 校验（{attempt['error']}）。请重新输出严格合法 JSON（只输出 JSON）。",
                            }
                        ]
                        continue
                    break

            ok_text, vinfo, verr = _validate_text_output(text, validate)
            if not ok_text:
                attempt["ok"] = False
                attempt["error"] = verr or "validation_failed"
                attempt["validation"] = vinfo
                attempt["preview"] = _preview_text(text, limit=200)
                attempts.append(attempt)
                if retry_idx < max_retries:
                    messages = list(messages0) + [
                        {
                            "role": "user",
                            "content": f"你的上一次输出未通过校验（{attempt['error']}）。请重新输出完整结果，并确保满足校验要求。",
                        }
                    ]
                    continue
                break

            used_model = model
            used_tier = tier
            final_text = text
            final_validation = vinfo
            attempt["ok"] = True
            attempt["validation"] = vinfo
            attempts.append(attempt)
            break

        if used_model:
            break

    elapsed_total_ms = round((time.time() - started) * 1000.0, 1)

    if not used_model:
        _append_call_log(
            {
                "ts": _now_iso(),
                "request_id": request_id,
                "tool": "glm_router_write_file",
                "expect": expect,
                "family": family_s,
                "allow_paid": allow_paid,
                "api_base": api_base,
                "output_path": str(output_path),
                "attempts": attempts,
                "elapsed_ms": elapsed_total_ms,
                "validation": final_validation,
            }
        )
        raise RuntimeError(f"All model attempts failed. attempts={attempts}")

    if expect == "json":
        parsed, err = _parse_json_output(final_text)
        if err or parsed is None:
            raise RuntimeError(f"unexpected: json parse failed after success: {err}")
        out_bytes = (json.dumps(parsed, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    else:
        out_bytes = (str(final_text).strip() + "\n").encode("utf-8")

    _write_bytes_atomic(output_path, out_bytes, overwrite=overwrite)
    sha256 = _sha256_hex(out_bytes)
    preview = _preview_text(final_text, limit=preview_chars)

    log_record: Dict[str, Any] = {
        "ts": _now_iso(),
        "request_id": request_id,
        "tool": "glm_router_write_file",
        "expect": expect,
        "family": family_s,
        "allow_paid": allow_paid,
        "api_base": api_base,
        "output_path": str(output_path),
        "bytes": len(out_bytes),
        "sha256": sha256,
        "used_model": used_model,
        "used_tier": used_tier,
        "attempts": attempts,
        "elapsed_ms": elapsed_total_ms,
        "validation": final_validation,
    }

    include_prompts = _env_bool("GLM_ROUTER_CALL_LOG_INCLUDE_PROMPTS", False)
    include_answers = _env_bool("GLM_ROUTER_CALL_LOG_INCLUDE_ANSWERS", False)
    if include_prompts:
        log_record["input_paths"] = input_paths
        log_record["template_path"] = template_path_raw
        log_record["system"] = system
        log_record["instructions"] = instructions
    if include_answers:
        log_record["preview"] = preview
    _append_call_log(log_record)

    structured: Dict[str, Any] = {
        "output_path": str(output_path),
        "bytes": len(out_bytes),
        "sha256": sha256,
        "chars": len(str(final_text or "")),
        "used_model": used_model,
        "used_tier": used_tier,
        "attempts": attempts,
        "validation": final_validation,
        "preview": preview,
        "meta": args.get("meta") if isinstance(args.get("meta"), dict) else None,
    }
    return _call_result(text=f"Wrote file: {output_path}", structured=structured)


def handle_tools_call(request_id: RequestId, params: Dict[str, Any], *, api_base: str) -> None:
    try:
        tool_name, args = _parse_call_params(params)
    except ValueError as e:
        _send_error(request_id, -32602, str(e))
        return

    try:
        if tool_name == "glm_router_chat":
            _send_result(request_id, handle_glm_router_chat(request_id, args, api_base=api_base))
            return
        if tool_name == "glm_router_write_file":
            _send_result(request_id, handle_glm_router_write_file(request_id, args, api_base=api_base))
            return

        _send_result(
            request_id,
            _call_result(text=f"Unknown tool: {tool_name}", structured={"error": "unknown_tool"}, is_error=True),
        )
    except ValueError as e:
        _send_error(request_id, -32602, str(e))
    except Exception as e:  # pragma: no cover
        logging.exception("Unhandled error in tool call")
        _send_error(request_id, -32603, "internal error", {"detail": str(e)})


def main(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(prog="glm-router-mcp")
    parser.add_argument(
        "--api-base",
        default=None,
        help=f"API base URL (default: $BIGMODEL_API_BASE or {DEFAULT_API_BASE})",
    )
    parser.add_argument(
        "--log-level",
        default=os.environ.get("GLM_ROUTER_LOG_LEVEL", "INFO"),
        help="Log level (stderr) (default: INFO or $GLM_ROUTER_LOG_LEVEL)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
        stream=sys.stderr,
    )

    api_base = _get_api_base(args.api_base)

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
            handle_tools_call(request_id, _as_object(params), api_base=api_base)
            continue

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
