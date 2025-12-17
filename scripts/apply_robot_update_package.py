#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import queue
import shlex
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


JSONRPC_VERSION = "2.0"
MCP_PROTOCOL_VERSION = "2025-06-18"

PLACEHOLDER_USER_IDS = {"U1_USER_ID", "CHILD_USER_ID"}


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _as_dict(value: Any, *, where: str) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    raise ValueError(f"{where}: expected object")


def _as_list(value: Any, *, where: str) -> List[Any]:
    if isinstance(value, list):
        return value
    raise ValueError(f"{where}: expected array")


def _as_str(value: Any, *, where: str) -> str:
    if isinstance(value, str):
        return value
    raise ValueError(f"{where}: expected string")


def _is_blank(s: str) -> bool:
    return not s.strip()


def _resolve_user_id(raw: Any, *, u1_user_id: str, child_user_id: str) -> str:
    if not isinstance(raw, str) or _is_blank(raw):
        raise ValueError("action.user_id must be a non-empty string")
    if raw == "U1_USER_ID":
        return u1_user_id
    if raw == "CHILD_USER_ID":
        return child_user_id
    return raw


def _validate_review_status(package: Dict[str, Any], *, force: bool) -> None:
    review = _as_dict(package.get("review", {}), where="review")
    required = bool(review.get("required", True))
    status = review.get("status", "pending")
    if required and status != "approved" and not force:
        raise ValueError(f"review.status must be 'approved' to apply (got: {status!r}). Use --force to bypass.")


def _resolve_target_user_ids(
    package: Dict[str, Any],
    *,
    u1_user_id_override: Optional[str],
    child_user_id_override: Optional[str],
) -> Tuple[str, str]:
    target = _as_dict(package.get("target", {}), where="target")
    u1_user_id = u1_user_id_override or str(target.get("u1_user_id", "")).strip() or os.getenv("U1_USER_ID", "")
    child_user_id = (
        child_user_id_override
        or str(target.get("child_user_id", "")).strip()
        or os.getenv("CHILD_USER_ID", "")
    )

    if not u1_user_id or u1_user_id in PLACEHOLDER_USER_IDS:
        raise ValueError("target.u1_user_id is missing/placeholder; pass --u1-user-id or set U1_USER_ID.")
    if not child_user_id or child_user_id in PLACEHOLDER_USER_IDS:
        raise ValueError("target.child_user_id is missing/placeholder; pass --child-user-id or set CHILD_USER_ID.")
    return u1_user_id, child_user_id


def _verify_inputs(package: Dict[str, Any]) -> None:
    inputs = _as_list(package.get("inputs", []), where="inputs")
    for i, item in enumerate(inputs):
        obj = _as_dict(item, where=f"inputs[{i}]")
        path_raw = obj.get("path")
        sha_raw = obj.get("sha256")
        if path_raw is None:
            continue
        path = Path(_as_str(path_raw, where=f"inputs[{i}].path"))
        if not path.exists():
            raise ValueError(f"inputs[{i}].path not found: {path}")
        if sha_raw is None:
            continue
        expected = _as_str(sha_raw, where=f"inputs[{i}].sha256").lower()
        actual = _sha256_file(path).lower()
        if actual != expected:
            raise ValueError(f"inputs[{i}] sha256 mismatch for {path}: expected {expected}, got {actual}")


def _collect_mem0_calls(
    package: Dict[str, Any],
    *,
    u1_user_id: str,
    child_user_id: str,
) -> List[Dict[str, Any]]:
    proposed_actions = _as_list(package.get("proposed_actions", []), where="proposed_actions")
    calls: List[Dict[str, Any]] = []
    for i, action in enumerate(proposed_actions):
        a = _as_dict(action, where=f"proposed_actions[{i}]")
        a_type = a.get("type")
        if a_type != "mem0.add_memory":
            continue

        user_id = _resolve_user_id(a.get("user_id"), u1_user_id=u1_user_id, child_user_id=child_user_id)
        memory = _as_dict(a.get("memory", {}), where=f"proposed_actions[{i}].memory")

        call_args: Dict[str, Any] = {
            "user_id": user_id,
            "kind": memory.get("kind"),
            "topic": memory.get("topic"),
            "content": memory.get("content"),
            "source": memory.get("source"),
            "related_entities": memory.get("related_entities", []),
            "tags": memory.get("tags", []),
        }

        # Basic validation (keep it permissive; spec may evolve).
        if not isinstance(call_args["kind"], str) or _is_blank(call_args["kind"]):
            raise ValueError(f"proposed_actions[{i}].memory.kind must be a non-empty string")
        if not isinstance(call_args["content"], str) or _is_blank(call_args["content"]):
            raise ValueError(f"proposed_actions[{i}].memory.content must be a non-empty string")

        calls.append(
            {
                "tool": "mem0-memory.add_memory",
                "arguments": call_args,
                "meta": {
                    "source_package_action_index": i,
                    "privacy": a.get("privacy", None),
                    "confidence": a.get("confidence", None),
                    "rationale": a.get("rationale", None),
                },
            }
        )
    return calls


