#!/usr/bin/env python3
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple, Union

MCP_PROTOCOL_VERSION = "2025-06-18"
JSONRPC_VERSION = "2.0"

RequestId = Union[str, int]

_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,48}$")
_ENV_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_SCRIPT_RE = re.compile(r"^[A-Za-z0-9_./-]{1,160}$")

try:
    import fcntl  # type: ignore[attr-defined]
except Exception:  # pragma: no cover - platform dependent
    fcntl = None


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


def _tool(*, name: str, title: str, description: str, input_schema: Dict[str, Any], output_schema: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "name": name,
        "title": title,
        "description": description,
        "inputSchema": input_schema,
        "outputSchema": output_schema,
    }


def _repo_root() -> Path:
    raw = (os.environ.get("TMUX_ORCH_REPO_ROOT") or "").strip()
    return Path(raw).resolve(strict=False) if raw else Path.cwd().resolve(strict=False)


def _split_csv(raw: str) -> List[str]:
    return [p.strip() for p in raw.split(",") if p.strip()]


def _is_within(path: Path, parent: Path) -> bool:
    return path == parent or parent in path.parents


def _allowed_write_bases(repo_root: Path) -> List[Path]:
    raw = (os.environ.get("TMUX_ORCH_WRITE_BASE_DIRS") or "").strip()
    bases = _split_csv(raw) if raw else ["archives", "state", "exports"]
    out: List[Path] = []
    for b in bases:
        out.append((repo_root / b).resolve(strict=False))
    return out


def _allowed_scripts(repo_root: Path) -> List[str]:
    raw = (os.environ.get("TMUX_ORCH_ALLOWED_SCRIPTS") or "").strip()
    if raw:
        return _split_csv(raw)
    scripts_dir = (repo_root / "scripts").resolve(strict=False)
    allowed: List[str] = []
    if scripts_dir.is_dir():
        for p in scripts_dir.iterdir():
            if not p.is_file():
                continue
            if p.name.startswith("worker_") and p.name.endswith(".sh"):
                allowed.append(f"scripts/{p.name}")
    if not allowed:
        allowed = ["scripts/worker_topic_init_glm.sh"]
    allowed.sort()
    return allowed


def _session_prefix(default: str = "codexw") -> str:
    raw = (os.environ.get("TMUX_ORCH_SESSION_PREFIX") or "").strip()
    return raw or default


def _validate_name(label: str, value: str) -> str:
    v = value.strip()
    if not v:
        raise ValueError(f"{label} is empty")
    if not _NAME_RE.match(v):
        raise ValueError(f"{label} invalid: {value!r} (allowed: {_NAME_RE.pattern})")
    return v


