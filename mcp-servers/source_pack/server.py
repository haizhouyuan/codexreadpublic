#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

MCP_PROTOCOL_VERSION = "2025-06-18"
JSONRPC_VERSION = "2.0"

RequestId = Union[str, int]

DEFAULT_TIMEOUT_SEC = 30.0
DEFAULT_MIN_CHARS = 2000

_DEFAULT_USER_AGENT = "codexread/0.1 (+https://github.com/haizhouyuan/codexread)"
# Some sites (notably SEC EDGAR) require a descriptive User-Agent with contact info.
# Allow users to set SOURCE_PACK_USER_AGENT via .env without committing secrets/PII.
USER_AGENT = (os.environ.get("SOURCE_PACK_USER_AGENT") or os.environ.get("SEC_USER_AGENT") or _DEFAULT_USER_AGENT).strip() or _DEFAULT_USER_AGENT


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


def _as_list_str(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        out: List[str] = []
        for x in value:
            if isinstance(x, str) and x.strip():
                out.append(x.strip())
        return out
    raise ValueError("expected array")


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
    fetch_input_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "url": {"type": "string"},
            "topic_id": {"type": "string"},
            "pack_id": {"type": "string"},
            "out_dir": {"type": "string", "description": "Optional output dir; must be under state/source_packs unless allow override."},
            "allow_paid": {"type": "boolean", "description": "Allow quota/paid fetchers (default false)."},
            "fetchers": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Ordered fetchers: local,jina_reader,tavily_extract,bigmodel_reader",
            },
            "timeout_sec": {"type": "number", "minimum": 1, "maximum": 300},
            "min_chars": {"type": "integer", "minimum": 0, "maximum": 2_000_000},
            "meta": {"type": "object", "description": "Opaque metadata echoed back."},
        },
        "required": ["url"],
    }

    attempt_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "fetcher": {"type": "string"},
            "tier": {"type": "string", "description": "free|quota|paid"},
            "ok": {"type": "boolean"},
            "seconds": {"type": "number"},
            "chars": {"type": "integer"},
            "content_type": {"type": "string"},
            "final_url": {"type": "string"},
            "raw_path": {"type": "string"},
            "text_path": {"type": "string"},
            "links_path": {"type": "string"},
            "reason": {"type": "string"},
            "error": {"type": "string"},
        },
        "required": ["fetcher", "tier", "ok", "seconds", "chars"],
    }

    fetch_output_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "url": {"type": "string"},
            "final_url": {"type": "string"},
            "topic_id": {"type": "string"},
            "pack_id": {"type": "string"},
            "status": {"type": "string", "description": "done|partial|blocked|failed"},
            "fetcher_used": {"type": "string"},
            "out_dir": {"type": "string"},
            "manifest_path": {"type": "string"},
            "text_path": {"type": "string"},
            "raw_path": {"type": "string"},
            "links_path": {"type": "string"},
            "chars": {"type": "integer"},
            "attempts": {"type": "array", "items": attempt_schema},
            "needs_followup": {"type": "boolean"},
            "meta": {"type": "object"},
        },
        "required": ["url", "pack_id", "status", "out_dir", "manifest_path", "attempts", "needs_followup"],
    }

    return [
        _tool(
            name="source_pack_fetch",
            title="Source Pack Fetch",
            description="Fetch a URL into a local evidence pack directory (manifest + raw + extracted text). Returns only metadata+paths.",
            input_schema=fetch_input_schema,
            output_schema=fetch_output_schema,
        )
    ]


