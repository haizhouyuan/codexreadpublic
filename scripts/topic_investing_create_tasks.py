#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from topic_investing_utils import parse_company_pool, priority_rank


REPO_ROOT = Path(__file__).resolve().parents[1]


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _die(msg: str, code: int = 2) -> None:
    print(msg, file=sys.stderr)
    raise SystemExit(code)


def _repo_rel(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except Exception:
        return str(path)


def _task_exists(conn: sqlite3.Connection, *, topic_id: str, title: str) -> bool:
    cur = conn.execute(
        """
        SELECT 1
        FROM tasks
        WHERE topic_id = ?
          AND title = ?
          AND status IN ('pending','in_progress')
        LIMIT 1
        """,
        (topic_id, title),
    )
    return cur.fetchone() is not None


def _open_task_store(tasks_db: Path):
    sys.path.insert(0, str((REPO_ROOT / "mcp-servers" / "tasks").resolve(strict=False)))
    from task_store import TaskStore  # type: ignore

    store = TaskStore(db_path=str(tasks_db))
    store.ensure_schema()
    return store


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Create investing tasks from investing.md gaps column (dedupe by title).")
    ap.add_argument("--topic-id", required=True)
    ap.add_argument("--investing-path", default="", help="Explicit investing.md path (default: archives/topics/<topic>/investing.md).")
    ap.add_argument("--tasks-db", default="state/tasks.sqlite")
    ap.add_argument("--limit", type=int, default=6)
    ap.add_argument("--source", default="auto:investing_seed")
    ap.add_argument("--tag", default="")
    ap.add_argument("--out", default="", help="Write JSON to file (optional).")
    args = ap.parse_args(argv)

    topic_id = str(args.topic_id).strip()
    if not topic_id:
        _die("topic_id is required")

    investing_raw = str(args.investing_path).strip()
    if investing_raw:
        investing_path = Path(investing_raw).expanduser()
        if not investing_path.is_absolute():
            investing_path = (REPO_ROOT / investing_path).resolve(strict=False)
    else:
        investing_path = (REPO_ROOT / "archives" / "topics" / topic_id / "investing.md").resolve(strict=False)

    if not investing_path.exists():
        _die(f"investing.md not found: {investing_path}")

    tasks_db = Path(str(args.tasks_db)).expanduser()
    if not tasks_db.is_absolute():
        tasks_db = (REPO_ROOT / tasks_db).resolve(strict=False)

    companies, warnings = parse_company_pool(investing_path)
    candidates = [c for c in companies if (c.get("gaps") or "").strip()]
    candidates.sort(
        key=lambda r: (
            0 if r.get("status") == "thesis_candidate" else 1,
            -priority_rank(r.get("priority") or ""),
            (r.get("ticker") or r.get("company") or ""),
        )
    )
    selected = candidates[: max(0, int(args.limit))]

    store = _open_task_store(tasks_db)
    created: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    conn = sqlite3.connect(str(tasks_db))
    try:
        for row in selected:
            company = (row.get("company") or "").strip()
            if not company:
                continue
            ticker = (row.get("ticker") or "").strip()
            gap = (row.get("gaps") or "").strip()
            gap_short = gap.split("；")[0].split(";")[0].split("\n")[0].strip()
            if len(gap_short) > 80:
                gap_short = gap_short[:77].rstrip() + "..."

            title = f"[{topic_id}] Verify {company}{f' ({ticker})' if ticker else ''} — {gap_short or 'missing evidence'}"
            if _task_exists(conn, topic_id=topic_id, title=title):
                skipped.append({"title": title, "reason": "duplicate_title"})
                continue

            tags = ["auto_investing", f"topic:{topic_id}"]
            if ticker:
                tags.append(f"ticker:{ticker}")
            segment = (row.get("segment") or "").strip()
            if segment:
                tags.append(f"segment:{segment}")
            level = (row.get("evidence_level") or "").strip()
            if level:
                tags.append(f"level:{level}")
            if args.tag:
                tags.append(f"run:{args.tag}")

            description = "\n".join(
                [
                    f"- company: {company}",
                    f"- ticker: {ticker}",
                    f"- segment: {segment}",
                    f"- gap: {gap}",
                    f"- source: {_repo_rel(investing_path)}",
                ]
            ).strip()

            priority = (row.get("priority") or "").strip().lower() or "medium"
            if priority not in ("low", "medium", "high"):
                priority = "medium"

            task = store.create_task(
                title=title,
                description=description,
                category="investing",
                priority=priority,
                tags=tags,
                topic_id=topic_id,
                source=str(args.source).strip() or None,
            )
            created.append({"id": task.id, "title": task.title, "priority": task.priority, "tags": task.tags})
    finally:
        conn.close()

    payload: dict[str, Any] = {
        "generated_at": _now_iso(),
        "topic_id": topic_id,
        "investing_path": str(investing_path),
        "tasks_db": str(tasks_db),
        "warnings": warnings,
        "created": created,
        "skipped": skipped,
    }

    out_raw = str(args.out).strip()
    if out_raw:
        out_path = Path(out_raw).expanduser()
        if not out_path.is_absolute():
            out_path = (REPO_ROOT / out_path).resolve(strict=False)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(str(out_path))
        return 0

    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

