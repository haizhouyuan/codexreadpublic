#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


DEFAULT_API_BASE = "https://open.bigmodel.cn/api/paas/v4"
DEFAULT_KEY_ENV = "BIGMODEL_API_KEY"


@dataclass(frozen=True)
class CaseResult:
    name: str
    ok: bool
    detail: str
    preview: str


def _get_api_key(env_name: str) -> str:
    value = (os.environ.get(env_name) or "").strip()
    if not value:
        raise SystemExit(
            f"Missing API key. Set {env_name} in your environment (do NOT commit it). "
            f"Example: {env_name}='***' python3 {os.path.relpath(__file__)} --help"
        )
    return value


def _http_post_json(url: str, *, api_key: str, payload: dict[str, Any], timeout_sec: float) -> tuple[int, str]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url=url,
        method="POST",
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "codexread-smoke-test-glm/0.1",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return int(getattr(resp, "status", 200)), body
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return int(getattr(e, "code", 0) or 0), body


def _call_chat_completions(
    *,
    api_base: str,
    api_key: str,
    model: str,
    messages: list[dict[str, Any]],
    thinking_type: str | None,
    tools: list[dict[str, Any]] | None = None,
    tool_choice: str | dict[str, Any] | None = None,
    timeout_sec: float,
) -> tuple[int, dict[str, Any]]:
    payload: dict[str, Any] = {"model": model, "messages": messages}
    if thinking_type:
        payload["thinking"] = {"type": thinking_type}
    if tools is not None:
        payload["tools"] = tools
    if tool_choice is not None:
        payload["tool_choice"] = tool_choice

    status, body = _http_post_json(
        f"{api_base.rstrip('/')}/chat/completions",
        api_key=api_key,
        payload=payload,
        timeout_sec=timeout_sec,
    )
    try:
        return status, json.loads(body)
    except json.JSONDecodeError:
        return status, {"_raw": body}


def _extract_assistant(data: dict[str, Any]) -> tuple[str, Any]:
    try:
        choice0 = (data.get("choices") or [None])[0] or {}
        msg = choice0.get("message") or {}
        content = msg.get("content") or ""
        tool_calls = msg.get("tool_calls")
        return str(content), tool_calls
    except Exception:
        return "", None


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
    t = _strip_code_fences(text).strip()
    if t.startswith("{") and t.endswith("}"):
        return t
    if t.startswith("[") and t.endswith("]"):
        return t
    start_obj = t.find("{")
    end_obj = t.rfind("}")
    if start_obj != -1 and end_obj != -1 and end_obj > start_obj:
        return t[start_obj : end_obj + 1]
    start_arr = t.find("[")
    end_arr = t.rfind("]")
    if start_arr != -1 and end_arr != -1 and end_arr > start_arr:
        return t[start_arr : end_arr + 1]
    return t


def _preview(text: str, *, limit: int = 240) -> str:
    t = " ".join(str(text).split())
    if len(t) <= limit:
        return t
    return t[: limit - 3] + "..."


def _case_json_only(*, api_base: str, api_key: str, model: str, thinking_type: str | None, timeout_sec: float) -> CaseResult:
    prompt = (
        "只输出一个 JSON 对象（不要 Markdown，不要解释）。\n"
        "字段：ok(boolean), model(string), sum(number), why(string)。\n"
        "其中 sum = 1+2+3。"
    )
    status, data = _call_chat_completions(
        api_base=api_base,
        api_key=api_key,
        model=model,
        messages=[{"role": "user", "content": prompt}],
        thinking_type=thinking_type,
        timeout_sec=timeout_sec,
    )
    content, _tool_calls = _extract_assistant(data)
    if status != 200:
        return CaseResult("json_only", False, f"http_status={status}", _preview(json.dumps(data, ensure_ascii=False)))
    try:
        parsed = json.loads(_extract_json_candidate(content))
        ok = isinstance(parsed, dict) and parsed.get("sum") == 6
        return CaseResult("json_only", ok, "parsed_json" if ok else "bad_json_fields", _preview(content))
    except Exception:
        return CaseResult("json_only", False, "not_json", _preview(content))


