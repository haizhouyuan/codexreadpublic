#!/usr/bin/env python3
import argparse
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests


URL_RE = re.compile(r"https?://[^\s\)\]\}<>\"']+")


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


def _get_env(env: dict[str, str], *keys: str) -> str | None:
    for key in keys:
        value = os.getenv(key) or env.get(key)
        if value:
            return value
    return None


def _extract_urls(text: str) -> list[str]:
    urls = []
    for m in URL_RE.finditer(text or ""):
        urls.append(m.group(0).rstrip(".,;:"))
    seen: set[str] = set()
    uniq: list[str] = []
    for u in urls:
        if u in seen:
            continue
        seen.add(u)
        uniq.append(u)
    return uniq


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


@dataclass
class ProviderResult:
    provider: str
    model: str | None
    ok: bool
    elapsed_seconds: float
    error: str | None
    answer_preview: str | None
    urls: list[str]
    raw: Any


def _dashscope_chat(
    api_key: str,
    model: str,
    query: str,
    timeout_seconds: int,
    want_json: bool,
) -> ProviderResult:
    url = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
    system = (
        "你是一个检索助手。你必须使用联网搜索能力。"
        "输出时请优先给出可核验的一手来源（官方/标准组织/公司公告/技术白皮书），并提供完整 URL。"
    )
    if want_json:
        user = (
            "请联网搜索并返回严格 JSON（不要代码块、不要多余文本）。\n"
            "JSON schema:\n"
            '{ "answer": string, "sources": [ { "title": string, "url": string } ], "notes": string }\n'
            f"问题：{query}"
        )
    else:
        user = query

    payload: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "enable_search": True,
        "temperature": 0.2,
        "max_tokens": 900,
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    started = time.time()
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=timeout_seconds)
        elapsed = time.time() - started
    except Exception as e:
        return ProviderResult(
            provider="dashscope",
            model=model,
            ok=False,
            elapsed_seconds=time.time() - started,
            error=f"request_error: {e}",
            answer_preview=None,
            urls=[],
            raw=None,
        )

    raw_text = resp.text
    if resp.status_code >= 400:
        return ProviderResult(
            provider="dashscope",
            model=model,
            ok=False,
            elapsed_seconds=elapsed,
            error=f"http_{resp.status_code}: {raw_text[:500]}",
            answer_preview=None,
            urls=[],
            raw=None,
        )

    data = resp.json()
    content = (((data.get("choices") or [{}])[0].get("message") or {}).get("content")) or ""
    parsed = _try_parse_json(content) if want_json else None
    urls: list[str] = []
    if isinstance(parsed, dict) and isinstance(parsed.get("sources"), list):
        for s in parsed["sources"]:
            if isinstance(s, dict) and s.get("url"):
                urls.append(str(s["url"]))
    if not urls:
        urls = _extract_urls(content)

    preview = (content or "").strip().replace("\n", " ")
    if len(preview) > 400:
        preview = preview[:400] + "…"

    return ProviderResult(
        provider="dashscope",
        model=model,
        ok=True,
        elapsed_seconds=elapsed,
        error=None,
        answer_preview=preview,
        urls=urls,
        raw=data,
    )


def _bigmodel_web_search(
    api_key: str,
    engine: str,
    query: str,
    timeout_seconds: int,
    count: int,
    *,
    recency: str = "noLimit",
    content_size: str = "medium",
    domain_filter: str | None = None,
) -> ProviderResult:
    url = "https://open.bigmodel.cn/api/paas/v4/web_search"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload: dict[str, Any] = {
        "search_engine": engine,
        "search_query": query,
        "count": count,
        "search_intent": False,
        "search_recency_filter": recency,
        "content_size": content_size,
    }
    if domain_filter:
        payload["search_domain_filter"] = domain_filter

    started = time.time()
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=timeout_seconds)
        elapsed = time.time() - started
    except Exception as e:
        return ProviderResult(
            provider="bigmodel_web_search",
            model=engine,
            ok=False,
            elapsed_seconds=time.time() - started,
            error=f"request_error: {e}",
            answer_preview=None,
            urls=[],
            raw=None,
        )

    if resp.status_code >= 400:
        return ProviderResult(
            provider="bigmodel_web_search",
            model=engine,
            ok=False,
            elapsed_seconds=elapsed,
            error=f"http_{resp.status_code}: {resp.text[:500]}",
            answer_preview=None,
            urls=[],
            raw=None,
        )

    data = resp.json()
    results = data.get("search_result") or []
    urls = []
    for r in results:
        if isinstance(r, dict) and r.get("link"):
            urls.append(str(r["link"]))
    preview = ""
    if results and isinstance(results[0], dict):
        preview = (results[0].get("title") or "").strip()
        if results[0].get("link"):
            preview = (preview + " — " + str(results[0]["link"])).strip()
    preview = preview.replace("\n", " ")
    if len(preview) > 400:
        preview = preview[:400] + "…"
    return ProviderResult(
        provider="bigmodel_web_search",
        model=engine,
        ok=True,
        elapsed_seconds=elapsed,
        error=None,
        answer_preview=preview or None,
        urls=urls,
        raw=data,
    )


