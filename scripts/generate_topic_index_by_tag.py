#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Dict, List


REPO_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class DigestMeta:
    path: Path
    title: str
    published_at: str
    source_url: str
    tags: List[str]


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _parse_frontmatter(md_path: Path) -> Dict[str, str]:
    text = _read_text(md_path)
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


def _parse_jsonish(v: str) -> Any:
    v = (v or "").strip()
    if not v:
        return ""
    try:
        return json.loads(v)
    except Exception:
        return v.strip('"').strip("'")


def _parse_tags(v: str) -> List[str]:
    parsed = _parse_jsonish(v)
    if isinstance(parsed, list):
        return [str(t).strip() for t in parsed if str(t).strip()]
    if isinstance(parsed, str) and parsed.strip():
        return [parsed.strip()]
    return []


def _normalize_tag(tag: str) -> str:
    return str(tag or "").strip()


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(description="Generate a topic-level digest index grouped by tags.")
    ap.add_argument("topic_id", help="Topic id under archives/topics/")
    ap.add_argument(
        "--out",
        default="notes/index_by_tag.md",
        help="Output path relative to topic dir (default: notes/index_by_tag.md).",
    )
    args = ap.parse_args(argv)

    topic_id = str(args.topic_id).strip()
    if not topic_id:
        raise SystemExit("topic_id required")

    topic_dir = REPO_ROOT / "archives" / "topics" / topic_id
    digests_dir = topic_dir / "digests"
    if not digests_dir.is_dir():
        raise SystemExit(f"digests dir not found: {digests_dir}")

    out_path = topic_dir / str(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    metas: List[DigestMeta] = []
    for md in sorted(digests_dir.glob("*.md")):
        fm = _parse_frontmatter(md)
        title = _parse_jsonish(fm.get("title", "")) or md.stem
        published_at = _parse_jsonish(fm.get("published_at", "")) or ""
        source_url = _parse_jsonish(fm.get("source_url", "")) or ""
        tags = _parse_tags(fm.get("tags", ""))
        metas.append(
            DigestMeta(
                path=md,
                title=str(title),
                published_at=str(published_at),
                source_url=str(source_url),
                tags=tags,
            )
        )

    groups: Dict[str, List[DigestMeta]] = {}
    for m in metas:
        for t in m.tags:
            tag = _normalize_tag(t)
            if not tag or tag == "bilibili" or tag.startswith("bvid:"):
                continue
            groups.setdefault(tag, []).append(m)

    # Sort groups by count desc then name.
    group_items = sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    top_tags = group_items[:40]

    lines: List[str] = []
    lines.append(f"# {topic_id} — Digest Index by Tag")
    lines.append("")
    lines.append(f"- generated_at: `{now_iso()}`")
    lines.append(f"- digests_count: `{len(metas)}`")
    lines.append(f"- tags_count: `{len(groups)}`")
    lines.append("")
    lines.append("## Top Tags")
    lines.append("")
    if not top_tags:
        lines.append("- (none)")
    else:
        for tag, ms in top_tags:
            lines.append(f"- {tag} ({len(ms)})")
    lines.append("")

    for tag, ms in group_items:
        lines.append(f"## {tag} ({len(ms)})")
        lines.append("")
        ms_sorted = sorted(ms, key=lambda x: (x.published_at or "", x.path.name), reverse=True)
        for m in ms_sorted:
            rel = f"../digests/{m.path.name}"
            date = m.published_at or ""
            lines.append(f"- {date} [{m.title}]({rel})")
        lines.append("")

    out_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    print(str(out_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(__import__("sys").argv[1:]))