def _case_tool_call(*, api_base: str, api_key: str, model: str, thinking_type: str | None, timeout_sec: float) -> CaseResult:
    tools = [
        {
            "type": "function",
            "function": {
                "name": "add_numbers",
                "description": "Add two integers and return their sum.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "a": {"type": "integer"},
                        "b": {"type": "integer"},
                    },
                    "required": ["a", "b"],
                },
            },
        }
    ]

    prompt = "请调用 add_numbers，令 a=2, b=40。不要直接给出答案。"
    status, data = _call_chat_completions(
        api_base=api_base,
        api_key=api_key,
        model=model,
        messages=[{"role": "user", "content": prompt}],
        thinking_type=thinking_type,
        tools=tools,
        tool_choice="auto",
        timeout_sec=timeout_sec,
    )
    content, tool_calls = _extract_assistant(data)
    if status != 200:
        return CaseResult("tool_call", False, f"http_status={status}", _preview(json.dumps(data, ensure_ascii=False)))
    if tool_calls:
        return CaseResult("tool_call", True, "tool_calls_present", _preview(json.dumps(tool_calls, ensure_ascii=False)))
    if content.strip():
        return CaseResult("tool_call", False, "no_tool_calls (content_returned)", _preview(content))
    return CaseResult("tool_call", False, "no_tool_calls", _preview(json.dumps(data, ensure_ascii=False)))


def _case_vision_url(
    *, api_base: str, api_key: str, model: str, thinking_type: str | None, timeout_sec: float, image_url: str
) -> CaseResult:
    prompt = "请用中文描述图片的主要内容，并列出你看到的关键对象（用逗号分隔）。"
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": image_url}},
                {"type": "text", "text": prompt},
            ],
        }
    ]
    status, data = _call_chat_completions(
        api_base=api_base,
        api_key=api_key,
        model=model,
        messages=messages,
        thinking_type=thinking_type,
        timeout_sec=timeout_sec,
    )
    content, _tool_calls = _extract_assistant(data)
    if status != 200:
        return CaseResult("vision_image_url", False, f"http_status={status}", _preview(json.dumps(data, ensure_ascii=False)))
    ok = bool(content.strip())
    return CaseResult("vision_image_url", ok, "ok" if ok else "empty_content", _preview(content))


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="smoke_test_glm_models.py")
    parser.add_argument("--api-base", default=DEFAULT_API_BASE, help=f"API base URL (default: {DEFAULT_API_BASE})")
    parser.add_argument(
        "--api-key-env",
        default=DEFAULT_KEY_ENV,
        help=f"Env var name holding API key (default: {DEFAULT_KEY_ENV})",
    )
    parser.add_argument(
        "--models",
        default="glm-4.5-flash,glm-4.6v-flash",
        help="Comma-separated model list to test (default: glm-4.5-flash,glm-4.6v-flash)",
    )
    parser.add_argument("--thinking", default="disabled", choices=["enabled", "disabled", "off"], help="thinking.type")
    parser.add_argument("--timeout-sec", type=float, default=60.0, help="HTTP timeout seconds (default: 60)")
    parser.add_argument(
        "--image-url",
        default="https://cloudcovert-1305175928.cos.ap-guangzhou.myqcloud.com/%E5%9B%BE%E7%89%87grounding.PNG",
        help="Image URL for glm-4.6v-flash vision smoke test.",
    )
    args = parser.parse_args(argv)

    api_key = _get_api_key(args.api_key_env)
    thinking = None if args.thinking == "off" else args.thinking
    models = [m.strip() for m in str(args.models).split(",") if m.strip()]
    if not models:
        raise SystemExit("--models is empty")

    started = time.time()
    results: list[tuple[str, CaseResult]] = []

    for model in models:
        results.append((model, _case_json_only(api_base=args.api_base, api_key=api_key, model=model, thinking_type=thinking, timeout_sec=args.timeout_sec)))
        results.append((model, _case_tool_call(api_base=args.api_base, api_key=api_key, model=model, thinking_type=thinking, timeout_sec=args.timeout_sec)))
        if "4.6v" in model or "vision" in model.lower():
            results.append(
                (
                    model,
                    _case_vision_url(
                        api_base=args.api_base,
                        api_key=api_key,
                        model=model,
                        thinking_type=thinking,
                        timeout_sec=args.timeout_sec,
                        image_url=args.image_url,
                    ),
                )
            )

    ok_all = True
    for model, res in results:
        status = "PASS" if res.ok else "FAIL"
        ok_all = ok_all and res.ok
        print(f"[{status}] {model} :: {res.name} :: {res.detail} :: {res.preview}")

    elapsed = time.time() - started
    print(f"\nDone in {elapsed:.1f}s. overall={'PASS' if ok_all else 'FAIL'}")
    return 0 if ok_all else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
