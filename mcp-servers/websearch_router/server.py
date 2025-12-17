#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import socket
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple, Union

MCP_PROTOCOL_VERSION = "2025-06-18"
JSONRPC_VERSION = "2.0"

RequestId = Union[str, int]

DEFAULT_CACHE_TTL_SECONDS = 86400
DEFAULT_TIMEOUT_SEC = 30.0
DEFAULT_MAX_RESULTS = 5

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_URL_RE = re.compile(r"https?://[^\s\)\]\}<>\"']+")

try:  # pragma: no cover - windows fallback
    import fcntl  # type: ignore
except Exception:  # pragma: no cover - windows fallback
    fcntl = None

_TRACKING_QUERY_KEYS = {
    "fbclid",
    "gclid",
    "igshid",
    "mc_cid",
    "mc_eid",
    "mkt_tok",
    "ref",
    "ref_src",
}


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _env_bool(name: str, default: bool) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    if raw == "":
        return default
    return raw not in ("0", "false", "no", "off")


def _env_int(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if raw == "":
        return default
    try:
        return int(raw)
    except Exception:
        return default


def _repo_root() -> Path:
    raw = (os.environ.get("WEBSEARCH_ROUTER_REPO_ROOT") or "").strip()
    return Path(raw).resolve(strict=False) if raw else Path.cwd().resolve(strict=False)


def _state_dir(repo_root: Path) -> Path:
    return repo_root / "state" / "websearch_router"


def _lock_path_for(path: Path) -> Path:
    return path.with_name(path.name + ".lock")


@contextlib.contextmanager
def _file_lock(lock_path: Path):
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    f = lock_path.open("a+", encoding="utf-8")
    try:
        if fcntl is not None:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            if fcntl is not None:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        finally:
            f.close()


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


def _normalize_url_for_dedupe(url: str) -> str:
    u = (url or "").strip()
    if not u:
        return ""
    try:
        parts = urllib.parse.urlsplit(u)
        scheme = (parts.scheme or "").lower()
        netloc = (parts.netloc or "").lower()
        path = parts.path or ""
        query = parts.query or ""
        if query:
            items = urllib.parse.parse_qsl(query, keep_blank_values=True)
            filtered = []
            for k, v in items:
                key = (k or "").strip().lower()
                if not key:
                    continue
                if key.startswith("utm_") or key in _TRACKING_QUERY_KEYS:
                    continue
                filtered.append((k, v))
            filtered.sort()
            query = urllib.parse.urlencode(filtered, doseq=True)
        return urllib.parse.urlunsplit((scheme, netloc, path, query, ""))
    except Exception:
        return u


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


def _call_result(*, text: str, structured: Any, is_error: bool = False) -> Dict[str, Any]:
    out: Dict[str, Any] = {"content": [_content_text(text)], "structuredContent": structured}
    if is_error:
        out["isError"] = True
    return out


def _tool(*, name: str, title: str, description: str, input_schema: Dict[str, Any], output_schema: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "name": name,
        "title": title,
        "description": description,
        "inputSchema": input_schema,
        "outputSchema": output_schema,
    }


def _tools_list() -> List[Dict[str, Any]]:
    search_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "max_results": {"type": "integer", "minimum": 1, "maximum": 20},
            "min_results": {"type": "integer", "minimum": 1, "maximum": 20},
            "language": {"type": "string", "description": "auto|en|zh-hans|zh-hant"},
            "recency": {"type": "string", "description": "noLimit|oneDay|oneWeek|oneMonth|oneYear"},
            "domain_filter": {"type": "string"},
            "allow_paid": {"type": "boolean"},
            "timeout_sec": {"type": "number", "minimum": 1, "maximum": 120},
            "use_cache": {"type": "boolean"},
        },
        "required": ["query"],
    }
    search_output_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "language": {"type": "string"},
            "provider_used": {"type": "string"},
            "cache_hit": {"type": "boolean"},
            "attempts": {"type": "array"},
            "results": {"type": "array"},
            "raw_path": {"type": "string"},
            "usage": {"type": "object"},
            "needs_followup": {"type": "boolean"},
        },
        "required": ["query", "language", "provider_used", "cache_hit", "attempts", "results", "usage", "needs_followup"],
    }

    usage_schema: Dict[str, Any] = {"type": "object", "properties": {}}
    usage_output_schema: Dict[str, Any] = {"type": "object", "properties": {"usage": {"type": "object"}}, "required": ["usage"]}

    return [
        _tool(
            name="websearch_router_search",
            title="WebSearch Router Search",
            description="Cost/Quota-aware web search with cache and free→quota→paid fallback.",
            input_schema=search_schema,
            output_schema=search_output_schema,
        ),
        _tool(
            name="websearch_router_get_usage",
            title="WebSearch Router Usage",
            description="Return local usage counters for each provider.",
            input_schema=usage_schema,
            output_schema=usage_output_schema,
        ),
    ]


