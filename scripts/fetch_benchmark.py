#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import io
import json
import os
import re
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

import requests
try:
    from bs4 import BeautifulSoup  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    BeautifulSoup = None  # type: ignore[assignment]


USER_AGENT = "codexread/0.1 (+https://github.com/haizhouyuan/codexread)"


def _load_dotenv(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    env: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
            value = value[1:-1]
        env[key] = value
    return env


def _get_env_any(env: dict[str, str], *keys: str) -> str | None:
    for key in keys:
        value = os.getenv(key) or env.get(key)
        if value:
            return value
    return None


def _safe_slug(s: str, *, max_len: int = 80) -> str:
    s = s.strip()
    s = re.sub(r"^https?://", "", s)
    s = re.sub(r"[\s\t\r\n]+", "_", s)
    s = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff._-]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    if not s:
        s = "url"
    if len(s) > max_len:
        s = s[:max_len].rstrip("_")
    return s


def _sha256_bytes(data: bytes) -> str:
    h = hashlib.sha256()
    h.update(data)
    return h.hexdigest()


def _html_to_text(html_text: str) -> str:
    if BeautifulSoup is None:
        # Extremely minimal fallback if bs4 isn't installed.
        text = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", html_text)
        text = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", text)
        text = re.sub(r"(?is)<!--.*?-->", " ", text)
        text = re.sub(r"(?is)<[^>]+>", " ", text)
    else:
        soup = BeautifulSoup(html_text, "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        text = soup.get_text("\n")
    lines: list[str] = []
    for line in text.splitlines():
        line = line.replace("\u00a0", " ")
        line = re.sub(r"[ \t]+", " ", line).strip()
        if not line:
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def _pdf_to_text(raw: bytes) -> str:
    from pypdf import PdfReader  # type: ignore[import-not-found]

    reader = PdfReader(io.BytesIO(raw))
    parts: list[str] = []
    for page in reader.pages:
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        text = text.replace("\u00a0", " ")
        text = re.sub(r"[ \t]+", " ", text)
        text = "\n".join([line.strip() for line in text.splitlines() if line.strip()])
        if text:
            parts.append(text)
    return "\n\n".join(parts).strip()


def _jina_reader_url(url: str) -> str:
    if url.startswith("https://"):
        return "https://r.jina.ai/https://" + url[len("https://") :]
    if url.startswith("http://"):
        return "https://r.jina.ai/http://" + url[len("http://") :]
    raise ValueError("url must start with http:// or https://")


@dataclass
class Attempt:
    fetcher: str
    ok: bool
    seconds: float
    chars: int
    out_dir: str
    text_path: str | None = None
    raw_path: str | None = None
    content_type: str | None = None
    sha256: str | None = None
    error: str | None = None
    meta: dict[str, Any] | None = None


def _write_manifest(out_dir: Path, *, url: str, attempt: Attempt) -> None:
    payload = {
        "url": url,
        "fetched_at": dt.datetime.now().replace(microsecond=0).isoformat(),
        "attempt": asdict(attempt),
    }
    (out_dir / "manifest.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _fetch_local(url: str, *, out_dir: Path, timeout: int) -> Attempt:
    start = time.time()
    try:
        r = requests.get(
            url,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html,*/*"},
            timeout=timeout,
            allow_redirects=True,
        )
        content_type = r.headers.get("Content-Type")
        raw = r.content
        is_pdf = (content_type or "").lower().startswith("application/pdf") or raw[:4] == b"%PDF" or url.lower().endswith(".pdf")
        if is_pdf:
            raw_path = out_dir / "download.pdf"
            raw_path.write_bytes(raw)
            text = _pdf_to_text(raw)
        else:
            raw_path = out_dir / "raw.html"
            raw_path.write_bytes(raw)
            encoding = r.encoding or "utf-8"
            html_text = raw.decode(encoding, errors="ignore")
            text = _html_to_text(html_text)
        if not text.strip():
            raise RuntimeError("empty extraction")
        text_path = out_dir / "text.md"
        text_path.write_text(text + "\n", encoding="utf-8")
        attempt = Attempt(
            fetcher="local",
            ok=True,
            seconds=time.time() - start,
            chars=len(text),
            out_dir=str(out_dir),
            text_path=str(text_path),
            raw_path=str(raw_path),
            content_type=content_type,
            sha256=_sha256_bytes(raw),
        )
    except Exception as e:
        attempt = Attempt(
            fetcher="local",
            ok=False,
            seconds=time.time() - start,
            chars=0,
            out_dir=str(out_dir),
            error=str(e),
        )
    _write_manifest(out_dir, url=url, attempt=attempt)
    return attempt


def _fetch_jina(url: str, *, out_dir: Path, timeout: int) -> Attempt:
    start = time.time()
    reader_url = _jina_reader_url(url)
    try:
        r = requests.get(reader_url, headers={"User-Agent": USER_AGENT}, timeout=timeout)
        if r.status_code >= 400:
            raise RuntimeError(f"jina_reader http_{r.status_code}")
        text = (r.text or "").strip()
        if not text:
            raise RuntimeError("empty response")
        raw_path = out_dir / "reader.txt"
        raw_path.write_text(text + "\n", encoding="utf-8")
        text_path = out_dir / "text.md"
        text_path.write_text(text + "\n", encoding="utf-8")
        attempt = Attempt(
            fetcher="jina_reader",
            ok=True,
            seconds=time.time() - start,
            chars=len(text),
            out_dir=str(out_dir),
            text_path=str(text_path),
            raw_path=str(raw_path),
            content_type=r.headers.get("Content-Type"),
            sha256=_sha256_bytes((text + "\n").encode("utf-8")),
            meta={"reader_url": reader_url},
        )
    except Exception as e:
        attempt = Attempt(
            fetcher="jina_reader",
            ok=False,
            seconds=time.time() - start,
            chars=0,
            out_dir=str(out_dir),
            error=str(e),
            meta={"reader_url": reader_url},
        )
    _write_manifest(out_dir, url=url, attempt=attempt)
    return attempt


def _bigmodel_reader(api_key: str, url: str, *, timeout: int) -> dict[str, Any]:
    endpoint = "https://open.bigmodel.cn/api/paas/v4/reader"
    payload = {
        "url": url,
        "timeout": timeout,
        "no_cache": True,
        "return_format": "markdown",
        "with_links_summary": True,
    }
    r = requests.post(
        endpoint,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=payload,
        timeout=max(5, timeout + 30),
    )
    if r.status_code >= 400:
        raise RuntimeError(f"bigmodel_reader http_{r.status_code}")
    return r.json()  # type: ignore[no-any-return]


def _fetch_bigmodel(url: str, *, out_dir: Path, timeout: int, env: dict[str, str]) -> Attempt:
    start = time.time()
    api_key = _get_env_any(env, "BIGMODEL_API_KEY")
    if not api_key:
        attempt = Attempt(
            fetcher="bigmodel_reader",
            ok=False,
            seconds=0.0,
            chars=0,
            out_dir=str(out_dir),
            error="missing BIGMODEL_API_KEY",
        )
        _write_manifest(out_dir, url=url, attempt=attempt)
        return attempt

    try:
        data = _bigmodel_reader(api_key, url, timeout=timeout)
        raw_path = out_dir / "reader.json"
        raw_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        rr = data.get("reader_result") or {}
        content = str(rr.get("content") or "").strip()
        if not content:
            raise RuntimeError("empty reader_result.content")
        text_path = out_dir / "text.md"
        text_path.write_text(content + "\n", encoding="utf-8")
        attempt = Attempt(
            fetcher="bigmodel_reader",
            ok=True,
            seconds=time.time() - start,
            chars=len(content),
            out_dir=str(out_dir),
            text_path=str(text_path),
            raw_path=str(raw_path),
            content_type="application/json",
            sha256=_sha256_bytes(content.encode("utf-8")),
            meta={"title": rr.get("title"), "description": rr.get("description")},
        )
    except Exception as e:
        attempt = Attempt(
            fetcher="bigmodel_reader",
            ok=False,
            seconds=time.time() - start,
            chars=0,
            out_dir=str(out_dir),
            error=str(e),
        )
    _write_manifest(out_dir, url=url, attempt=attempt)
    return attempt


def _tavily_extract(api_key: str, url: str, *, timeout: int, depth: str) -> dict[str, Any]:
    endpoint = "https://api.tavily.com/extract"
    payload: dict[str, Any] = {
        "api_key": api_key,
        "urls": [url],
        "extract_depth": depth,
        "include_images": False,
    }
    r = requests.post(endpoint, json=payload, timeout=max(5, timeout + 10))
    if r.status_code >= 400:
        raise RuntimeError(f"tavily_extract http_{r.status_code}")
    return r.json()  # type: ignore[no-any-return]


def _fetch_tavily(url: str, *, out_dir: Path, timeout: int, env: dict[str, str], depth: str) -> Attempt:
    start = time.time()
    api_key = _get_env_any(env, "TAVILY_API_KEY", "tavilyApiKey")
    if not api_key:
        attempt = Attempt(
            fetcher="tavily_extract",
            ok=False,
            seconds=0.0,
            chars=0,
            out_dir=str(out_dir),
            error="missing TAVILY_API_KEY/tavilyApiKey",
        )
        _write_manifest(out_dir, url=url, attempt=attempt)
        return attempt

    try:
        data = _tavily_extract(api_key, url, timeout=timeout, depth=depth)
        raw_path = out_dir / "extract.json"
        raw_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        # Best-effort: docs may evolve; try common shapes.
        content = ""
        if isinstance(data.get("results"), list) and data["results"]:
            item = data["results"][0]
            if isinstance(item, dict):
                content = str(item.get("content") or item.get("raw_content") or item.get("text") or "").strip()
        if not content and isinstance(data.get("content"), str):
            content = data["content"].strip()
        if not content:
            raise RuntimeError("no content in tavily extract response")

        text_path = out_dir / "text.md"
        text_path.write_text(content + "\n", encoding="utf-8")
        attempt = Attempt(
            fetcher="tavily_extract",
            ok=True,
            seconds=time.time() - start,
            chars=len(content),
            out_dir=str(out_dir),
            text_path=str(text_path),
            raw_path=str(raw_path),
            content_type="application/json",
            sha256=_sha256_bytes(content.encode("utf-8")),
            meta={"extract_depth": depth},
        )
    except Exception as e:
        attempt = Attempt(
            fetcher="tavily_extract",
            ok=False,
            seconds=time.time() - start,
            chars=0,
            out_dir=str(out_dir),
            error=str(e),
            meta={"extract_depth": depth},
        )
    _write_manifest(out_dir, url=url, attempt=attempt)
    return attempt


def _default_urls(repo_root: Path) -> list[str]:
    # A small, representative set: PDF + HTML + WeChat (often blocks plain HTTP).
    urls: list[str] = [
        "https://www.ashrae.org/File%20Library/Technical%20Resources/Bookstore/WhitePaper_TC099-WaterCooledServers.pdf",
        "https://download.schneider-electric.com/files?p_Doc_Ref=SPD_WP282_EN&p_File_Name=WP282_V1_EN.pdf",
        "https://techcommunity.microsoft.com/blog/azureinfrastructureblog/liquid-cooling-in-air-cooled-data-centers-on-microsoft-azure/4268822",
    ]
    idx = repo_root / "imports" / "content" / "wechat" / "capitalwatch" / "latest_20251216_121251.md"
    if idx.exists():
        m = re.search(r"^\\s*-\\s*mp_url:\\s*(https?://\\S+)", idx.read_text(encoding="utf-8", errors="ignore"), re.M)
        if m:
            urls.append(m.group(1))
    return urls


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark multiple URL fetching/extraction approaches.")
    parser.add_argument("--url", action="append", default=[], help="URL to fetch (repeatable). If omitted, uses a small default set.")
    parser.add_argument("--timeout", type=int, default=30, help="HTTP timeout seconds (default: 30)")
    parser.add_argument(
        "--fetchers",
        default="local,jina_reader",
        help="Comma-separated fetchers: local,jina_reader,tavily_extract,bigmodel_reader",
    )
    parser.add_argument("--tavily-depth", default="basic", help="Tavily extract depth: basic|advanced (default: basic)")
    parser.add_argument(
        "--out-dir",
        default=None,
        help="Output base directory (default: state/tmp/fetch_benchmark/benchmark_<ts>/)",
    )
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    env = _load_dotenv(repo_root / ".env")
    urls = [u.strip() for u in args.url if u.strip()] or _default_urls(repo_root)

    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    base_dir = Path(args.out_dir) if args.out_dir else (repo_root / "state" / "tmp" / "fetch_benchmark" / f"benchmark_{ts}")
    base_dir.mkdir(parents=True, exist_ok=True)

    fetchers = [f.strip() for f in str(args.fetchers).split(",") if f.strip()]
    attempts: list[dict[str, Any]] = []

    for idx, url in enumerate(urls, start=1):
        slug = _safe_slug(url)
        url_dir = base_dir / f"{idx:02d}_{slug}"
        url_dir.mkdir(parents=True, exist_ok=True)

        for f in fetchers:
            out_dir = url_dir / f
            out_dir.mkdir(parents=True, exist_ok=True)
            if f == "local":
                a = _fetch_local(url, out_dir=out_dir, timeout=int(args.timeout))
            elif f == "jina_reader":
                a = _fetch_jina(url, out_dir=out_dir, timeout=int(args.timeout))
            elif f == "tavily_extract":
                a = _fetch_tavily(
                    url,
                    out_dir=out_dir,
                    timeout=int(args.timeout),
                    env=env,
                    depth=str(args.tavily_depth or "basic").strip() or "basic",
                )
            elif f == "bigmodel_reader":
                a = _fetch_bigmodel(url, out_dir=out_dir, timeout=int(args.timeout), env=env)
            else:
                a = Attempt(fetcher=f, ok=False, seconds=0.0, chars=0, out_dir=str(out_dir), error="unknown fetcher")
                _write_manifest(out_dir, url=url, attempt=a)
            attempts.append({"url": url, **asdict(a)})

    json_path = base_dir / "benchmark.json"
    json_path.write_text(json.dumps({"generated_at": ts, "attempts": attempts}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    md_lines = [f"# fetch_benchmark ({ts})", "", f"- urls: {len(urls)}", f"- fetchers: {', '.join(fetchers)}", ""]
    md_lines.append("| # | fetcher | ok | chars | seconds | text_path | error |")
    md_lines.append("|---:|---|:--:|---:|---:|---|---|")
    for i, row in enumerate(attempts, start=1):
        ok = "✅" if row.get("ok") else "❌"
        md_lines.append(
            "| {i} | {fetcher} | {ok} | {chars} | {sec:.2f} | {text_path} | {error} |".format(
                i=i,
                fetcher=row.get("fetcher", ""),
                ok=ok,
                chars=int(row.get("chars") or 0),
                sec=float(row.get("seconds") or 0.0),
                text_path=row.get("text_path") or "",
                error=(row.get("error") or "").replace("\n", " ")[:120],
            )
        )
    md_path = base_dir / "benchmark.md"
    md_path.write_text("\n".join(md_lines).rstrip() + "\n", encoding="utf-8")

    print(str(md_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