def _tavily_search(api_key: str, query: str, timeout_seconds: int, max_results: int) -> ProviderResult:
    url = "https://api.tavily.com/search"
    payload: dict[str, Any] = {
        "api_key": api_key,
        "query": query,
        "search_depth": "advanced",
        "max_results": max_results,
        "include_answer": True,
        "include_raw_content": False,
    }
    started = time.time()
    try:
        resp = requests.post(url, json=payload, timeout=timeout_seconds)
        elapsed = time.time() - started
    except Exception as e:
        return ProviderResult(
            provider="tavily",
            model=None,
            ok=False,
            elapsed_seconds=time.time() - started,
            error=f"request_error: {e}",
            answer_preview=None,
            urls=[],
            raw=None,
        )
    if resp.status_code >= 400:
        return ProviderResult(
            provider="tavily",
            model=None,
            ok=False,
            elapsed_seconds=elapsed,
            error=f"http_{resp.status_code}: {resp.text[:500]}",
            answer_preview=None,
            urls=[],
            raw=None,
        )
    data = resp.json()
    urls = []
    for r in data.get("results") or []:
        if isinstance(r, dict) and r.get("url"):
            urls.append(str(r["url"]))
    preview = (data.get("answer") or "").strip().replace("\n", " ")
    if len(preview) > 400:
        preview = preview[:400] + "…"
    return ProviderResult(
        provider="tavily",
        model=None,
        ok=True,
        elapsed_seconds=elapsed,
        error=None,
        answer_preview=preview or None,
        urls=urls,
        raw=data,
    )


def _brave_search(api_key: str, query: str, timeout_seconds: int, count: int) -> ProviderResult:
    base = "https://api.search.brave.com/res/v1/web/search"
    # Brave expects a fixed enum for search_lang (e.g. "en", "zh-hans", "zh-hant").
    # Default to "en" to avoid 422 validation errors across installations.
    params = {"q": query, "count": str(count), "search_lang": "en"}
    url = f"{base}?{urlencode(params)}"
    headers = {"X-Subscription-Token": api_key, "Accept": "application/json"}
    started = time.time()
    try:
        resp = requests.get(url, headers=headers, timeout=timeout_seconds)
        elapsed = time.time() - started
    except Exception as e:
        return ProviderResult(
            provider="brave",
            model=None,
            ok=False,
            elapsed_seconds=time.time() - started,
            error=f"request_error: {e}",
            answer_preview=None,
            urls=[],
            raw=None,
        )
    if resp.status_code >= 400:
        return ProviderResult(
            provider="brave",
            model=None,
            ok=False,
            elapsed_seconds=elapsed,
            error=f"http_{resp.status_code}: {resp.text[:500]}",
            answer_preview=None,
            urls=[],
            raw=None,
        )
    data = resp.json()
    urls = []
    for r in (((data.get("web") or {}).get("results")) or []):
        if isinstance(r, dict) and r.get("url"):
            urls.append(str(r["url"]))
    preview = ""
    top = (((data.get("web") or {}).get("results")) or [])[:1]
    if top and isinstance(top[0], dict):
        preview = (top[0].get("title") or "") + " — " + (top[0].get("description") or "")
    preview = preview.strip().replace("\n", " ")
    if len(preview) > 400:
        preview = preview[:400] + "…"
    return ProviderResult(
        provider="brave",
        model=None,
        ok=True,
        elapsed_seconds=elapsed,
        error=None,
        answer_preview=preview or None,
        urls=urls,
        raw=data,
    )


