#!/usr/bin/env python3
import argparse
import datetime as dt
import re
import sys
import time
import urllib.parse
from dataclasses import dataclass
from typing import Any

import requests


USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0 Safari/537.36"


@dataclass
class Article:
    title: str
    account: str
    ts: int
    sogou_link: str
    page: int


def _strip_tags(html: str) -> str:
    return re.sub(r"<.*?>", "", html or "").replace("\n", " ").strip()


def _fetch_html(url: str, timeout: int) -> str:
    r = requests.get(url, timeout=timeout, headers={"User-Agent": USER_AGENT})
    r.raise_for_status()
    return r.text


def _parse_articles(html: str, *, page: int) -> list[Article]:
    items: list[Article] = []
    # Sogou Weixin uses a fixed container id prefix on result pages.
    blocks = re.split(r'<li id="sogou_vr_11002601_box_\d+"', html)
    for block in blocks[1:]:
        end = block.find("</li>")
        part = block[:end] if end != -1 else block

        title_m = re.search(r'uigs="article_title_\d+">(.*?)</a>', part, re.S)
        title = _strip_tags(title_m.group(1)) if title_m else ""

        account_m = re.search(r'<span class="all-time-y2">(.*?)</span>', part, re.S)
        account = _strip_tags(account_m.group(1)) if account_m else ""

        ts_m = re.search(r"timeConvert\('?(?P<ts>\d+)'?\)", part)
        ts = int(ts_m.group("ts")) if ts_m else 0

        link_m = re.search(r'href="(?P<link>/link\?url=[^"]+)"', part)
        link = ""
        if link_m:
            link = "https://weixin.sogou.com" + link_m.group("link").replace("&amp;", "&")

        if title and account and ts and link:
            items.append(Article(title=title, account=account, ts=ts, sogou_link=link, page=page))
    return items


def _is_antispider_redirect(url: str, timeout: int) -> bool:
    try:
        r = requests.get(url, timeout=timeout, headers={"User-Agent": USER_AGENT}, allow_redirects=False)
    except Exception:
        return True
    loc = (r.headers.get("Location") or "").lower()
    if "antispider" in loc:
        return True
    if r.status_code in (301, 302) and "weixin.sogou.com/antispider" in loc:
        return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch latest WeChat articles via Sogou Weixin (best-effort; may hit antispider).")
    parser.add_argument("--account", required=True, help="WeChat account name as shown on Sogou results (e.g. capitalwatch)")
    parser.add_argument("--top", type=int, default=3, help="How many latest articles to print (default: 3)")
    parser.add_argument("--pages", type=int, default=3, help="How many pages to scan (default: 3)")
    parser.add_argument("--timeout", type=int, default=30, help="HTTP timeout seconds (default: 30)")
    parser.add_argument("--sleep", type=float, default=0.8, help="Sleep seconds between page fetches (default: 0.8)")
    args = parser.parse_args()

    account = str(args.account).strip()
    if not account:
        print("ERROR: --account is required", file=sys.stderr)
        return 2

    collected: list[Article] = []
    for page in range(1, max(1, int(args.pages)) + 1):
        url = "https://weixin.sogou.com/weixin?type=2&query=" + urllib.parse.quote(account) + f"&page={page}"
        try:
            html = _fetch_html(url, timeout=int(args.timeout))
        except Exception as e:
            print(f"WARN: fetch failed page={page} err={e}", file=sys.stderr)
            break
        if "antispider" in html.lower() or "请输入验证码" in html:
            print(f"BLOCKED: Sogou antispider triggered on page={page}. Use browser automation (CDP) to solve manually.", file=sys.stderr)
            break
        collected.extend(_parse_articles(html, page=page))
        time.sleep(max(0.0, float(args.sleep)))

    matched = [a for a in collected if a.account.lower() == account.lower()]
    matched.sort(key=lambda a: a.ts, reverse=True)

    if not matched:
        print(f"NO_RESULTS: account={account} pages_scanned={args.pages}", file=sys.stderr)
        return 1

    # Best-effort note: the /link redirects often go to antispider (cannot bypass).
    top_n = matched[: int(args.top)]
    print(f"# Sogou Weixin latest (best-effort)\n")
    print(f"- account: {account}")
    print(f"- pages_scanned: {min(int(args.pages), max(1, len(set(a.page for a in collected))))}")
    print(f"- matched_articles: {len(matched)}")
    print("")
    for a in top_n:
        day = dt.datetime.fromtimestamp(a.ts).strftime("%Y-%m-%d")
        blocked = _is_antispider_redirect(a.sogou_link, timeout=int(args.timeout))
        status = "blocked(antispider)" if blocked else "ok"
        print(f"- {day} [{status}] {a.title}")
        print(f"  - sogou_link: {a.sogou_link}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

