#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path


@dataclass(frozen=True)
class DigestMeta:
    title: str
    source_type: str
    source_url: str
    source_path: str
    published_at: str


def _strip_quotes(value: str) -> str:
    v = value.strip()
    if len(v) >= 2 and ((v[0] == v[-1] == '"') or (v[0] == v[-1] == "'")):
        return v[1:-1]
    return v


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---\n"):
        return {}, text
    lines = text.splitlines(keepends=False)
    if not lines or lines[0].strip() != "---":
        return {}, text

    end = None
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            end = idx
            break
    if end is None:
        return {}, text

    fm_lines = lines[1:end]
    rest = "\n".join(lines[end + 1 :]).lstrip("\n")

    fm: dict[str, str] = {}
    for raw in fm_lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = re.split(r"\s+#", value.strip(), 1)[0].strip()
        value = _strip_quotes(value)
        if value.lower() in {"null", "~"}:
            value = ""
        fm[key] = value
    return fm, rest


def _derive_date(meta: DigestMeta, digest_path: Path) -> str:
    if meta.published_at.strip():
        return meta.published_at.strip()

    m = re.match(r"^(\d{4}-\d{2}-\d{2})_", digest_path.name)
    if m:
        return m.group(1)
    return date.today().isoformat()


def _derive_title(meta: DigestMeta, digest_path: Path) -> str:
    if meta.title.strip():
        return meta.title.strip()
    stem = digest_path.stem
    stem = re.sub(r"^\d{4}-\d{2}-\d{2}_", "", stem)
    stem = stem.replace("_", " ").strip()
    return stem or digest_path.name


def _escape_table_cell(value: str) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ").strip()


def _is_table_row(line: str) -> bool:
    s = line.strip()
    return s.startswith("|") and s.endswith("|")


def _is_blank_row(line: str) -> bool:
    s = line.strip()
    if not _is_table_row(s):
        return False
    cells = [c.strip() for c in s.strip("|").split("|")]
    return all(c == "" for c in cells)