def _tmux(args: List[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["tmux", *args],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _tmux_check(args: List[str]) -> subprocess.CompletedProcess[str]:
    cp = _tmux(args)
    if cp.returncode != 0:
        raise RuntimeError(f"tmux failed: tmux {' '.join(args)} :: {cp.stderr.strip()}")
    return cp


def _worker_session(prefix: str, worker_id: int) -> str:
    if worker_id < 0 or worker_id > 128:
        raise ValueError("worker_id out of range")
    return _validate_name("session", f"{prefix}-{worker_id}")


def _ensure_session(session: str, *, repo_root: Path) -> None:
    cp = _tmux(["has-session", "-t", session])
    if cp.returncode == 0:
        return
    _tmux_check(["new-session", "-d", "-s", session, "-c", str(repo_root), "-n", "main"])


def _pane_target(session: str) -> str:
    return f"{session}:0.0"


def _pane_id(target: str) -> str:
    cp = _tmux_check(["display-message", "-p", "-t", target, "#{pane_id}"])
    return cp.stdout.strip()


def _capture_tail(target: str, *, lines: int) -> str:
    n = max(1, min(int(lines), 500))
    cp = _tmux_check(["capture-pane", "-p", "-t", target, "-S", f"-{n}"])
    return cp.stdout


def _status_path(repo_root: Path, worker_id: int) -> Path:
    return repo_root / "state" / "tmux_orch" / "workers" / str(worker_id) / "status.json"


def _worker_lock_path(repo_root: Path, worker_id: int) -> Path:
    return repo_root / "state" / "tmux_orch" / "workers" / str(worker_id) / "dispatch.lock"


def _atomic_write_text(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding=encoding,
            delete=False,
            dir=str(path.parent),
            prefix=path.name + ".tmp.",
        ) as f:
            tmp_path = Path(f.name)
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(str(tmp_path), str(path))
    finally:
        if tmp_path is not None:
            try:
                tmp_path.unlink()
            except FileNotFoundError:
                pass


def _write_status(repo_root: Path, worker_id: int, status: Dict[str, Any]) -> None:
    path = _status_path(repo_root, worker_id)
    _atomic_write_text(path, json.dumps(status, ensure_ascii=False) + "\n")


@contextmanager
def _worker_dispatch_lock(repo_root: Path, worker_id: int) -> Any:
    lock_file = _worker_lock_path(repo_root, worker_id)
    if fcntl is None:
        yield None
        return
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    f = lock_file.open("a+", encoding="utf-8")
    try:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        yield f
    finally:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        f.close()


def _read_status(repo_root: Path, worker_id: int) -> Dict[str, Any]:
    path = _status_path(repo_root, worker_id)
    if not path.exists():
        return {"worker_id": worker_id, "status": "unknown", "ts": None}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"worker_id": worker_id, "status": "unknown", "ts": None, "error": f"bad_status_file: {path}"}


def _is_busy(status: Dict[str, Any]) -> bool:
    return str(status.get("status") or "").strip().lower() in ("running", "busy")


def _resolve_safe_path(label: str, repo_root: Path, *, allowed_bases: List[Path], path_raw: str) -> str:
    p0 = Path(path_raw)
    p = p0 if p0.is_absolute() else (repo_root / p0)
    resolved = p.resolve(strict=False)
    if not _is_within(resolved, repo_root):
        raise ValueError(f"{label} must be under repo root: {repo_root}")
    if not any(_is_within(resolved, b) for b in allowed_bases):
        raise ValueError(f"{label} must be under allowed dirs: {', '.join(str(b) for b in allowed_bases)}")
    return str(resolved)


def _validate_script_rel(repo_root: Path, script_raw: str) -> str:
    script = script_raw.strip()
    if not script:
        raise ValueError("script must be non-empty string")
    if script.startswith("/"):
        raise ValueError("script must be a repo-relative path (no leading '/')")
    if not _SCRIPT_RE.match(script):
        raise ValueError(f"script has invalid characters: {script_raw!r}")
    if ".." in Path(script).parts:
        raise ValueError("script must not contain '..'")
    if not script.startswith("scripts/"):
        script = f"scripts/{script}"
    allowed = _allowed_scripts(repo_root)
    if script not in allowed:
        raise ValueError(f"script not allowed: {script} (allowed: {', '.join(allowed)})")
    abs_path = (repo_root / script).resolve(strict=False)
    if not _is_within(abs_path, repo_root):
        raise ValueError("script must be under repo root")
    if not abs_path.exists():
        raise RuntimeError(f"missing script: {abs_path}")
    return script


def _env_kv_from_object(env_obj: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    for k in sorted(env_obj.keys()):
        if not isinstance(k, str) or not _ENV_KEY_RE.match(k):
            raise ValueError(f"env key invalid: {k!r} (must match: {_ENV_KEY_RE.pattern})")
        v = env_obj.get(k)
        if v is None:
            continue
        if isinstance(v, (dict, list)):
            raise ValueError(f"env[{k}] must be a scalar (string/number/bool)")
        s = str(v)
        if "\n" in s or "\r" in s:
            raise ValueError(f"env[{k}] must not contain newlines")
        if len(s) > 4000:
            raise ValueError(f"env[{k}] too long")
        out.append(f"{k}={s}")
    return out


def _tools_list() -> List[Dict[str, Any]]:
    ensure_workers_schema = {
        "type": "object",
        "properties": {
            "n": {"type": "integer", "minimum": 1, "maximum": 16},
            "session_prefix": {"type": "string", "description": "Optional tmux session prefix."},
        },
        "required": ["n"],
    }

    dispatch_schema = {
        "type": "object",
        "properties": {
            "worker_id": {"type": "integer", "minimum": 0, "maximum": 128},
            "topic_id": {"type": "string"},
            "topic_title": {"type": "string"},
            "scope_hint": {"type": "string", "description": "Optional topic scope/boundary hint for better initialization."},
            "tag": {"type": "string"},
            "allow_paid": {"type": "boolean"},
            "record_path": {"type": "string"},
            "session_prefix": {"type": "string", "description": "Optional tmux session prefix."},
        },
        "required": ["worker_id", "topic_id", "topic_title"],
    }

    dispatch_script_schema = {
        "type": "object",
        "properties": {
            "worker_id": {"type": "integer", "minimum": 0, "maximum": 128},
            "script": {"type": "string", "description": "Repo-relative script under scripts/ (must be allowed)."},
            "env": {"type": "object", "description": "Env map passed to tmux respawn-pane (-e)."},
            "record_path": {"type": "string", "description": "Optional record path injected as ORCH_RECORD_PATH (validated)."},
            "require_idle": {"type": "boolean", "description": "Default true; reject if worker is running."},
            "force_kill": {"type": "boolean", "description": "Default false; allow overriding busy protection (kills current job)."},
            "session_prefix": {"type": "string", "description": "Optional tmux session prefix."},
        },
        "required": ["worker_id", "script"],
    }

    tail_schema = {
        "type": "object",
        "properties": {
            "worker_id": {"type": "integer", "minimum": 0, "maximum": 128},
            "lines": {"type": "integer", "minimum": 1, "maximum": 500},
            "session_prefix": {"type": "string"},
        },
        "required": ["worker_id"],
    }

    status_schema = {
        "type": "object",
        "properties": {
            "worker_id": {"type": "integer", "minimum": 0, "maximum": 128},
        },
        "required": ["worker_id"],
    }

    return [
        _tool(
            name="ensure_workers",
            title="Ensure tmux workers",
            description="Ensure N tmux worker sessions exist and return their pane targets.",
            input_schema=ensure_workers_schema,
            output_schema={"type": "object", "properties": {"workers": {"type": "array"}}, "required": ["workers"]},
        ),
        _tool(
            name="dispatch_script",
            title="Dispatch a whitelisted script",
            description="Run a whitelisted repo script on a worker with busy protection (require_idle by default).",
            input_schema=dispatch_script_schema,
            output_schema={"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]},
        ),
        _tool(
            name="dispatch_topic_init_glm",
            title="Dispatch topic init (GLM write-file)",
            description="Run topic initialization job (GLM writes overview/framework/open_questions to files) on a worker.",
            input_schema=dispatch_schema,
            output_schema={"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]},
        ),
        _tool(
            name="tail_worker",
            title="Tail worker output",
            description="Capture last N lines of worker pane output.",
            input_schema=tail_schema,
            output_schema={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
        ),
        _tool(
            name="get_worker_status",
            title="Get worker status",
            description="Read worker status file written by job scripts.",
            input_schema=status_schema,
            output_schema={"type": "object"},
        ),
    ]


def handle_initialize(request_id: RequestId, params: Dict[str, Any]) -> None:
    client_protocol = params.get("protocolVersion")
    protocol_version = MCP_PROTOCOL_VERSION if client_protocol in (None, MCP_PROTOCOL_VERSION) else client_protocol
    _send_result(
        request_id,
        {
            "protocolVersion": protocol_version,
            "serverInfo": {"name": "tmux-orchestrator-mcp", "version": "0.2.0"},
            "capabilities": {"tools": {"listChanged": False}},
            "instructions": "Provides tmux worker orchestration tools: ensure_workers, dispatch_script, dispatch_topic_init_glm, tail_worker, get_worker_status.",
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


def _call_result(*, text: str, structured: Any) -> Dict[str, Any]:
    return {"content": [_content_text(text)], "structuredContent": structured}


def _handle_ensure_workers(request_id: RequestId, args: Dict[str, Any]) -> Dict[str, Any]:
    repo_root = _repo_root()
    n_raw = args.get("n")
    if not isinstance(n_raw, int) or n_raw < 1 or n_raw > 16:
        raise ValueError("n must be integer in [1,16]")
    prefix = args.get("session_prefix") or _session_prefix()
    if not isinstance(prefix, str):
        raise ValueError("session_prefix must be string")
    prefix = _validate_name("session_prefix", prefix)

    workers: List[Dict[str, Any]] = []
    for worker_id in range(n_raw):
        session = _worker_session(prefix, worker_id)
        _ensure_session(session, repo_root=repo_root)
        target = _pane_target(session)
        workers.append(
            {
                "worker_id": worker_id,
                "session": session,
                "pane_target": target,
                "pane_id": _pane_id(target),
            }
        )
    return _call_result(text=f"Ensured {len(workers)} worker(s)", structured={"workers": workers})


def _handle_dispatch_topic_init_glm(request_id: RequestId, args: Dict[str, Any]) -> Dict[str, Any]:
    repo_root = _repo_root()
    allowed_bases = _allowed_write_bases(repo_root)

    worker_id = args.get("worker_id")
    if not isinstance(worker_id, int):
        raise ValueError("worker_id must be integer")

    prefix = args.get("session_prefix") or _session_prefix()
    if not isinstance(prefix, str):
        raise ValueError("session_prefix must be string")
    prefix = _validate_name("session_prefix", prefix)

    topic_id = args.get("topic_id")
    topic_title = args.get("topic_title")
    if not isinstance(topic_id, str) or not topic_id.strip():
        raise ValueError("topic_id must be non-empty string")
    if not isinstance(topic_title, str) or not topic_title.strip():
        raise ValueError("topic_title must be non-empty string")

    scope_hint = args.get("scope_hint")
    if scope_hint is not None and (not isinstance(scope_hint, str) or not scope_hint.strip()):
        raise ValueError("scope_hint must be string")

    tag = args.get("tag") or "init"
    if not isinstance(tag, str) or not tag.strip():
        raise ValueError("tag must be string")

    allow_paid = args.get("allow_paid", False)
    if not isinstance(allow_paid, bool):
        raise ValueError("allow_paid must be boolean")

    record_path_raw = args.get("record_path")
    if record_path_raw is not None and (not isinstance(record_path_raw, str) or not record_path_raw.strip()):
        raise ValueError("record_path must be string")

    env_map: Dict[str, Any] = {
        "ORCH_TOPIC_ID": topic_id.strip(),
        "ORCH_TOPIC_TITLE": topic_title.strip(),
        "ORCH_TAG": tag.strip(),
        "ORCH_ALLOW_PAID": "1" if allow_paid else "0",
    }
    if isinstance(scope_hint, str) and scope_hint.strip():
        env_map["ORCH_SCOPE_HINT"] = scope_hint.strip()
    if isinstance(record_path_raw, str) and record_path_raw.strip():
        env_map["ORCH_RECORD_PATH"] = _resolve_safe_path("record_path", repo_root, allowed_bases=allowed_bases, path_raw=record_path_raw)

    dispatch_args: Dict[str, Any] = {
        "worker_id": worker_id,
        "script": "scripts/worker_topic_init_glm.sh",
        "env": env_map,
        "record_path": record_path_raw or None,
        "require_idle": True,
        "force_kill": False,
        "session_prefix": prefix,
    }
    out = _handle_dispatch_script(request_id, dispatch_args)
    structured = out.get("structuredContent") if isinstance(out, dict) else None
    if isinstance(structured, dict):
        structured["record_path"] = record_path_raw or None
    return _call_result(text=f"Dispatched topic init to worker {worker_id}", structured=structured or {})


def _handle_dispatch_script(request_id: RequestId, args: Dict[str, Any]) -> Dict[str, Any]:
    repo_root = _repo_root()
    allowed_bases = _allowed_write_bases(repo_root)

    worker_id = args.get("worker_id")
    if not isinstance(worker_id, int):
        raise ValueError("worker_id must be integer")

    prefix = args.get("session_prefix") or _session_prefix()
    if not isinstance(prefix, str):
        raise ValueError("session_prefix must be string")
    prefix = _validate_name("session_prefix", prefix)

    script_raw = args.get("script")
    if not isinstance(script_raw, str):
        raise ValueError("script must be string")
    script_rel = _validate_script_rel(repo_root, script_raw)

    env_raw = args.get("env") or {}
    if not isinstance(env_raw, dict):
        raise ValueError("env must be object")

    record_path_raw = args.get("record_path")
    if record_path_raw is not None and (not isinstance(record_path_raw, str) or not record_path_raw.strip()):
        raise ValueError("record_path must be string")

    require_idle = args.get("require_idle", True)
    if not isinstance(require_idle, bool):
        raise ValueError("require_idle must be boolean")

    force_kill = args.get("force_kill", False)
    if not isinstance(force_kill, bool):
        raise ValueError("force_kill must be boolean")

    record_path_abs: str | None = None
    if isinstance(record_path_raw, str) and record_path_raw.strip():
        record_path_abs = _resolve_safe_path("record_path", repo_root, allowed_bases=allowed_bases, path_raw=record_path_raw.strip())

    env_map = dict(env_raw)
    env_map["ORCH_WORKER_ID"] = str(worker_id)
    env_record_path_raw = env_map.get("ORCH_RECORD_PATH")
    if env_record_path_raw is not None:
        if not isinstance(env_record_path_raw, str):
            raise ValueError("env.ORCH_RECORD_PATH must be string")
        if env_record_path_raw.strip():
            env_map["ORCH_RECORD_PATH"] = _resolve_safe_path(
                "ORCH_RECORD_PATH", repo_root, allowed_bases=allowed_bases, path_raw=env_record_path_raw.strip()
            )
        else:
            env_map.pop("ORCH_RECORD_PATH", None)
    if record_path_abs:
        env_map.setdefault("ORCH_RECORD_PATH", record_path_abs)
    env_kv = _env_kv_from_object(env_map)
    record_path_used = env_map.get("ORCH_RECORD_PATH")

    session = _worker_session(prefix, worker_id)
    _ensure_session(session, repo_root=repo_root)
    target = _pane_target(session)

    with _worker_dispatch_lock(repo_root, worker_id):
        status = _read_status(repo_root, worker_id)
        if require_idle and _is_busy(status) and not force_kill:
            running_topic = status.get("topic_id") or ""
            raise ValueError(
                f"worker {worker_id} is busy (status=running topic={running_topic!r}); pick another worker or set force_kill=true"
            )

        dispatch_id = os.urandom(4).hex()
        pre_status: Dict[str, Any] = {
            "worker_id": worker_id,
            "status": "running",
            "ts": _now_iso(),
            "dispatch_id": dispatch_id,
            "script": script_rel,
            "record_path": record_path_used or None,
            "topic_id": (str(env_map.get("ORCH_TOPIC_ID") or "").strip() or None),
            "topic_title": (str(env_map.get("ORCH_TOPIC_TITLE") or "").strip() or None),
            "tag": (str(env_map.get("ORCH_TAG") or "").strip() or None),
        }
        _write_status(repo_root, worker_id, pre_status)

        try:
            cmd_args = ["bash", "-lc", f"bash {script_rel}; exec bash"]
            _tmux_check(
                [
                    "respawn-pane",
                    "-k",
                    "-t",
                    target,
                    "-c",
                    str(repo_root),
                    *sum([["-e", kv] for kv in env_kv], []),
                    *cmd_args,
                ]
            )
        except Exception as exc:
            fail_status = dict(pre_status)
            fail_status["status"] = "failed"
            fail_status["error"] = f"dispatch_failed: {exc}"
            fail_status["ts"] = _now_iso()
            _write_status(repo_root, worker_id, fail_status)
            raise

    out: Dict[str, Any] = {
        "ok": True,
        "worker_id": worker_id,
        "session": session,
        "pane_target": target,
        "pane_id": _pane_id(target),
        "script": script_rel,
        "require_idle": require_idle,
        "force_kill": force_kill,
        "record_path": record_path_raw or None,
        "record_path_used": record_path_used or None,
        "ts": _now_iso(),
    }
    return _call_result(text=f"Dispatched script to worker {worker_id}", structured=out)


def _handle_tail_worker(request_id: RequestId, args: Dict[str, Any]) -> Dict[str, Any]:
    worker_id = args.get("worker_id")
    if not isinstance(worker_id, int):
        raise ValueError("worker_id must be integer")

    prefix = args.get("session_prefix") or _session_prefix()
    if not isinstance(prefix, str):
        raise ValueError("session_prefix must be string")
    prefix = _validate_name("session_prefix", prefix)

    lines = args.get("lines", 80)
    if not isinstance(lines, int) or lines < 1 or lines > 500:
        raise ValueError("lines must be integer in [1,500]")

    session = _worker_session(prefix, worker_id)
    target = _pane_target(session)
    text = _capture_tail(target, lines=lines)
    return _call_result(text=f"Tail worker {worker_id}", structured={"text": text})


def _handle_get_worker_status(request_id: RequestId, args: Dict[str, Any]) -> Dict[str, Any]:
    repo_root = _repo_root()
    worker_id = args.get("worker_id")
    if not isinstance(worker_id, int):
        raise ValueError("worker_id must be integer")
    status = _read_status(repo_root, worker_id)
    return _call_result(text=f"Status worker {worker_id}: {status.get('status')}", structured=status)


def handle_tools_call(request_id: RequestId, params: Dict[str, Any]) -> None:
    try:
        tool_name, args = _parse_call_params(params)
    except ValueError as e:
        _send_error(request_id, -32602, str(e))
        return

    try:
        if tool_name == "ensure_workers":
            _send_result(request_id, _handle_ensure_workers(request_id, args))
            return
        if tool_name == "dispatch_script":
            _send_result(request_id, _handle_dispatch_script(request_id, args))
            return
        if tool_name == "dispatch_topic_init_glm":
            _send_result(request_id, _handle_dispatch_topic_init_glm(request_id, args))
            return
        if tool_name == "tail_worker":
            _send_result(request_id, _handle_tail_worker(request_id, args))
            return
        if tool_name == "get_worker_status":
            _send_result(request_id, _handle_get_worker_status(request_id, args))
            return

        _send_result(
            request_id,
            {
                "content": [_content_text(f"Unknown tool: {tool_name}")],
                "isError": True,
            },
        )
    except ValueError as e:
        _send_error(request_id, -32602, str(e))
    except Exception as e:
        _send_error(request_id, -32603, "internal error", {"detail": str(e)})


def main(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(prog="tmux-orchestrator-mcp")
    parser.add_argument("--log-level", default=os.environ.get("TMUX_ORCH_LOG_LEVEL", "INFO"))
    args = parser.parse_args(argv)

    # keep stderr logs minimal; MCP responses go to stdout
    _ = args

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
        if request_id is None:
            continue

        if method == "initialize":
            try:
                handle_initialize(request_id, _as_object(params))
            except Exception as e:
                _send_error(request_id, -32603, "initialize failed", {"detail": str(e)})
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
