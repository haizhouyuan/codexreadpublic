#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import html as html_lib
import json
import os
import re
import sys
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

import requests


USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0 Safari/537.36"


@dataclass
class SogouArticle:
    title_raw: str
    title: str
    summary: str
    account: str
    ts: int
    page: int
    sogou_link: str


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


def _strip_tags(s: str) -> str:
    return re.sub(r"<.*?>", "", s or "").replace("\n", " ").strip()


def _safe_filename(stem: str, *, max_len: int = 80) -> str:
    s = re.sub(r"[\s\t\r\n]+", " ", stem).strip()
    s = html_lib.unescape(s)
    # Keep Chinese + ASCII alnum; map others to underscore.
    s = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "_", s).strip("_")
    if not s:
        s = "article"
    if len(s) > max_len:
        s = s[:max_len].rstrip("_")
    return s


def _fetch_html(session: requests.Session, url: str, *, referer: str | None, timeout: int) -> str:
    headers = {"User-Agent": USER_AGENT}
    if referer:
        headers["Referer"] = referer
    r = session.get(url, headers=headers, timeout=timeout)
    r.raise_for_status()
    return r.text


def _parse_sogou_search_page(html: str, *, page: int) -> list[SogouArticle]:
    items: list[SogouArticle] = []
    blocks = re.split(r'<li id="sogou_vr_11002601_box_\d+"', html)
    for block in blocks[1:]:
        end = block.find("</li>")
        part = block[:end] if end != -1 else block

        title_m = re.search(r'uigs="article_title_\d+">(.*?)</a>', part, re.S)
        title_raw = _strip_tags(title_m.group(1)) if title_m else ""
        title = html_lib.unescape(title_raw)

        summary_m = re.search(r'id="sogou_vr_11002601_summary_\d+">(.*?)</p>', part, re.S)
        summary = html_lib.unescape(_strip_tags(summary_m.group(1))) if summary_m else ""

        account_m = re.search(r'<span class="all-time-y2">(.*?)</span>', part, re.S)
        account = _strip_tags(account_m.group(1)) if account_m else ""

        ts_m = re.search(r"timeConvert\('?(?P<ts>\d+)'?\)", part)
        ts = int(ts_m.group(1)) if ts_m else 0

        link_m = re.search(r'href="(?P<link>/link\?url=[^"]+)"', part)
        sogou_link = ""
        if link_m:
            sogou_link = "https://weixin.sogou.com" + link_m.group("link").replace("&amp;", "&")

        if title and account and ts and sogou_link:
            items.append(
                SogouArticle(
                    title_raw=title_raw,
                    title=title,
                    summary=summary,
                    account=account,
                    ts=ts,
                    page=page,
                    sogou_link=sogou_link,
                )
            )
    return items


def _resolve_sogou_link_to_mp_url(session: requests.Session, *, search_url: str, sogou_link: str, timeout: int) -> str:
    headers = {"User-Agent": USER_AGENT, "Referer": search_url}
    r = session.get(sogou_link, headers=headers, timeout=timeout)
    r.raise_for_status()
    html = r.text or ""

    lowered = html.lower()
    if "antispider" in lowered or "请输入验证码" in html:
        raise RuntimeError("sogou /link blocked by antispider (captcha required)")

    # /link often returns a tiny JS snippet that assembles the final URL:
    #   var url = '';
    #   url += 'https://mp.'; url += 'weixin.qq.c'; ...
    #   window.location.replace(url)
    parts = [m.group(2) for m in re.finditer(r"url\s*\+=\s*(['\"])([^'\"]*)\1", html)]
    if not parts:
        parts = [m.group(2) for m in re.finditer(r"url\s*=\s*url\s*\+\s*(['\"])([^'\"]*)\1", html)]
    if parts:
        candidate = "".join(parts).replace("@", "")
        if candidate.startswith("http"):
            return candidate

    # Sometimes it's a direct window.location.replace('https://...')
    m = re.search(r"window\.location\.replace\(\s*(['\"])(https?://[^'\"]+)\1\s*\)", html)
    if m:
        return m.group(2)

    # Fallback: meta refresh.
    m = re.search(
        r'http-equiv=["\']refresh["\'][^>]*content=["\'][^;]+;\s*url=(https?://[^"\']+)',
        html,
        re.I,
    )
    if m:
        return m.group(1)

    raise RuntimeError("could not resolve sogou /link to mp.weixin url")


