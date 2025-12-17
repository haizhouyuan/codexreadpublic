#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional


REPO_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class DigestMeta:
    path: Path
    title: str
    source_url: str
    published_at: str


def _parse_frontmatter(md_path: Path) -> Dict[str, str]:
    text = md_path.read_text(encoding="utf-8", errors="replace")
    if not text.startswith("---"):
        return {}
    parts = text.split("\n---\n", 1)
    if len(parts) != 2:
        return {}
    fm = parts[0].splitlines()[1:]
    out: Dict[str, str] = {}
    for ln in fm:
        m = re.match(r"^([A-Za-z0-9_]+):\s*(.*)\s*$", ln)
        if not m:
            continue
        k, v = m.group(1), m.group(2)
        out[k] = v
    return out


def _parse_jsonish_string(v: str) -> str:
    v = v.strip()
    if not v:
        return ""
    # Our digests write title/url/path with json.dumps, so try json.loads first.
    try:
        return str(json.loads(v))
    except Exception:
        return v.strip('"').strip("'")


def main() -> int:
    ap = argparse.ArgumentParser(description="Rebuild topic sources.md from digests frontmatter (de-dupe).")
    ap.add_argument("topic_id", help="Topic id under archives/topics/")
    args = ap.parse_args()

    topic_id = args.topic_id.strip()
    topic_dir = REPO_ROOT / "archives" / "topics" / topic_id
    digests_dir = topic_dir / "digests"
    sources_md = topic_dir / "sources.md"
    if not digests_dir.is_dir():
        raise SystemExit(f"digests dir not found: {digests_dir}")

    metas: List[DigestMeta] = []
    for md in sorted(digests_dir.glob("*.md")):
        fm = _parse_frontmatter(md)
        title = _parse_jsonish_string(fm.get("title", ""))
        source_url = _parse_jsonish_string(fm.get("source_url", ""))
        published_at = _parse_jsonish_string(fm.get("published_at", ""))
        metas.append(DigestMeta(path=md, title=title or md.stem, source_url=source_url, published_at=published_at))

    # Sort by date desc, fallback by filename.
    metas.sort(key=lambda m: (m.published_at or "", m.path.name), reverse=True)

    rows: List[str] = []
    seen: set[str] = set()
    for m in metas:
        digest_rel = f"digests/{m.path.name}"
        key = f"{m.source_url}::{digest_rel}"
        if key in seen:
            continue
        seen.add(key)
        rows.append(f"| {m.published_at} | video | {m.title.replace('|', ' ')} | {m.source_url} | {digest_rel} |")

    header = [
        f"# {topic_id} — 资料清单",
        "",
        "> 记录已纳入主题档案的资料，并指向对应 digest。",
        "",
        "| 日期 | 类型 | 标题 | 链接/路径 | Digest |",
        "|---|---|---|---|---|",
        f"|  | UP | 投研先机（mid=414609825）主页 | https://space.bilibili.com/414609825/video |  |",
    ]
    text = "\n".join(header + rows).rstrip() + "\n"
    sources_md.write_text(text, encoding="utf-8")
    print(str(sources_md))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