def _collect_prompt_patches(package: Dict[str, Any]) -> List[Dict[str, Any]]:
    proposed_actions = _as_list(package.get("proposed_actions", []), where="proposed_actions")
    patches: List[Dict[str, Any]] = []
    for i, action in enumerate(proposed_actions):
        a = _as_dict(action, where=f"proposed_actions[{i}]")
        if a.get("type") != "child_bot.prompt_patch":
            continue
        title = str(a.get("title", "")).strip() or f"patch_{i}"
        content_md = str(a.get("content_md", "")).rstrip()
        if _is_blank(content_md):
            raise ValueError(f"proposed_actions[{i}].content_md must be non-empty for child_bot.prompt_patch")
        patches.append(
            {
                "index": i,
                "title": title,
                "mode": a.get("mode", "append"),
                "placement_hint": a.get("placement_hint", ""),
                "content_md": content_md,
                "rationale": a.get("rationale", ""),
                "confidence": a.get("confidence", None),
            }
        )
    return patches


def _render_prompt_patches_md(
    *,
    package_path: Path,
    package_sha256: str,
    generated_at: str,
    patches: List[Dict[str, Any]],
) -> str:
    lines: List[str] = []
    lines.append("# Robot Prompt Patches")
    lines.append("")
    lines.append(f"- package: `{package_path}`")
    lines.append(f"- package_sha256: `{package_sha256}`")
    if generated_at:
        lines.append(f"- package_generated_at: `{generated_at}`")
    lines.append(f"- exported_at: `{_now_iso()}`")
    lines.append("")

    if not patches:
        lines.append("> No `child_bot.prompt_patch` actions found.")
        lines.append("")
        return "\n".join(lines)

    for p in patches:
        lines.append(f"## {p['title']}")
        lines.append("")
        lines.append(f"- action_index: `{p['index']}`")
        lines.append(f"- mode: `{p['mode']}`")
        if str(p.get("placement_hint", "")).strip():
            lines.append(f"- placement_hint: {p['placement_hint']}")
        if p.get("confidence") is not None:
            lines.append(f"- confidence: `{p['confidence']}`")
        if str(p.get("rationale", "")).strip():
            lines.append(f"- rationale: {p['rationale']}")
        lines.append("")
        lines.append(p["content_md"])
        lines.append("")

    return "\n".join(lines)


@dataclass
class McpError(Exception):
    message: str
    data: Any | None = None

    def __str__(self) -> str:  # pragma: no cover
        if self.data is None:
            return self.message
        return f"{self.message} ({self.data})"