def handle_initialize(request_id: RequestId, params: Dict[str, Any]) -> None:
    client_protocol = params.get("protocolVersion")
    protocol_version = MCP_PROTOCOL_VERSION if client_protocol in (None, MCP_PROTOCOL_VERSION) else client_protocol
    _send_result(
        request_id,
        {
            "protocolVersion": protocol_version,
            "serverInfo": {"name": "source-pack-mcp", "version": "0.1.0"},
            "capabilities": {"tools": {"listChanged": False}},
            "instructions": "Provides source_pack_fetch (URL → evidence pack on disk).",
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


def _repo_root() -> Path:
    root = (os.environ.get("SOURCE_PACK_REPO_ROOT") or "").strip()
    return Path(root).resolve(strict=False) if root else Path.cwd().resolve(strict=False)


def _is_within(path: Path, parent: Path) -> bool:
    return path == parent or parent in path.parents


def _safe_slug(value: str, *, max_len: int = 80) -> str:
    s = str(value or "").strip()
    s = re.sub(r"^https?://", "", s)
    s = re.sub(r"[\s\t\r\n]+", "_", s)
    s = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff._-]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    if not s:
        s = "id"
    if len(s) > max_len:
        s = s[:max_len].rstrip("_")
    return s


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    os.makedirs(path.parent, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp.{os.getpid()}.{int(time.time() * 1000)}")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _atomic_write_text(path: Path, text: str) -> None:
    _atomic_write_bytes(path, (text or "").encode("utf-8"))


def _http_get(url: str, *, timeout_sec: float, headers: Dict[str, str]) -> Tuple[int, str, bytes, Dict[str, str], str]:
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            status = int(getattr(resp, "status", 200))
            final_url = str(getattr(resp, "geturl", lambda: url)())
            raw = resp.read()
            hdrs = {k: v for k, v in (getattr(resp, "headers", {}) or {}).items()}
            return status, final_url, raw, hdrs, ""
    except urllib.error.HTTPError as e:
        body = e.read()
        hdrs = {k: v for k, v in (getattr(e, "headers", {}) or {}).items()}
        final_url = str(getattr(e, "geturl", lambda: url)())
        return int(getattr(e, "code", 0) or 0), final_url, body, hdrs, f"http_error_{getattr(e, 'code', 0)}"
    except (urllib.error.URLError, TimeoutError, socket.timeout) as e:
        return 0, url, b"", {}, f"transport_error: {e}"


def _detect_block_reason(text: str, *, url: str | None = None) -> str | None:
    t = (text or "").lower()

    # Challenges / bot blocks
    if "antispider" in t or "请输入验证码" in (text or "") or "captcha" in t:
        return "blocked_challenge"
    if "cloudflare" in t and ("attention required" in t or "checking your browser" in t):
        return "blocked_challenge"

    host = ""
    if url:
        try:
            host = (urllib.parse.urlparse(url).netloc or "").lower()
        except Exception:
            host = ""

    # SEC filings contain many occurrences of words like "subscription" (e.g. "subscription agreement"),
    # which can trigger naive paywall detectors. For sec.gov, only treat explicit challenges as blocked.
    if host.endswith("sec.gov"):
        return None

    # Login / paywall heuristics (avoid false positives on long technical/legal docs).
    if re.search(r"\b(sign in|log in|login)\b", t) and re.search(r"\b(password|email|username)\b", t):
        return "login_required"

    if re.search(
        r"(subscription required|subscribe (now|to (read|continue|access|view))|already a subscriber|for subscribers only)",
        t,
    ):
        return "paywalled"

    return None


def _html_to_text(html_text: str) -> str:
    # SEC/inline XBRL pages often embed huge hidden blocks (ix:hidden / display:none) that bloat extraction.
    s = re.sub(r"(?is)<ix:hidden[^>]*>.*?</ix:hidden>", " ", html_text)
    s = re.sub(r"(?is)<ix:header[^>]*>.*?</ix:header>", " ", s)
    s = re.sub(
        r'(?is)<(div|span)[^>]*style=["\'][^"\']*display\s*:\s*none[^"\']*["\'][^>]*>.*?</\1>',
        " ",
        s,
    )
    # Minimal extractor: strip scripts/styles/tags.
    s = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", s)
    s = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", s)
    s = re.sub(r"(?is)<!--.*?-->", " ", s)
    s = re.sub(r"(?is)<noscript[^>]*>.*?</noscript>", " ", s)
    s = re.sub(r"(?i)<br\s*/?>", "\n", s)
    s = re.sub(r"(?i)</(p|div|h1|h2|h3|h4|h5|h6|li|tr)>", "\n", s)
    s = re.sub(r"(?is)<[^>]+>", " ", s)
    s = s.replace("\u00a0", " ")
    lines: List[str] = []
    for line in s.splitlines():
        line = re.sub(r"[ \\t]+", " ", line).strip()
        if not line:
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def _pdf_to_text(raw: bytes) -> str:
    try:
        from pypdf import PdfReader  # type: ignore[import-not-found]
    except Exception as exc:  # pragma: no cover - optional dependency
        raise RuntimeError("pypdf is required for PDF text extraction") from exc
    reader = PdfReader(io.BytesIO(raw))
    parts: List[str] = []
    for page in reader.pages:
        try:
            t = page.extract_text() or ""
        except Exception:
            t = ""
        t = t.replace("\u00a0", " ")
        t = re.sub(r"[ \\t]+", " ", t)
        t = "\n".join([ln.strip() for ln in t.splitlines() if ln.strip()])
        if t:
            parts.append(t)
    return "\n\n".join(parts).strip()


def _extract_links(html_text: str, *, base_url: str) -> Dict[str, Any]:
    hrefs: List[str] = []
    for m in re.finditer(r"(?is)\bhref\s*=\s*(['\"])([^'\"]+)\1", html_text):
        href = m.group(2).strip()
        if not href:
            continue
        if href.startswith("javascript:") or href.startswith("mailto:"):
            continue
        hrefs.append(href)
    abs_urls: List[str] = []
    for href in hrefs:
        try:
            u = urllib.parse.urljoin(base_url, href)
        except Exception:
            continue
        if u.startswith("http://") or u.startswith("https://"):
            abs_urls.append(u)
    # canonical / og:url
    canonical = None
    m = re.search(r'(?is)<link[^>]+rel=[\"\\\']canonical[\"\\\'][^>]*href=[\"\\\']([^\"\\\']+)', html_text)
    if m:
        canonical = urllib.parse.urljoin(base_url, m.group(1).strip())
    og_url = None
    m = re.search(r'(?is)<meta[^>]+property=[\"\\\']og:url[\"\\\'][^>]*content=[\"\\\']([^\"\\\']+)', html_text)
    if m:
        og_url = urllib.parse.urljoin(base_url, m.group(1).strip())
    uniq: List[str] = []
    seen = set()
    for u in abs_urls:
        if u in seen:
            continue
        seen.add(u)
        uniq.append(u)
    return {"canonical": canonical, "og_url": og_url, "hrefs": uniq}


def _jina_reader_url(url: str) -> str:
    if url.startswith("https://"):
        return "https://r.jina.ai/https://" + url[len("https://") :]
    if url.startswith("http://"):
        return "https://r.jina.ai/http://" + url[len("http://") :]
    raise ValueError("url must start with http:// or https://")


def _http_post_json(url: str, *, payload: Dict[str, Any], timeout_sec: float, headers: Dict[str, str]) -> Tuple[int, str]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return int(getattr(resp, "status", 200)), body
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return int(getattr(e, "code", 0) or 0), body
    except (urllib.error.URLError, TimeoutError, socket.timeout) as e:
        return 0, json.dumps({"error": "transport_error", "detail": str(e)}, ensure_ascii=False)


def _tavily_extract(api_key: str, *, url: str, timeout_sec: float, depth: str) -> Dict[str, Any]:
    status, body = _http_post_json(
        "https://api.tavily.com/extract",
        payload={
            "api_key": api_key,
            "urls": [url],
            "extract_depth": depth,
            "include_images": False,
        },
        timeout_sec=timeout_sec,
        headers={"Content-Type": "application/json", "User-Agent": "codexread-source-pack/0.1"},
    )
    if status >= 400 or status == 0:
        raise RuntimeError(f"tavily_extract http_{status}")
    return json.loads(body)


def _bigmodel_reader(api_key: str, *, url: str, timeout_sec: float) -> Dict[str, Any]:
    status, body = _http_post_json(
        "https://open.bigmodel.cn/api/paas/v4/reader",
        payload={"url": url, "timeout": int(timeout_sec), "no_cache": True, "return_format": "markdown", "with_links_summary": True},
        timeout_sec=max(5.0, timeout_sec + 30.0),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}", "User-Agent": "codexread-source-pack/0.1"},
    )
    if status >= 400 or status == 0:
        raise RuntimeError(f"bigmodel_reader http_{status}")
    return json.loads(body)


def _resolve_out_dir(
    *,
    repo_root: Path,
    topic_id: str | None,
    pack_id: str,
    out_dir_raw: str | None,
) -> Path:
    base_raw = (os.environ.get("SOURCE_PACK_BASE_DIR") or "").strip()
    base_dir = (repo_root / "state" / "source_packs") if not base_raw else (Path(base_raw) if Path(base_raw).is_absolute() else (repo_root / base_raw))
    base_dir = base_dir.resolve(strict=False)
    topic_part = _safe_slug(topic_id) if topic_id else ""
    default_dir = (base_dir / topic_part / pack_id) if topic_part else (base_dir / pack_id)

    if out_dir_raw and str(out_dir_raw).strip():
        p0 = Path(str(out_dir_raw).strip())
        out_dir = p0 if p0.is_absolute() else (repo_root / p0)
        out_dir = out_dir.resolve(strict=False)
    else:
        out_dir = default_dir.resolve(strict=False)

    allow_outside = _env_bool("SOURCE_PACK_ALLOW_OUTSIDE_STATE", False)
    if not allow_outside and not _is_within(out_dir, base_dir):
        raise ValueError(f"out_dir must be under {base_dir} (set SOURCE_PACK_ALLOW_OUTSIDE_STATE=1 to override)")
    if not _is_within(out_dir, repo_root):
        raise ValueError(f"out_dir must be under repo root: {repo_root}")
    return out_dir


def _attempt_record(
    *,
    fetcher: str,
    tier: str,
    ok: bool,
    seconds: float,
    chars: int,
    content_type: str | None = None,
    final_url: str | None = None,
    raw_path: str | None = None,
    text_path: str | None = None,
    links_path: str | None = None,
    reason: str | None = None,
    error: str | None = None,
) -> Dict[str, Any]:
    out: Dict[str, Any] = {"fetcher": fetcher, "tier": tier, "ok": bool(ok), "seconds": float(seconds), "chars": int(chars)}
    if content_type:
        out["content_type"] = content_type
    if final_url:
        out["final_url"] = final_url
    if raw_path:
        out["raw_path"] = raw_path
    if text_path:
        out["text_path"] = text_path
    if links_path:
        out["links_path"] = links_path
    if reason:
        out["reason"] = reason
    if error:
        out["error"] = error
    return out


def _fetch_local(url: str, *, out_dir: Path, timeout_sec: float) -> Tuple[Dict[str, Any], str | None]:
    started = time.time()
    status, final_url, raw, hdrs, err = _http_get(
        url,
        timeout_sec=timeout_sec,
        headers={"User-Agent": USER_AGENT, "Accept": "text/html,*/*"},
    )
    elapsed = time.time() - started

    content_type = (hdrs.get("Content-Type") or hdrs.get("content-type") or "").strip()
    is_pdf = "application/pdf" in content_type.lower() or url.lower().endswith(".pdf") or raw[:4] == b"%PDF"
    block_reason = None

    raw_path = None
    links_path = None
    text_path = None
    extracted_text = ""

    try:
        if status == 0:
            raise RuntimeError(err or "transport_error")
        if status >= 400:
            # still write body for debugging (may contain challenge page)
            if raw:
                raw_path = str(out_dir / "raw_error.html")
                _atomic_write_bytes(Path(raw_path), raw)
                block_reason = _detect_block_reason(raw.decode("utf-8", errors="ignore"), url=final_url or url)
            raise RuntimeError(f"http_{status}")

        if is_pdf:
            raw_path = str(out_dir / "download.pdf")
            _atomic_write_bytes(Path(raw_path), raw)
            extracted_text = _pdf_to_text(raw)
        else:
            raw_path = str(out_dir / "raw.html")
            _atomic_write_bytes(Path(raw_path), raw)
            html_text = raw.decode("utf-8", errors="ignore")
            block_reason = _detect_block_reason(html_text, url=final_url or url)
            if block_reason:
                raise RuntimeError(block_reason)
            extracted_text = _html_to_text(html_text)
            links = _extract_links(html_text, base_url=final_url or url)
            links_path = str(out_dir / "links_local.json")
            _atomic_write_text(Path(links_path), json.dumps(links, ensure_ascii=False, indent=2) + "\n")

        if extracted_text.strip():
            text_path = str(out_dir / "text_local.md")
            _atomic_write_text(Path(text_path), extracted_text + "\n")
        attempt = _attempt_record(
            fetcher="local",
            tier="free",
            ok=bool(extracted_text.strip()),
            seconds=elapsed,
            chars=len(extracted_text),
            content_type=content_type or None,
            final_url=final_url or None,
            raw_path=raw_path,
            text_path=text_path,
            links_path=links_path,
            reason=None,
            error=None,
        )
    except Exception as e:
        attempt = _attempt_record(
            fetcher="local",
            tier="free",
            ok=False,
            seconds=elapsed,
            chars=0,
            content_type=content_type or None,
            final_url=final_url or None,
            raw_path=raw_path,
            links_path=links_path,
            reason=block_reason,
            error=str(e),
        )
    return attempt, (final_url or url)


def _fetch_jina_reader(url: str, *, out_dir: Path, timeout_sec: float) -> Tuple[Dict[str, Any], str | None]:
    started = time.time()
    reader_url = _jina_reader_url(url)
    status, final_url, raw, hdrs, err = _http_get(
        reader_url,
        timeout_sec=timeout_sec,
        headers={"User-Agent": USER_AGENT, "Accept": "text/plain,*/*"},
    )
    elapsed = time.time() - started

    content_type = (hdrs.get("Content-Type") or hdrs.get("content-type") or "").strip()
    raw_path = None
    text_path = None

    try:
        if status == 0:
            raise RuntimeError(err or "transport_error")
        if status >= 400:
            raise RuntimeError(f"http_{status}")
        text = raw.decode("utf-8", errors="ignore").strip()
        if not text:
            raise RuntimeError("empty_body")
        raw_path = str(out_dir / "reader.txt")
        _atomic_write_text(Path(raw_path), text + "\n")
        text_path = str(out_dir / "text_jina_reader.md")
        _atomic_write_text(Path(text_path), text + "\n")
        attempt = _attempt_record(
            fetcher="jina_reader",
            tier="free",
            ok=True,
            seconds=elapsed,
            chars=len(text),
            content_type=content_type or None,
            final_url=final_url or None,
            raw_path=raw_path,
            text_path=text_path,
        )
    except Exception as e:
        attempt = _attempt_record(
            fetcher="jina_reader",
            tier="free",
            ok=False,
            seconds=elapsed,
            chars=0,
            content_type=content_type or None,
            final_url=final_url or None,
            raw_path=raw_path,
            text_path=text_path,
            error=str(e),
        )
    return attempt, url


def _fetch_tavily_extract(url: str, *, out_dir: Path, timeout_sec: float) -> Tuple[Dict[str, Any], str | None]:
    started = time.time()
    key = (os.environ.get("TAVILY_API_KEY") or os.environ.get("tavilyApiKey") or "").strip()
    depth = (os.environ.get("SOURCE_PACK_TAVILY_EXTRACT_DEPTH") or os.environ.get("WEBSEARCH_ROUTER_TAVILY_DEPTH") or "basic").strip() or "basic"
    raw_path = None
    text_path = None

    try:
        if not key:
            raise RuntimeError("missing TAVILY_API_KEY/tavilyApiKey")
        data = _tavily_extract(key, url=url, timeout_sec=timeout_sec, depth=depth)
        raw_path = str(out_dir / "extract.json")
        _atomic_write_text(Path(raw_path), json.dumps(data, ensure_ascii=False, indent=2) + "\n")

        content = ""
        if isinstance(data.get("results"), list) and data["results"]:
            item = data["results"][0]
            if isinstance(item, dict):
                content = str(item.get("content") or item.get("raw_content") or item.get("text") or "").strip()
        if not content and isinstance(data.get("content"), str):
            content = data["content"].strip()
        if not content:
            raise RuntimeError("no_content_in_response")

        text_path = str(out_dir / "text_tavily_extract.md")
        _atomic_write_text(Path(text_path), content + "\n")
        attempt = _attempt_record(
            fetcher="tavily_extract",
            tier="quota",
            ok=True,
            seconds=time.time() - started,
            chars=len(content),
            content_type="application/json",
            raw_path=raw_path,
            text_path=text_path,
            reason=f"depth={depth}",
        )
    except Exception as e:
        attempt = _attempt_record(
            fetcher="tavily_extract",
            tier="quota",
            ok=False,
            seconds=time.time() - started,
            chars=0,
            content_type="application/json",
            raw_path=raw_path,
            text_path=text_path,
            error=str(e),
            reason=f"depth={depth}",
        )
    return attempt, url


def _fetch_bigmodel_reader(url: str, *, out_dir: Path, timeout_sec: float) -> Tuple[Dict[str, Any], str | None]:
    started = time.time()
    key = (os.environ.get("BIGMODEL_API_KEY") or "").strip()
    raw_path = None
    text_path = None

    try:
        if not key:
            raise RuntimeError("missing BIGMODEL_API_KEY")
        data = _bigmodel_reader(key, url=url, timeout_sec=timeout_sec)
        raw_path = str(out_dir / "reader.json")
        _atomic_write_text(Path(raw_path), json.dumps(data, ensure_ascii=False, indent=2) + "\n")

        rr = data.get("reader_result") or {}
        content = str(rr.get("content") or "").strip()
        if not content:
            raise RuntimeError("empty_reader_result_content")
        text_path = str(out_dir / "text_bigmodel_reader.md")
        _atomic_write_text(Path(text_path), content + "\n")
        attempt = _attempt_record(
            fetcher="bigmodel_reader",
            tier="paid",
            ok=True,
            seconds=time.time() - started,
            chars=len(content),
            content_type="application/json",
            raw_path=raw_path,
            text_path=text_path,
            reason=str(rr.get("title") or "").strip()[:80] or None,
        )
    except Exception as e:
        attempt = _attempt_record(
            fetcher="bigmodel_reader",
            tier="paid",
            ok=False,
            seconds=time.time() - started,
            chars=0,
            content_type="application/json",
            raw_path=raw_path,
            text_path=text_path,
            error=str(e),
        )
    return attempt, url


def _choose_best_attempt(attempts: List[Dict[str, Any]], *, min_chars: int) -> Tuple[Optional[Dict[str, Any]], str]:
    # Prefer first attempt that meets min_chars; otherwise pick max chars among ok attempts.
    for a in attempts:
        if a.get("ok") and int(a.get("chars") or 0) >= min_chars and a.get("text_path"):
            return a, "meets_min_chars"
    best = None
    best_chars = -1
    for a in attempts:
        if not a.get("ok") or not a.get("text_path"):
            continue
        chars = int(a.get("chars") or 0)
        if chars > best_chars:
            best_chars = chars
            best = a
    return best, "best_effort"


def _write_manifest(out_dir: Path, *, payload: Dict[str, Any]) -> str:
    path = out_dir / "manifest.json"
    _atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return str(path)


def handle_source_pack_fetch(request_id: RequestId, args: Dict[str, Any]) -> None:
    url = str(args.get("url") or "").strip()
    if not url.startswith("http://") and not url.startswith("https://"):
        _send_error(request_id, -32602, "url must start with http:// or https://")
        return

    topic_id = str(args.get("topic_id") or "").strip() or None
    pack_id_raw = str(args.get("pack_id") or "").strip()
    allow_paid = bool(args.get("allow_paid") or False)

    fetchers = _as_list_str(args.get("fetchers"))
    if not fetchers:
        fetchers = ["local", "jina_reader", "tavily_extract", "bigmodel_reader"]

    timeout_sec = float(args.get("timeout_sec") or DEFAULT_TIMEOUT_SEC)
    timeout_sec = max(1.0, min(300.0, timeout_sec))
    min_chars = int(args.get("min_chars") or DEFAULT_MIN_CHARS)
    min_chars = max(0, min(2_000_000, min_chars))
    meta = args.get("meta") if isinstance(args.get("meta"), dict) else None

    repo_root = _repo_root()
    url_hash8 = hashlib.sha256(url.encode("utf-8")).hexdigest()[:8]
    if pack_id_raw:
        pack_id = _safe_slug(pack_id_raw, max_len=120)
    else:
        date = datetime.now(UTC).strftime("%Y-%m-%d")
        slug = _safe_slug(urllib.parse.urlparse(url).netloc + "_" + urllib.parse.urlparse(url).path, max_len=60)
        pack_id = f"{date}_{slug}_{url_hash8}"

    try:
        out_dir = _resolve_out_dir(repo_root=repo_root, topic_id=topic_id, pack_id=pack_id, out_dir_raw=args.get("out_dir"))
    except Exception as e:
        _send_error(request_id, -32602, f"invalid out_dir: {e}")
        return

    os.makedirs(out_dir, exist_ok=True)

    attempts: List[Dict[str, Any]] = []
    final_url = url

    for f in fetchers:
        f_norm = f.strip()
        if not f_norm:
            continue
        if f_norm in ("tavily_extract", "bigmodel_reader") and not allow_paid:
            continue

        if f_norm == "local":
            a, final_url = _fetch_local(url, out_dir=out_dir, timeout_sec=timeout_sec)
        elif f_norm == "jina_reader":
            a, _ = _fetch_jina_reader(url, out_dir=out_dir, timeout_sec=timeout_sec)
        elif f_norm == "tavily_extract":
            a, _ = _fetch_tavily_extract(url, out_dir=out_dir, timeout_sec=timeout_sec)
        elif f_norm == "bigmodel_reader":
            a, _ = _fetch_bigmodel_reader(url, out_dir=out_dir, timeout_sec=timeout_sec)
        else:
            a = _attempt_record(fetcher=f_norm, tier="free", ok=False, seconds=0.0, chars=0, error="unknown_fetcher")
        attempts.append(a)
        if a.get("ok") and int(a.get("chars") or 0) >= min_chars and a.get("text_path"):
            break

    chosen, choose_reason = _choose_best_attempt(attempts, min_chars=min_chars)

    status = "failed"
    needs_followup = True
    fetcher_used = ""
    text_path = None
    raw_path = None
    links_path = None
    chars = 0

    if chosen and chosen.get("ok"):
        fetcher_used = str(chosen.get("fetcher") or "")
        text_path = chosen.get("text_path")
        raw_path = chosen.get("raw_path")
        links_path = chosen.get("links_path")
        chars = int(chosen.get("chars") or 0)
        if chars >= min_chars:
            status = "done"
            needs_followup = False
        else:
            status = "partial"
            needs_followup = True
        # Copy chosen text to top-level text.md for stable downstream reference.
        try:
            if isinstance(text_path, str) and text_path:
                chosen_text = Path(text_path).read_text(encoding="utf-8", errors="ignore")
                _atomic_write_text(out_dir / "text.md", chosen_text if chosen_text.endswith("\n") else (chosen_text + "\n"))
                text_path = str(out_dir / "text.md")
        except Exception:
            pass
    else:
        # detect blocked if any attempt said so
        reasons = [str(a.get("reason") or "") for a in attempts]
        if any(r in ("blocked_challenge", "login_required", "paywalled") for r in reasons):
            status = "blocked"
        else:
            status = "failed"
        needs_followup = True

    manifest_payload: Dict[str, Any] = {
        "schema_version": "1",
        "url": url,
        "final_url": final_url,
        "topic_id": topic_id,
        "pack_id": pack_id,
        "status": status,
        "choose_reason": choose_reason,
        "min_chars": min_chars,
        "allow_paid": allow_paid,
        "fetchers": fetchers,
        "fetched_at": _now_iso(),
        "attempts": attempts,
        "paths": {
            "out_dir": str(out_dir),
            "text_path": text_path,
            "raw_path": raw_path,
            "links_path": links_path,
        },
        "meta": meta or {},
    }

    manifest_path = _write_manifest(out_dir, payload=manifest_payload)

    structured = {
        "url": url,
        "final_url": final_url,
        "topic_id": topic_id,
        "pack_id": pack_id,
        "status": status,
        "fetcher_used": fetcher_used,
        "out_dir": str(out_dir),
        "manifest_path": manifest_path,
        "text_path": text_path,
        "raw_path": raw_path,
        "links_path": links_path,
        "chars": chars,
        "attempts": attempts,
        "needs_followup": needs_followup,
        "meta": meta or {},
    }
    _send_result(request_id, _call_result(text=f"source_pack_fetch {status} pack_id={pack_id}", structured=structured))


def handle_tools_call(request_id: RequestId, params: Dict[str, Any]) -> None:
    try:
        name, args = _parse_call_params(params)
        if name == "source_pack_fetch":
            handle_source_pack_fetch(request_id, args)
            return
        _send_error(request_id, -32601, f"unknown tool: {name}")
    except ValueError as e:
        _send_error(request_id, -32602, str(e))
    except Exception as e:
        _send_error(request_id, -32603, f"internal_error: {e}")


def main() -> int:
    for raw in sys.stdin:
        line = raw.strip()
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
        params = _as_object(msg.get("params"))

        if method == "initialize":
            handle_initialize(request_id, params)
        elif method == "tools/list":
            handle_tools_list(request_id, params)
        elif method == "tools/call":
            handle_tools_call(request_id, params)
        else:
            _send_error(request_id, -32601, f"unknown method: {method}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