def _tongxiao_iqs_search(api_key: str, query: str, timeout_seconds: int, num_results: int) -> ProviderResult:
    url = "https://cloud-iqs.aliyuncs.com/search/llm"
    headers = {"X-API-Key": api_key, "Content-Type": "application/json"}
    payload = {"query": query, "numResults": num_results}
    started = time.time()
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=timeout_seconds)
        elapsed = time.time() - started
    except Exception as e:
        return ProviderResult(
            provider="tongxiao_iqs",
            model=None,
            ok=False,
            elapsed_seconds=time.time() - started,
            error=f"request_error: {e}",
            answer_preview=None,
            urls=[],
            raw=None,
        )
    if resp.status_code >= 400:
        return ProviderResult(
            provider="tongxiao_iqs",
            model=None,
            ok=False,
            elapsed_seconds=elapsed,
            error=f"http_{resp.status_code}: {resp.text[:500]}",
            answer_preview=None,
            urls=[],
            raw=None,
        )
    data = resp.json()
    urls = []
    items = data.get("pageItems") or []
    for item in items:
        if isinstance(item, dict) and item.get("link"):
            urls.append(str(item["link"]))
    preview = ""
    if items and isinstance(items[0], dict):
        preview = (items[0].get("title") or "") + " — " + (items[0].get("summary") or items[0].get("snippet") or "")
    preview = preview.strip().replace("\n", " ")
    if len(preview) > 400:
        preview = preview[:400] + "…"
    return ProviderResult(
        provider="tongxiao_iqs",
        model=None,
        ok=True,
        elapsed_seconds=elapsed,
        error=None,
        answer_preview=preview or None,
        urls=urls,
        raw=data,
    )


DEFAULT_QUERIES: list[dict[str, str]] = [
    {
        "id": "ashrae_tc99_water_cooled_servers",
        "query": "ASHRAE TC 9.9 water-cooled servers whitepaper liquid cooling data center PDF",
    },
    {
        "id": "ocp_advanced_cooling_solutions_cold_plate",
        "query": "Open Compute Project Advanced Cooling Solutions cold plate specification",
    },
    {
        "id": "schneider_wp282_immersion_vs_air_capex",
        "query": "Schneider Electric WP282 immersion cooling vs air cooling capex",
    },
]