class McpStdioClient:
    def __init__(
        self,
        *,
        command: List[str],
        cwd: Optional[Path],
        env: Dict[str, str],
        startup_timeout_sec: float,
        tool_timeout_sec: float,
    ) -> None:
        self._command = command
        self._cwd = cwd
        self._env = env
        self._startup_timeout_sec = startup_timeout_sec
        self._tool_timeout_sec = tool_timeout_sec
        self._proc: subprocess.Popen[str] | None = None
        self._queue: queue.Queue[Dict[str, Any]] = queue.Queue()
        self._reader: threading.Thread | None = None
        self._next_id = 1

    def start(self) -> None:
        if self._proc is not None:
            return

        self._proc = subprocess.Popen(
            self._command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=None,
            cwd=str(self._cwd) if self._cwd else None,
            env=self._env,
            text=True,
            bufsize=1,
        )
        assert self._proc.stdin is not None
        assert self._proc.stdout is not None

        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

        self._initialize()

    def close(self) -> None:
        if self._proc is None:
            return
        proc = self._proc
        self._proc = None
        try:
            proc.terminate()
        except Exception:
            pass
        try:
            proc.wait(timeout=3)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
            try:
                proc.wait(timeout=3)
            except Exception:
                pass

        if self._reader is not None:
            self._reader.join(timeout=1)
            self._reader = None

    def call_tool(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        result = self._request("tools/call", {"name": name, "arguments": arguments}, timeout=self._tool_timeout_sec)
        if not isinstance(result, dict):
            return {"result": result}
        return result

    def _read_loop(self) -> None:
        assert self._proc is not None
        assert self._proc.stdout is not None
        for line in self._proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except Exception:
                continue
            if isinstance(msg, dict):
                self._queue.put(msg)

    def _initialize(self) -> None:
        self._request(
            "initialize",
            {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "codexread-apply", "version": "0.1.0"},
            },
            timeout=self._startup_timeout_sec,
        )
        self._notify("notifications/initialized", {})

    def _notify(self, method: str, params: Dict[str, Any]) -> None:
        if self._proc is None or self._proc.stdin is None:
            raise RuntimeError("MCP process not started")
        msg = {"jsonrpc": JSONRPC_VERSION, "method": method, "params": params}
        self._proc.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
        self._proc.stdin.flush()

    def _request(self, method: str, params: Dict[str, Any], *, timeout: float) -> Any:
        if self._proc is None or self._proc.stdin is None:
            raise RuntimeError("MCP process not started")
        request_id = self._next_id
        self._next_id += 1

        req = {"jsonrpc": JSONRPC_VERSION, "id": request_id, "method": method, "params": params}
        self._proc.stdin.write(json.dumps(req, ensure_ascii=False) + "\n")
        self._proc.stdin.flush()

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            remaining = max(0.0, deadline - time.monotonic())
            try:
                msg = self._queue.get(timeout=min(0.2, remaining))
            except queue.Empty:
                if self._proc.poll() is not None:
                    raise McpError("MCP process exited unexpectedly")
                continue

            if msg.get("id") != request_id:
                continue

            if "error" in msg:
                err = msg.get("error") or {}
                raise McpError(str(err.get("message", "mcp error")), err)
            return msg.get("result")

        raise TimeoutError(f"MCP request timed out: {method}")


