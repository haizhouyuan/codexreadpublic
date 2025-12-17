#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import shutil
from pathlib import Path


def replace_placeholder(path: Path, topic_id: str) -> None:
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace("<topic_id>", topic_id), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a new topic archive from templates.")
    parser.add_argument("topic_id", help="Stable slug, e.g. space_industry")
    parser.add_argument(
        "--dest-root",
        default="archives/topics",
        help="Destination root (default: archives/topics)",
    )
    parser.add_argument(
        "--templates-dir",
        default="templates/topic",
        help="Templates directory (default: templates/topic)",
    )
    args = parser.parse_args()

    topic_id = args.topic_id.strip()
    if not topic_id:
        raise SystemExit("topic_id is required")

    dest_root = Path(args.dest_root)
    templates_dir = Path(args.templates_dir)

    if not templates_dir.is_dir():
        raise SystemExit(f"templates dir not found: {templates_dir}")

    topic_dir = dest_root / topic_id
    if topic_dir.exists():
        raise SystemExit(f"topic already exists: {topic_dir}")

    (topic_dir / "digests").mkdir(parents=True, exist_ok=False)
    (topic_dir / "notes").mkdir(parents=True, exist_ok=False)

    template_files = ["AGENTS.md", "overview.md", "framework.md", "timeline.md", "sources.md", "open_questions.md"]
    # Optional (investing layer) — included when the template exists.
    if (templates_dir / "investing.md").exists():
        template_files.append("investing.md")

    for template_name in template_files:
        src = templates_dir / template_name
        dst = topic_dir / template_name
        shutil.copyfile(src, dst)
        replace_placeholder(dst, topic_id)

    triage_src = templates_dir / "triage_policy.md"
    if triage_src.exists():
        triage_dst = topic_dir / "notes" / "triage_policy.md"
        shutil.copyfile(triage_src, triage_dst)
        replace_placeholder(triage_dst, topic_id)

    print(str(topic_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