def _write_markdown_summary(path: Path, rows: list[dict[str, Any]]) -> None:
    lines: list[str] = []
    lines.append("# websearch benchmark")
    lines.append("")
    lines.append(f"- generated_at: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("")
    for row in rows:
        lines.append(f"## {row['query_id']}")
        lines.append("")
        lines.append(f"- query: {row['query']}")
        lines.append("")
        for r in row["results"]:
            tag = f"{r['provider']}" + (f"/{r['model']}" if r.get("model") else "")
            if r["ok"]:
                lines.append(f"- {tag}: ok ({r['elapsed_seconds']:.2f}s), urls={len(r['urls'])}")
                if r.get("answer_preview"):
                    lines.append(f"  - preview: {r['answer_preview']}")
                for u in r["urls"][:8]:
                    lines.append(f"  - {u}")
            else:
                lines.append(f"- {tag}: FAIL ({r['elapsed_seconds']:.2f}s) {r.get('error','')}")
        lines.append("")
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark multiple web search backends with real project queries.")
    parser.add_argument("--out-dir", default="state/tmp/websearch_benchmark", help="Output directory (default: state/tmp/...)")
    parser.add_argument("--dashscope-models", default="qwen-turbo,qwen3-max", help="Comma-separated models for DashScope (enable_search)")
    parser.add_argument("--max-results", type=int, default=5, help="Max results for Tavily/Brave (default: 5)")
    parser.add_argument("--timeout", type=int, default=45, help="Per-request timeout seconds (default: 45)")
    parser.add_argument("--no-dashscope", action="store_true", help="Skip DashScope enable_search tests")
    parser.add_argument(
        "--no-bigmodel",
        action="store_true",
        help="Skip BigModel Web Search (search_std/search_pro/search_pro_sogou/search_pro_quark) tests",
    )
    parser.add_argument(
        "--bigmodel-engines",
        default="search_std,search_pro,search_pro_sogou,search_pro_quark",
        help="Comma-separated BigModel search_engine values",
    )
    parser.add_argument(
        "--bigmodel-recency",
        default="noLimit",
        help="BigModel search_recency_filter: oneDay|oneWeek|oneMonth|oneYear|noLimit",
    )
    parser.add_argument(
        "--bigmodel-content-size",
        default="medium",
        help="BigModel content_size: medium|high",
    )
    parser.add_argument("--no-tavily", action="store_true", help="Skip Tavily tests")
    parser.add_argument("--no-brave", action="store_true", help="Skip Brave tests")
    parser.add_argument("--no-tongxiao", action="store_true", help="Skip Tongxiao IQS (Quark) tests")
    parser.add_argument("--queries-json", default=None, help="Optional JSON file with [{id, query}]")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    env = _load_dotenv(repo_root / ".env")

    dashscope_key = _get_env(env, "DASHSCOPE_API_KEY", "WEBSEARCH_API_KEY")
    bigmodel_key = _get_env(env, "BIGMODEL_API_KEY")
    tavily_key = _get_env(env, "TAVILY_API_KEY", "tavilyApiKey")
    brave_key = _get_env(env, "BRAVE_API_KEY", "braveapikey")
    tongxiao_key = _get_env(env, "TONGXIAO_API_KEY")

    out_dir = (repo_root / args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    out_json = out_dir / f"benchmark_{timestamp}.json"
    out_md = out_dir / f"benchmark_{timestamp}.md"

    queries = DEFAULT_QUERIES
    if args.queries_json:
        queries = json.loads(Path(args.queries_json).read_text(encoding="utf-8"))

    dashscope_models = [m.strip() for m in str(args.dashscope_models).split(",") if m.strip()]
    bigmodel_engines = [e.strip() for e in str(args.bigmodel_engines).split(",") if e.strip()]

    rows: list[dict[str, Any]] = []
    for q in queries:
        qid = q.get("id") or "query"
        query = q.get("query") or ""
        results: list[dict[str, Any]] = []

        if not args.no_dashscope:
            if not dashscope_key:
                results.append(
                    ProviderResult(
                        provider="dashscope",
                        model=",".join(dashscope_models) or None,
                        ok=False,
                        elapsed_seconds=0.0,
                        error="missing DASHSCOPE_API_KEY/WEBSEARCH_API_KEY",
                        answer_preview=None,
                        urls=[],
                        raw=None,
                    ).__dict__
                )
            else:
                for model in dashscope_models:
                    r = _dashscope_chat(
                        api_key=dashscope_key,
                        model=model,
                        query=query,
                        timeout_seconds=args.timeout,
                        want_json=True,
                    )
                    results.append(r.__dict__)

        if not args.no_bigmodel:
            if not bigmodel_key:
                results.append(
                    ProviderResult(
                        provider="bigmodel_web_search",
                        model=",".join(bigmodel_engines) or None,
                        ok=False,
                        elapsed_seconds=0.0,
                        error="missing BIGMODEL_API_KEY",
                        answer_preview=None,
                        urls=[],
                        raw=None,
                    ).__dict__
                )
            else:
                for engine in bigmodel_engines:
                    r = _bigmodel_web_search(
                        api_key=bigmodel_key,
                        engine=engine,
                        query=query,
                        timeout_seconds=args.timeout,
                        count=args.max_results,
                        recency=str(args.bigmodel_recency),
                        content_size=str(args.bigmodel_content_size),
                    )
                    results.append(r.__dict__)

        if not args.no_tavily:
            if not tavily_key:
                results.append(
                    ProviderResult(
                        provider="tavily",
                        model=None,
                        ok=False,
                        elapsed_seconds=0.0,
                        error="missing TAVILY_API_KEY/tavilyApiKey",
                        answer_preview=None,
                        urls=[],
                        raw=None,
                    ).__dict__
                )
            else:
                r = _tavily_search(api_key=tavily_key, query=query, timeout_seconds=args.timeout, max_results=args.max_results)
                results.append(r.__dict__)

        if not args.no_brave:
            if not brave_key:
                results.append(
                    ProviderResult(
                        provider="brave",
                        model=None,
                        ok=False,
                        elapsed_seconds=0.0,
                        error="missing BRAVE_API_KEY/braveapikey",
                        answer_preview=None,
                        urls=[],
                        raw=None,
                    ).__dict__
                )
            else:
                r = _brave_search(api_key=brave_key, query=query, timeout_seconds=args.timeout, count=args.max_results)
                results.append(r.__dict__)

        if not args.no_tongxiao:
            if not tongxiao_key:
                results.append(
                    ProviderResult(
                        provider="tongxiao_iqs",
                        model=None,
                        ok=False,
                        elapsed_seconds=0.0,
                        error="missing TONGXIAO_API_KEY",
                        answer_preview=None,
                        urls=[],
                        raw=None,
                    ).__dict__
                )
            else:
                r = _tongxiao_iqs_search(
                    api_key=tongxiao_key,
                    query=query,
                    timeout_seconds=args.timeout,
                    num_results=args.max_results,
                )
                results.append(r.__dict__)

        rows.append({"query_id": qid, "query": query, "results": results})

    out_json.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_markdown_summary(out_md, rows)

    print(f"OK: wrote {out_json}")
    print(f"OK: wrote {out_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