def _parse_env_kv(items: Iterable[str]) -> Dict[str, str]:
    env: Dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise ValueError(f"--mem0-mcp-env expects KEY=VALUE (got: {item!r})")
        k, v = item.split("=", 1)
        k = k.strip()
        if not k:
            raise ValueError(f"--mem0-mcp-env expects KEY=VALUE (got: {item!r})")
        env[k] = v
    return env


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply an approved robot update package (review-first).")
    parser.add_argument("package", help="Path to robot update package JSON")
    parser.add_argument("--out-dir", default="state/robot-update-applies", help="Output directory (default: state/...)")
    parser.add_argument("--u1-user-id", default=None, help="Override target.u1_user_id")
    parser.add_argument("--child-user-id", default=None, help="Override target.child_user_id")
    parser.add_argument("--verify-inputs", action="store_true", help="Verify inputs[].path exists and sha256 matches")
    parser.add_argument("--force", action="store_true", help="Allow applying even if review.status != approved")

    parser.add_argument(
        "--apply-mem0",
        action="store_true",
        help="Apply mem0.add_memory actions via an external mem0-memory MCP (stdio).",
    )
    parser.add_argument(
        "--mem0-scope",
        choices=["u1", "child", "both"],
        default="u1",
        help="Which user_id memories to apply when --apply-mem0 (default: u1).",
    )
    parser.add_argument(
        "--mem0-mcp-command",
        default=os.getenv("MEM0_MCP_COMMAND", ""),
        help='Launch command for mem0-memory MCP (e.g. "python3 /path/to/server.py"). Can also set MEM0_MCP_COMMAND.',
    )
    parser.add_argument("--mem0-mcp-cwd", default=os.getenv("MEM0_MCP_CWD", ""), help="Cwd for mem0 MCP process")
    parser.add_argument(
        "--mem0-mcp-env",
        action="append",
        default=[],
        help="Extra env for mem0 MCP process (repeatable KEY=VALUE)",
    )
    parser.add_argument("--startup-timeout-sec", type=float, default=10.0, help="MCP startup timeout (seconds)")
    parser.add_argument("--tool-timeout-sec", type=float, default=60.0, help="Per-tool timeout (seconds)")
    parser.add_argument("--continue-on-error", action="store_true", help="Continue applying mem0 calls on errors")

    args = parser.parse_args()

    package_path = Path(args.package)
    if not package_path.exists():
        print(f"Package not found: {package_path}", file=sys.stderr)
        return 2

    package = json.loads(package_path.read_text(encoding="utf-8"))
    if not isinstance(package, dict):
        print("Invalid package: expected a JSON object at top level", file=sys.stderr)
        return 2

    try:
        _validate_review_status(package, force=bool(args.force))
        if args.verify_inputs:
            _verify_inputs(package)
        u1_user_id, child_user_id = _resolve_target_user_ids(
            package,
            u1_user_id_override=args.u1_user_id,
            child_user_id_override=args.child_user_id,
        )
    except ValueError as e:
        print(f"Validation error: {e}", file=sys.stderr)
        return 2

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    package_sha256 = _sha256_file(package_path)
    generated_at = str(package.get("generated_at", "")).strip()

    mem0_calls = _collect_mem0_calls(package, u1_user_id=u1_user_id, child_user_id=child_user_id)
    prompt_patches = _collect_prompt_patches(package)

    stem = package_path.name
    prompt_out = out_dir / f"{stem}.prompt_patches.md"
    mem0_out = out_dir / f"{stem}.mem0_calls.json"
    receipt_out = out_dir / f"{stem}.receipt.json"
    mem0_results_out = out_dir / f"{stem}.mem0_apply_results.json"

    prompt_out.write_text(
        _render_prompt_patches_md(
            package_path=package_path,
            package_sha256=package_sha256,
            generated_at=generated_at,
            patches=prompt_patches,
        ),
        encoding="utf-8",
    )
    mem0_out.write_text(json.dumps({"calls": mem0_calls}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    mem0_apply_results: List[Dict[str, Any]] = []
    if args.apply_mem0:
        if not args.mem0_mcp_command.strip():
            print("Missing --mem0-mcp-command (or MEM0_MCP_COMMAND) for --apply-mem0", file=sys.stderr)
            return 2

        cmd = shlex.split(args.mem0_mcp_command)
        if not cmd:
            print("Invalid --mem0-mcp-command", file=sys.stderr)
            return 2

        base_env = os.environ.copy()
        base_env.update(_parse_env_kv(args.mem0_mcp_env))
        cwd = Path(args.mem0_mcp_cwd).resolve() if args.mem0_mcp_cwd.strip() else None

        client = McpStdioClient(
            command=cmd,
            cwd=cwd,
            env=base_env,
            startup_timeout_sec=float(args.startup_timeout_sec),
            tool_timeout_sec=float(args.tool_timeout_sec),
        )
        try:
            client.start()
            for call in mem0_calls:
                args_obj = _as_dict(call.get("arguments", {}), where="mem0_call.arguments")
                uid = str(args_obj.get("user_id", "")).strip()

                if args.mem0_scope == "u1" and uid != u1_user_id:
                    continue
                if args.mem0_scope == "child" and uid != child_user_id:
                    continue

                try:
                    result = client.call_tool("add_memory", args_obj)
                    mem0_apply_results.append({"ok": True, "user_id": uid, "result": result, "call": call})
                except Exception as e:
                    mem0_apply_results.append({"ok": False, "user_id": uid, "error": str(e), "call": call})
                    if not args.continue_on_error:
                        break
        finally:
            client.close()

        mem0_results_out.write_text(
            json.dumps({"results": mem0_apply_results}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    receipt = {
        "applied_at": _now_iso(),
        "package_path": str(package_path),
        "package_sha256": package_sha256,
        "package_generated_at": generated_at or None,
        "review_status": _as_dict(package.get("review", {}), where="review").get("status", None),
        "target": {"u1_user_id": u1_user_id, "child_user_id": child_user_id},
        "outputs": {
            "prompt_patches_md": str(prompt_out),
            "mem0_calls_json": str(mem0_out),
            "mem0_apply_results_json": str(mem0_results_out) if args.apply_mem0 else None,
        },
        "counts": {"mem0_calls": len(mem0_calls), "prompt_patches": len(prompt_patches)},
        "mem0_applied": bool(args.apply_mem0),
        "mem0_scope": args.mem0_scope if args.apply_mem0 else None,
    }
    receipt_out.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(str(receipt_out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