def handle_initialize(request_id: RequestId, params: Dict[str, Any]) -> None:
    client_protocol = params.get("protocolVersion")
    protocol_version = MCP_PROTOCOL_VERSION if client_protocol in (None, MCP_PROTOCOL_VERSION) else client_protocol
    _send_result(
        request_id,
        {
            "protocolVersion": protocol_version,
            "serverInfo": {"name": "websearch-router-mcp", "version": "0.1.0"},
            "capabilities": {"tools": {"listChanged": False}},
            "instructions": "Provides websearch_router_search + websearch_router_get_usage with cost/quota-aware fallback.",
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


def _get_env_any(*keys: str) -> str:
    for k in keys:
        v = (os.environ.get(k) or "").strip()
        if v:
            return v
    return ""


def _http_request(
    url: str,
    *,
    method: str,
    headers: Dict[str, str],
    payload: Dict[str, Any] | None,
    timeout_sec: float,
) -> Tuple[int, str]:
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {**headers, "Content-Type": "application/json"}
    req = urllib.request.Request(url=url, method=method, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return int(getattr(resp, "status", 200)), body
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return int(getattr(e, "code", 0) or 0), body
    except (urllib.error.URLError, TimeoutError, socket.timeout) as e:
        return 0, json.dumps({"error": "transport_error", "detail": str(e)}, ensure_ascii=False)


def _cache_key(provider: str, params: Dict[str, Any]) -> str:
    blob = json.dumps({"provider": provider, **params}, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:32]


def _cache_path(repo_root: Path, provider: str, key: str) -> Path:
    return _state_dir(repo_root) / "cache" / provider / f"{key}.json"


def _load_cache(path: Path, *, ttl_seconds: int) -> Dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    created_at = data.get("created_at")
    if not isinstance(created_at, str) or not created_at:
        return None
    try:
        ts = datetime.fromisoformat(created_at.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None
    if time.time() - ts > ttl_seconds:
        return None
    return data


def _write_cache(path: Path, data: Dict[str, Any]) -> None:
    payload = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    _atomic_write_text(path, payload)


def _usage_path(repo_root: Path) -> Path:
    return _state_dir(repo_root) / "usage.json"


def _load_usage(repo_root: Path) -> Dict[str, Any]:
    path = _usage_path(repo_root)
    if not path.exists():
        return {"providers": {}, "updated_at": None}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"providers": {}, "updated_at": None}
    if not isinstance(data, dict):
        return {"providers": {}, "updated_at": None}
    if "providers" not in data or not isinstance(data.get("providers"), dict):
        data["providers"] = {}
    return data


def _save_usage(repo_root: Path, data: Dict[str, Any]) -> None:
    data["updated_at"] = _now_iso()
    path = _usage_path(repo_root)
    payload = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    _atomic_write_text(path, payload)


def _today_key() -> str:
    return time.strftime("%Y-%m-%d")


def _bump_usage(usage: Dict[str, Any], provider: str) -> None:
    providers = usage.setdefault("providers", {})
    p = providers.setdefault(provider, {"total": 0, "by_day": {}})
    p["total"] = int(p.get("total") or 0) + 1
    by_day = p.setdefault("by_day", {})
    day = _today_key()
    by_day[day] = int(by_day.get(day) or 0) + 1


def _usage_snapshot(usage: Dict[str, Any], provider: str) -> Dict[str, int]:
    p = (usage.get("providers") or {}).get(provider) or {}
    by_day = p.get("by_day") or {}
    day = _today_key()
    return {"total": int(p.get("total") or 0), "today": int(by_day.get(day) or 0)}


def _within_limits(usage: Dict[str, Any], provider: str) -> Tuple[bool, str | None]:
    snap = _usage_snapshot(usage, provider)
    if provider == "tongxiao_iqs":
        limit = _env_int("WEBSEARCH_ROUTER_LIMIT_TONGXIAO_TOTAL", 1000)
        if snap["total"] >= limit:
            return False, f"quota_exhausted(total>={limit})"
        return True, None

    per_day_defaults = {
        "brave": 500,
        "tavily": 200,
        "bigmodel_web_search": 50,
        "dashscope_web": 50,
    }
    env_key = f"WEBSEARCH_ROUTER_LIMIT_{provider.upper()}_PER_DAY"
    if provider == "bigmodel_web_search":
        env_key = "WEBSEARCH_ROUTER_LIMIT_BIGMODEL_PER_DAY"
    if provider == "dashscope_web":
        env_key = "WEBSEARCH_ROUTER_LIMIT_DASHSCOPE_PER_DAY"
    limit = _env_int(env_key, per_day_defaults.get(provider, 10_000))
    if snap["today"] >= limit:
        return False, f"rate_limited(today>={limit})"
    return True, None


def _detect_language(query: str) -> str:
    if _CJK_RE.search(query or ""):
        return "zh-hans"
    return "en"


def _filter_by_domain(results: List[Dict[str, Any]], domain_filter: str) -> List[Dict[str, Any]]:
    d = (domain_filter or "").strip()
    if not d:
        return results
    out: List[Dict[str, Any]] = []
    for r in results:
        url = str(r.get("url") or "")
        try:
            host = urllib.parse.urlparse(url).netloc.lower()
        except Exception:
            host = ""
        if host and (host == d.lower() or host.endswith("." + d.lower())):
            out.append(r)
    return out


def _brave_search(api_key: str, *, query: str, count: int, search_lang: str, timeout_sec: float) -> Tuple[bool, str | None, List[Dict[str, Any]], Any]:
    base = "https://api.search.brave.com/res/v1/web/search"
    params = {"q": query, "count": str(count), "search_lang": search_lang}
    url = f"{base}?{urllib.parse.urlencode(params)}"
    status, body = _http_request(url, method="GET", headers={"X-Subscription-Token": api_key, "Accept": "application/json"}, payload=None, timeout_sec=timeout_sec)
    if status == 422 and search_lang != "en":
        params["search_lang"] = "en"
        url = f"{base}?{urllib.parse.urlencode(params)}"
        status, body = _http_request(url, method="GET", headers={"X-Subscription-Token": api_key, "Accept": "application/json"}, payload=None, timeout_sec=timeout_sec)
    if status != 200:
        return False, f"http_{status}: {body[:300]}", [], None
    try:
        data = json.loads(body)
    except Exception:
        return False, "bad_json", [], None
    items = (((data.get("web") or {}).get("results")) or []) if isinstance(data, dict) else []
    results: List[Dict[str, Any]] = []
    for it in items[:count]:
        if not isinstance(it, dict):
            continue
        u = str(it.get("url") or "").strip()
        if not u:
            continue
        results.append(
            {
                "title": str(it.get("title") or "").strip(),
                "url": u,
                "snippet": str(it.get("description") or "").strip(),
                "published_at": None,
                "score": None,
                "source": "brave",
            }
        )
    return True, None, results, data


def _tavily_search(api_key: str, *, query: str, max_results: int, timeout_sec: float) -> Tuple[bool, str | None, List[Dict[str, Any]], Any]:
    url = "https://api.tavily.com/search"
    payload: Dict[str, Any] = {
        "api_key": api_key,
        "query": query,
        "search_depth": (os.environ.get("WEBSEARCH_ROUTER_TAVILY_DEPTH") or "basic").strip() or "basic",
        "max_results": int(max_results),
        "include_answer": False,
        "include_raw_content": False,
    }
    status, body = _http_request(url, method="POST", headers={"Accept": "application/json"}, payload=payload, timeout_sec=timeout_sec)
    if status != 200:
        return False, f"http_{status}: {body[:300]}", [], None
    try:
        data = json.loads(body)
    except Exception:
        return False, "bad_json", [], None
    items = (data.get("results") or []) if isinstance(data, dict) else []
    results: List[Dict[str, Any]] = []
    for it in items[:max_results]:
        if not isinstance(it, dict):
            continue
        u = str(it.get("url") or "").strip()
        if not u:
            continue
        results.append(
            {
                "title": str(it.get("title") or "").strip(),
                "url": u,
                "snippet": str(it.get("content") or "").strip(),
                "published_at": None,
                "score": it.get("score"),
                "source": "tavily",
            }
        )
    return True, None, results, data


def _tongxiao_iqs_search(api_key: str, *, query: str, num_results: int, timeout_sec: float) -> Tuple[bool, str | None, List[Dict[str, Any]], Any]:
    url = "https://cloud-iqs.aliyuncs.com/search/llm"
    payload = {"query": query, "numResults": int(num_results)}
    status, body = _http_request(
        url,
        method="POST",
        headers={"X-API-Key": api_key, "Accept": "application/json"},
        payload=payload,
        timeout_sec=timeout_sec,
    )
    if status != 200:
        return False, f"http_{status}: {body[:300]}", [], None
    try:
        data = json.loads(body)
    except Exception:
        return False, "bad_json", [], None
    items = (data.get("pageItems") or []) if isinstance(data, dict) else []
    results: List[Dict[str, Any]] = []
    for it in items[:num_results]:
        if not isinstance(it, dict):
            continue
        u = str(it.get("link") or "").strip()
        if not u:
            continue
        snippet = str(it.get("summary") or it.get("snippet") or "").strip()
        results.append(
            {
                "title": str(it.get("title") or "").strip(),
                "url": u,
                "snippet": snippet,
                "published_at": it.get("publishTime") or it.get("published_at") or None,
                "score": it.get("rerankScore") or None,
                "source": "tongxiao_iqs",
            }
        )
    return True, None, results, data


def _bigmodel_web_search(
    api_key: str,
    *,
    query: str,
    engine: str,
    count: int,
    recency: str,
    domain_filter: str | None,
    timeout_sec: float,
) -> Tuple[bool, str | None, List[Dict[str, Any]], Any]:
    url = "https://open.bigmodel.cn/api/paas/v4/web_search"
    payload: Dict[str, Any] = {
        "search_engine": engine,
        "search_query": query,
        "count": int(count),
        "search_intent": False,
        "search_recency_filter": recency or "noLimit",
        "content_size": (os.environ.get("WEBSEARCH_ROUTER_BIGMODEL_CONTENT_SIZE") or "medium").strip() or "medium",
    }
    if domain_filter:
        payload["search_domain_filter"] = domain_filter
    status, body = _http_request(
        url,
        method="POST",
        headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
        payload=payload,
        timeout_sec=timeout_sec,
    )
    if status != 200:
        return False, f"http_{status}: {body[:300]}", [], None
    try:
        data = json.loads(body)
    except Exception:
        return False, "bad_json", [], None
    items = (data.get("search_result") or []) if isinstance(data, dict) else []
    results: List[Dict[str, Any]] = []
    for it in items[:count]:
        if not isinstance(it, dict):
            continue
        u = str(it.get("link") or "").strip()
        if not u:
            continue
        results.append(
            {
                "title": str(it.get("title") or "").strip(),
                "url": u,
                "snippet": str(it.get("content") or "").strip(),
                "published_at": it.get("publish_date") or None,
                "score": None,
                "source": f"bigmodel_web_search/{engine}",
            }
        )
    return True, None, results, data


def _strip_code_fences(text: str) -> str:
    t = (text or "").strip()
    if t.startswith("```"):
        parts = t.split("```")
        if len(parts) >= 3:
            return parts[1].lstrip("json").lstrip().strip()
    return t


def _try_parse_json(text: str) -> Any | None:
    t = _strip_code_fences(text)
    try:
        return json.loads(t)
    except Exception:
        return None


def _extract_urls(text: str) -> List[str]:
    urls: List[str] = []
    for m in _URL_RE.finditer(text or ""):
        urls.append(m.group(0).rstrip(".,;:"))
    seen: set[str] = set()
    out: List[str] = []
    for u in urls:
        if u in seen:
            continue
        seen.add(u)
        out.append(u)
    return out


def _dashscope_web(
    api_key: str,
    *,
    query: str,
    model: str,
    timeout_sec: float,
) -> Tuple[bool, str | None, List[Dict[str, Any]], Any]:
    url = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
    system = (
        "你是一个检索助手。你必须使用联网搜索能力。"
        "输出时请优先给出可核验的一手来源（官方/标准组织/公司公告/技术白皮书），并提供完整 URL。"
    )
    user = (
        "请联网搜索并返回严格 JSON（不要代码块、不要多余文本）。\n"
        "JSON schema:\n"
        '{ "answer": string, "sources": [ { "title": string, "url": string } ], "notes": string }\n'
        f"问题：{query}"
    )
    payload: Dict[str, Any] = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "enable_search": True,
        "temperature": 0.2,
        "max_tokens": 900,
    }
    status, body = _http_request(
        url,
        method="POST",
        headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
        payload=payload,
        timeout_sec=timeout_sec,
    )
    if status != 200:
        return False, f"http_{status}: {body[:300]}", [], None
    try:
        data = json.loads(body)
    except Exception:
        return False, "bad_json", [], None
    content = (((data.get("choices") or [{}])[0].get("message") or {}).get("content")) if isinstance(data, dict) else ""
    content = content or ""
    parsed = _try_parse_json(content)
    sources: List[Dict[str, Any]] = []
    if isinstance(parsed, dict) and isinstance(parsed.get("sources"), list):
        sources = [s for s in parsed.get("sources") if isinstance(s, dict)]

    urls = []
    results: List[Dict[str, Any]] = []
    if sources:
        for s in sources:
            u = str(s.get("url") or "").strip()
            if not u:
                continue
            urls.append(u)
            results.append(
                {
                    "title": str(s.get("title") or "").strip(),
                    "url": u,
                    "snippet": str(parsed.get("answer") or "").strip() if isinstance(parsed, dict) else "",
                    "published_at": None,
                    "score": None,
                    "source": f"dashscope_web/{model}",
                }
            )
    else:
        urls = _extract_urls(content)
        for u in urls:
            results.append({"title": "", "url": u, "snippet": "", "published_at": None, "score": None, "source": f"dashscope_web/{model}"})

    if not results:
        return False, "no_sources", [], data
    return True, None, results, data


def _select_bigmodel_engines(language: str) -> List[str]:
    if language.startswith("zh"):
        return ["search_pro_quark", "search_pro_sogou", "search_pro", "search_std"]
    return ["search_pro_quark", "search_pro", "search_std"]


def _log_call(repo_root: Path, entry: Dict[str, Any]) -> None:
    path_raw = (os.environ.get("WEBSEARCH_ROUTER_CALL_LOG") or "").strip()
    if not path_raw:
        return
    include_query = _env_bool("WEBSEARCH_ROUTER_CALL_LOG_INCLUDE_QUERY", False)
    if not include_query and "query" in entry:
        entry.pop("query", None)
    path = Path(path_raw)
    if not path.is_absolute():
        path = (repo_root / path).resolve(strict=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _file_lock(_lock_path_for(path)):
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _route_search(
    repo_root: Path,
    *,
    query: str,
    max_results: int,
    min_results: int,
    language: str,
    allow_paid: bool,
    recency: str,
    domain_filter: str | None,
    timeout_sec: float,
    use_cache: bool,
) -> Dict[str, Any]:
    lang = language if language != "auto" else _detect_language(query)
    usage = _load_usage(repo_root)
    ttl = _env_int("WEBSEARCH_ROUTER_CACHE_TTL_SECONDS", DEFAULT_CACHE_TTL_SECONDS)

    providers: List[Tuple[str, str]] = [("brave", "free"), ("tavily", "free")]
    if lang.startswith("zh"):
        providers.append(("tongxiao_iqs", "quota"))
    if allow_paid:
        providers.append(("bigmodel_web_search", "paid"))
        providers.append(("dashscope_web", "paid"))

    attempts: List[Dict[str, Any]] = []
    best: Tuple[int, str, List[Dict[str, Any]], str | None] = (0, "", [], None)

    free_merged: List[Dict[str, Any]] = []
    free_seen: set[str] = set()
    free_sources: List[str] = []

    def _merge_free(provider_base: str, items: List[Dict[str, Any]]) -> None:
        if provider_base and provider_base not in free_sources:
            free_sources.append(provider_base)
        for it in items:
            u = str(it.get("url") or "").strip()
            if not u:
                continue
            key = _normalize_url_for_dedupe(u) or u
            if key in free_seen:
                continue
            free_seen.add(key)
            free_merged.append(it)

    def _free_provider_label() -> str:
        if not free_sources:
            return "combined_free"
        return "combined_free(" + "+".join(free_sources) + ")"

    def _free_cache_hit_overall() -> bool:
        ok = [a for a in attempts if a.get("tier") == "free" and a.get("ok") and not a.get("skipped")]
        return bool(ok) and all(bool(a.get("cache_hit")) for a in ok)

    for provider, tier in providers:
        ok_limit, limit_reason = _within_limits(usage, provider)
        if not ok_limit:
            attempts.append({"provider": provider, "tier": tier, "ok": False, "skipped": True, "reason": limit_reason, "cache_hit": False, "result_count": 0})
            continue

        cache_params = {
            "query": query,
            "max_results": max_results,
            "language": lang,
            "recency": recency,
            "domain_filter": domain_filter or "",
        }
        if provider == "tavily":
            cache_params["tavily_depth"] = (os.environ.get("WEBSEARCH_ROUTER_TAVILY_DEPTH") or "basic").strip() or "basic"
        elif provider == "dashscope_web":
            cache_params["dashscope_model"] = (os.environ.get("WEBSEARCH_ROUTER_DASHSCOPE_MODEL") or "qwen-turbo").strip() or "qwen-turbo"
        elif provider == "bigmodel_web_search":
            cache_params["bigmodel_content_size"] = (os.environ.get("WEBSEARCH_ROUTER_BIGMODEL_CONTENT_SIZE") or "medium").strip() or "medium"
            cache_params["bigmodel_engines"] = _select_bigmodel_engines(lang)
        ckey = _cache_key(provider, cache_params)
        cpath = _cache_path(repo_root, provider, ckey)
        if use_cache:
            cached = _load_cache(cpath, ttl_seconds=ttl)
            if cached and isinstance(cached.get("results"), list):
                cached_provider = str(cached.get("provider") or provider)
                results = [r for r in cached.get("results") if isinstance(r, dict)]
                results = _filter_by_domain(results, domain_filter or "") if domain_filter else results
                attempts.append({"provider": cached_provider, "tier": tier, "ok": True, "cache_hit": True, "result_count": len(results), "raw_path": str(cpath)})
                if tier == "free" and results and len(results) < min_results:
                    _merge_free(provider, results)
                    if len(free_merged) > best[0]:
                        best = (len(free_merged), _free_provider_label(), free_merged, None)
                    if len(free_merged) >= min_results:
                        return {
                            "query": query,
                            "language": lang,
                            "provider_used": _free_provider_label(),
                            "cache_hit": _free_cache_hit_overall(),
                            "attempts": attempts,
                            "results": free_merged[:max_results],
                            "raw_path": "",
                            "usage": {p: _usage_snapshot(usage, p) for p in ("brave", "tavily", "tongxiao_iqs", "bigmodel_web_search", "dashscope_web")},
                            "needs_followup": False,
                        }
                if len(results) > best[0]:
                    best = (len(results), cached_provider, results, str(cpath))
                if len(results) >= min_results:
                    return {
                        "query": query,
                        "language": lang,
                        "provider_used": cached_provider,
                        "cache_hit": True,
                        "attempts": attempts,
                        "results": results[:max_results],
                        "raw_path": str(cpath),
                        "usage": {p: _usage_snapshot(usage, p) for p in ("brave", "tavily", "tongxiao_iqs", "bigmodel_web_search", "dashscope_web")},
                        "needs_followup": False,
                    }
                continue

        started = time.time()
        error = None
        results: List[Dict[str, Any]] = []
        raw: Any = None
        raw_path: str | None = None
        provider_ok = False

        if provider == "brave":
            key = _get_env_any("BRAVE_API_KEY", "braveapikey")
            if not key:
                error = "missing BRAVE_API_KEY/braveapikey"
            else:
                provider_ok, error, results, raw = _brave_search(key, query=query, count=max_results, search_lang=lang, timeout_sec=timeout_sec)

        elif provider == "tavily":
            key = _get_env_any("TAVILY_API_KEY", "tavilyApiKey")
            if not key:
                error = "missing TAVILY_API_KEY/tavilyApiKey"
            else:
                provider_ok, error, results, raw = _tavily_search(key, query=query, max_results=max_results, timeout_sec=timeout_sec)

        elif provider == "tongxiao_iqs":
            key = _get_env_any("TONGXIAO_API_KEY")
            if not key:
                error = "missing TONGXIAO_API_KEY"
            else:
                provider_ok, error, results, raw = _tongxiao_iqs_search(key, query=query, num_results=max_results, timeout_sec=timeout_sec)

        elif provider == "bigmodel_web_search":
            key = _get_env_any("BIGMODEL_API_KEY")
            if not key:
                error = "missing BIGMODEL_API_KEY"
            else:
                for engine in _select_bigmodel_engines(lang):
                    provider_ok, error, results, raw = _bigmodel_web_search(
                        key,
                        query=query,
                        engine=engine,
                        count=max_results,
                        recency=recency,
                        domain_filter=domain_filter,
                        timeout_sec=timeout_sec,
                    )
                    if provider_ok and results:
                        provider = f"bigmodel_web_search/{engine}"
                        break

        elif provider == "dashscope_web":
            key = _get_env_any("DASHSCOPE_API_KEY", "WEBSEARCH_API_KEY")
            model = (os.environ.get("WEBSEARCH_ROUTER_DASHSCOPE_MODEL") or "qwen-turbo").strip() or "qwen-turbo"
            if not key:
                error = "missing DASHSCOPE_API_KEY/WEBSEARCH_API_KEY"
            else:
                provider_ok, error, results, raw = _dashscope_web(key, query=query, model=model, timeout_sec=timeout_sec)
                if provider_ok:
                    provider = f"dashscope_web/{model}"

        elapsed = time.time() - started
        results = _filter_by_domain(results, domain_filter or "") if domain_filter else results
        if provider_ok:
            base = provider.split("/")[0]
            with _file_lock(_lock_path_for(_usage_path(repo_root))):
                usage = _load_usage(repo_root)
                _bump_usage(usage, base)
                _save_usage(repo_root, usage)
            cache_payload = {
                "created_at": _now_iso(),
                "provider": provider,
                "tier": tier,
                "results": results,
                "raw": raw,
                "meta": {"language": lang, "recency": recency, "domain_filter": domain_filter or ""},
            }
            _write_cache(cpath, cache_payload)
            raw_path = str(cpath)

        attempts.append(
            {
                "provider": provider,
                "tier": tier,
                "ok": bool(provider_ok),
                "cache_hit": False,
                "elapsed_seconds": round(elapsed, 3),
                "result_count": len(results),
                "error": error,
                "raw_path": raw_path,
            }
        )

        _log_call(
            repo_root,
            {
                "ts": _now_iso(),
                "provider": provider,
                "tier": tier,
                "ok": bool(provider_ok),
                "result_count": len(results),
                "elapsed_seconds": round(elapsed, 3),
                "query_hash": hashlib.sha256(query.encode("utf-8")).hexdigest()[:16],
                "query": query,
            },
        )

        if provider_ok and tier == "free" and results and len(results) < min_results:
            base = provider.split("/")[0]
            _merge_free(base, results)
            if len(free_merged) > best[0]:
                best = (len(free_merged), _free_provider_label(), free_merged, None)
            if len(free_merged) >= min_results:
                return {
                    "query": query,
                    "language": lang,
                    "provider_used": _free_provider_label(),
                    "cache_hit": False,
                    "attempts": attempts,
                    "results": free_merged[:max_results],
                    "raw_path": "",
                    "usage": {p: _usage_snapshot(usage, p) for p in ("brave", "tavily", "tongxiao_iqs", "bigmodel_web_search", "dashscope_web")},
                    "needs_followup": False,
                }

        if len(results) > best[0]:
            best = (len(results), provider, results, raw_path)
        if provider_ok and len(results) >= min_results:
            return {
                "query": query,
                "language": lang,
                "provider_used": provider,
                "cache_hit": False,
                "attempts": attempts,
                "results": results[:max_results],
                "raw_path": raw_path or "",
                "usage": {p: _usage_snapshot(usage, p) for p in ("brave", "tavily", "tongxiao_iqs", "bigmodel_web_search", "dashscope_web")},
                "needs_followup": False,
            }

    needs_followup = True
    if best[1]:
        return {
            "query": query,
            "language": lang,
            "provider_used": best[1],
            "cache_hit": False,
            "attempts": attempts,
            "results": best[2][:max_results],
            "raw_path": best[3] or "",
            "usage": {p: _usage_snapshot(usage, p) for p in ("brave", "tavily", "tongxiao_iqs", "bigmodel_web_search", "dashscope_web")},
            "needs_followup": needs_followup,
        }
    return {
        "query": query,
        "language": lang,
        "provider_used": "none",
        "cache_hit": False,
        "attempts": attempts,
        "results": [],
        "raw_path": "",
        "usage": {p: _usage_snapshot(usage, p) for p in ("brave", "tavily", "tongxiao_iqs", "bigmodel_web_search", "dashscope_web")},
        "needs_followup": needs_followup,
    }


def handle_tools_call(request_id: RequestId, params: Dict[str, Any]) -> None:
    try:
        tool_name, args = _parse_call_params(params)
    except ValueError as e:
        _send_error(request_id, -32602, str(e))
        return

    repo_root = _repo_root()

    if tool_name == "websearch_router_get_usage":
        usage = _load_usage(repo_root)
        _send_result(request_id, _call_result(text="OK", structured={"usage": usage}))
        return

    if tool_name != "websearch_router_search":
        _send_result(request_id, {"content": [_content_text(f"Unknown tool: {tool_name}")], "isError": True})
        return

    query = str(args.get("query") or "").strip()
    if not query:
        _send_error(request_id, -32602, "query is required")
        return

    max_results = int(args.get("max_results") or DEFAULT_MAX_RESULTS)
    max_results = max(1, min(max_results, 20))
    min_results = int(args.get("min_results") or max_results)
    min_results = max(1, min(min_results, 20))
    language = str(args.get("language") or "auto").strip() or "auto"
    if language not in ("auto", "en", "zh-hans", "zh-hant"):
        language = "auto"
    allow_paid = bool(args.get("allow_paid", _env_bool("WEBSEARCH_ROUTER_ALLOW_PAID_DEFAULT", False)))
    recency = str(args.get("recency") or "noLimit").strip() or "noLimit"
    domain_filter = str(args.get("domain_filter") or "").strip() or None
    timeout_sec = float(args.get("timeout_sec") or DEFAULT_TIMEOUT_SEC)
    timeout_sec = max(1.0, min(timeout_sec, 120.0))
    use_cache = bool(args.get("use_cache", True))

    out = _route_search(
        repo_root,
        query=query,
        max_results=max_results,
        min_results=min_results,
        language=language,
        allow_paid=allow_paid,
        recency=recency,
        domain_filter=domain_filter,
        timeout_sec=timeout_sec,
        use_cache=use_cache,
    )
    text = f"websearch_router_search ok provider={out.get('provider_used')} results={len(out.get('results') or [])}"
    if out.get("needs_followup"):
        text += " (needs_followup=true)"
    _send_result(request_id, _call_result(text=text, structured=out))


def main(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(prog="websearch-router-mcp")
    parser.add_argument("--repo-root", default=None, help="Override WEBSEARCH_ROUTER_REPO_ROOT")
    args = parser.parse_args(argv)
    if args.repo_root:
        os.environ["WEBSEARCH_ROUTER_REPO_ROOT"] = str(Path(args.repo_root).resolve(strict=False))

    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception:
            continue
        if not isinstance(msg, dict):
            continue
        method = msg.get("method")
        request_id = msg.get("id")
        params = msg.get("params") if isinstance(msg.get("params"), dict) else {}

        if method == "initialize":
            handle_initialize(request_id, params)
        elif method == "tools/list":
            handle_tools_list(request_id, params)
        elif method == "tools/call":
            handle_tools_call(request_id, params)
        else:
            _send_error(request_id, -32601, f"method not found: {method}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