def _bigmodel_reader(api_key: str, url: str, *, timeout: int) -> dict:
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
        raise RuntimeError(f"bigmodel reader http_{r.status_code}: {r.text[:300]}")
    return r.json()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fetch latest WeChat public-account articles via Sogou (discovery) + BigModel reader (content)."
    )
    parser.add_argument("--account", required=True, help="WeChat account name as shown on Sogou results (e.g. capitalwatch)")
    parser.add_argument("--top", type=int, default=3, help="How many latest articles to fetch (default: 3)")
    parser.add_argument("--pages", type=int, default=4, help="How many Sogou pages to scan (default: 4)")
    parser.add_argument(
        "--out-dir",
        default=None,
        help="Output directory (default: imports/content/wechat/<account>/)",
    )
    parser.add_argument("--timeout", type=int, default=30, help="HTTP timeout seconds (default: 30)")
    parser.add_argument("--sleep", type=float, default=0.6, help="Sleep seconds between requests (default: 0.6)")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    env = _load_dotenv(repo_root / ".env")
    api_key = _get_env(env, "BIGMODEL_API_KEY")
    if not api_key:
        print("ERROR: missing BIGMODEL_API_KEY (required for BigModel reader).", file=sys.stderr)
        return 2

    account = str(args.account).strip()
    if not account:
        print("ERROR: --account is required", file=sys.stderr)
        return 2

    out_dir = Path(args.out_dir) if args.out_dir else (repo_root / "imports" / "content" / "wechat" / account)
    out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    session = requests.Session()
    items: list[SogouArticle] = []

    for page in range(1, max(1, int(args.pages)) + 1):
        search_url = "https://weixin.sogou.com/weixin?type=2&query=" + urllib.parse.quote(account) + f"&page={page}"
        try:
            html = _fetch_html(session, search_url, referer=None, timeout=int(args.timeout))
        except Exception as e:
            print(f"WARN: search fetch failed page={page}: {e}", file=sys.stderr)
            break
        if "antispider" in html.lower() or "请输入验证码" in html:
            print(f"BLOCKED: Sogou antispider triggered at search page={page}.", file=sys.stderr)
            break
        items.extend(_parse_sogou_search_page(html, page=page))
        time.sleep(max(0.0, float(args.sleep)))

    items = [it for it in items if it.account.lower() == account.lower()]
    items.sort(key=lambda x: x.ts, reverse=True)
    if not items:
        print(f"NO_RESULTS: account={account} pages_scanned={args.pages}", file=sys.stderr)
        return 1

    top_n = items[: max(1, int(args.top))]
    fetched: list[dict[str, object]] = []
    ts_tag = dt.datetime.now().strftime("%Y%m%d_%H%M%S")

    for idx, it in enumerate(top_n, start=1):
        published_dt = dt.datetime.fromtimestamp(it.ts)
        published_day = published_dt.strftime("%Y-%m-%d")
        search_url = "https://weixin.sogou.com/weixin?type=2&query=" + urllib.parse.quote(account) + f"&page={it.page}"
        try:
            mp_url = _resolve_sogou_link_to_mp_url(
                session,
                search_url=search_url,
                sogou_link=it.sogou_link,
                timeout=int(args.timeout),
            )
        except Exception as e:
            fetched.append(
                {
                    "ok": False,
                    "title": it.title,
                    "published_day": published_day,
                    "sogou_link": it.sogou_link,
                    "error": str(e),
                }
            )
            continue

        time.sleep(max(0.0, float(args.sleep)))

        try:
            reader = _bigmodel_reader(api_key, mp_url, timeout=int(args.timeout))
            rr = reader.get("reader_result") or {}
            title = str(rr.get("title") or it.title).strip()
            content = str(rr.get("content") or "").strip()
            description = str(rr.get("description") or "").strip()
        except Exception as e:
            fetched.append(
                {
                    "ok": False,
                    "title": it.title,
                    "published_day": published_day,
                    "sogou_link": it.sogou_link,
                    "mp_url": mp_url,
                    "error": str(e),
                }
            )
            continue

        safe = _safe_filename(title)
        out_path = out_dir / f"{published_day}_{idx:02d}_{safe}.md"
        frontmatter = {
            "title": title,
            "source": mp_url,
            "account": account,
            "published_at": published_dt.replace(microsecond=0).isoformat(),
            "sogou_link": it.sogou_link,
            "fetched_at": dt.datetime.now().replace(microsecond=0).isoformat(),
        }
        body_lines = ["---", json.dumps(frontmatter, ensure_ascii=False, indent=2), "---", ""]
        if description:
            body_lines.append(f"> {description}")
            body_lines.append("")
        body_lines.append(content or "(empty content)")
        out_path.write_text("\n".join(body_lines).rstrip() + "\n", encoding="utf-8")

        fetched.append(
            {
                "ok": True,
                "title": title,
                "published_day": published_day,
                "sogou_link": it.sogou_link,
                "mp_url": mp_url,
                "output_path": str(out_path),
            }
        )
        time.sleep(max(0.0, float(args.sleep)))

    index_path = out_dir / f"latest_{ts_tag}.md"
    index_lines = [f"# WeChat latest fetch ({account})", ""]
    index_lines.append(f"- generated_at: {dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    index_lines.append(f"- account: {account}")
    index_lines.append("")
    for row in fetched:
        if row.get("ok"):
            index_lines.append(f"- ✅ {row['published_day']} {row['title']}")
            index_lines.append(f"  - mp_url: {row['mp_url']}")
            index_lines.append(f"  - file: {row['output_path']}")
        else:
            index_lines.append(f"- ❌ {row.get('published_day','')} {row.get('title','')}")
            index_lines.append(f"  - error: {row.get('error','')}")
            if row.get("sogou_link"):
                index_lines.append(f"  - sogou_link: {row['sogou_link']}")
            if row.get("mp_url"):
                index_lines.append(f"  - mp_url: {row['mp_url']}")
    index_path.write_text("\n".join(index_lines).rstrip() + "\n", encoding="utf-8")

    print(f"OK: wrote {index_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