def _normalize_date_key(value: str) -> str:
    s = str(value or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return s
    if re.fullmatch(r"\d{4}-\d{2}", s):
        return s + "-01"
    if re.fullmatch(r"\d{4}", s):
        return s + "-01-01"
    return ""


def _update_sources(topic_dir: Path, *, row: list[str], digest_rel: str) -> bool:
    sources_path = topic_dir / "sources.md"
    if not sources_path.exists():
        raise FileNotFoundError(sources_path)

    lines = sources_path.read_text(encoding="utf-8").splitlines(keepends=True)

    header_idx = None
    for i, line in enumerate(lines):
        if "| 日期 |" in line and "| Digest |" in line:
            header_idx = i
            break
    if header_idx is None:
        raise RuntimeError(f"sources table header not found in {sources_path}")

    sep_idx = header_idx + 1
    if sep_idx >= len(lines) or not _is_table_row(lines[sep_idx]):
        raise RuntimeError(f"sources table separator not found in {sources_path}")

    row_start = sep_idx + 1
    row_end = row_start
    while row_end < len(lines) and _is_table_row(lines[row_end]):
        row_end += 1

    existing_rows = lines[row_start:row_end]
    if any(digest_rel in r for r in existing_rows):
        return False

    kept_rows = [r for r in existing_rows if not _is_blank_row(r)]
    new_row = "| " + " | ".join(_escape_table_cell(v) for v in row) + " |\n"
    kept_rows.append(new_row)

    # Sort newest-first by the first column (date). Keep non-standard dates at the bottom.
    def _row_sort_key(line: str) -> tuple[int, str]:
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        date_raw = cells[0] if cells else ""
        key = _normalize_date_key(date_raw)
        if not key:
            return (0, "")
        return (1, key)

    kept_rows = sorted(kept_rows, key=_row_sort_key, reverse=True)

    out = lines[:row_start] + kept_rows + lines[row_end:]
    sources_path.write_text("".join(out), encoding="utf-8")
    return True


def _update_timeline(topic_dir: Path, *, date_str: str, title: str, digest_rel: str) -> bool:
    timeline_path = topic_dir / "timeline.md"
    if not timeline_path.exists():
        raise FileNotFoundError(timeline_path)

    lines = timeline_path.read_text(encoding="utf-8").splitlines(keepends=True)
    if any(digest_rel in ln for ln in lines):
        return False

    line = f"- {date_str}：{title}（引用：`{digest_rel}`）\n"
    # Insert into the bullet list and keep timeline sorted oldest-first.
    start_idx = None
    for i, ln in enumerate(lines):
        if ln.lstrip().startswith("- "):
            start_idx = i
            break

    if start_idx is None:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] = lines[-1] + "\n"
        if lines and lines[-1].strip():
            lines.append("\n")
        lines.append(line)
        timeline_path.write_text("".join(lines), encoding="utf-8")
        return True

    prefix = lines[:start_idx]
    i = start_idx
    entries: list[str] = []
    while i < len(lines) and lines[i].lstrip().startswith("- "):
        entries.append(lines[i])
        i += 1
    suffix = lines[i:]

    entries.append(line)

    def _entry_key(ln: str) -> tuple[int, str]:
        m = re.match(r"^\s*-\s*(\d{4}-\d{2}-\d{2})", ln)
        if not m:
            return (1, "")
        return (0, m.group(1))

    entries = sorted(entries, key=_entry_key)
    out = prefix + entries + suffix
    timeline_path.write_text("".join(out), encoding="utf-8")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest a digest into a topic archive (update sources.md, optional timeline.md).")
    parser.add_argument("topic_id", help="Stable slug, e.g. space_industry")
    parser.add_argument("digest_path", help="Path to a digest markdown file")
    parser.add_argument("--topic-root", default="archives/topics", help="Topic root directory (default: archives/topics)")
    parser.add_argument("--timeline", action="store_true", help="Also append a reference entry to timeline.md")
    args = parser.parse_args()

    topic_id = str(args.topic_id).strip()
    if not topic_id:
        raise SystemExit("topic_id is required")

    digest_path = Path(str(args.digest_path)).expanduser().resolve()
    if not digest_path.exists():
        raise SystemExit(f"digest not found: {digest_path}")

    topic_dir = (Path(str(args.topic_root)) / topic_id).resolve()
    if not topic_dir.is_dir():
        raise SystemExit(f"topic not found: {topic_dir}")

    try:
        digest_rel = str(digest_path.relative_to(topic_dir)).replace("\\", "/")
    except Exception:
        raise SystemExit(f"digest must be under topic dir: {topic_dir} (got: {digest_path})")

    raw = digest_path.read_text(encoding="utf-8")
    fm, _rest = _parse_frontmatter(raw)
    meta = DigestMeta(
        title=fm.get("title", ""),
        source_type=fm.get("source_type", ""),
        source_url=fm.get("source_url", ""),
        source_path=fm.get("source_path", ""),
        published_at=fm.get("published_at", ""),
    )

    date_str = _derive_date(meta, digest_path)
    title = _derive_title(meta, digest_path)
    link = meta.source_url.strip() or meta.source_path.strip()

    changed_sources = _update_sources(
        topic_dir,
        row=[date_str, meta.source_type.strip(), title, link, f"`{digest_rel}`"],
        digest_rel=digest_rel,
    )

    changed_timeline = False
    if bool(args.timeline):
        changed_timeline = _update_timeline(topic_dir, date_str=date_str, title=title, digest_rel=digest_rel)

    if changed_sources:
        print(f"updated: {topic_dir / 'sources.md'}")
    else:
        print("sources.md: no change (already present)")

    if bool(args.timeline):
        if changed_timeline:
            print(f"updated: {topic_dir / 'timeline.md'}")
        else:
            print("timeline.md: no change (already present)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
