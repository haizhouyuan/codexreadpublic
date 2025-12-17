#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


_TOPIC_ID_RE = re.compile(r"^[a-z0-9_][a-z0-9_-]{0,63}$")


def _is_table_row(line: str) -> bool:
    s = line.strip()
    return s.startswith("|") and s.endswith("|")


def _strip_code(value: str) -> str:
    return (value or "").strip().strip("`").strip()


def _normalize_cell(value: str) -> str:
    return _strip_code(value).strip()


def _normalize_status(value: str) -> str:
    return _normalize_cell(value).lower().replace(" ", "_")


def _normalize_priority(value: str) -> str:
    return _normalize_cell(value).lower().replace(" ", "_")


def _find_heading(lines: list[str], *, startswith: str) -> int | None:
    needle = startswith.strip()
    for idx, ln in enumerate(lines):
        if ln.strip().startswith(needle):
            return idx
    return None


def _find_first_table_after(lines: list[str], start_idx: int, *, max_lookahead: int = 120) -> tuple[int | None, list[str] | None]:
    end = min(len(lines), start_idx + max_lookahead)
    for i in range(start_idx, end):
        if _is_table_row(lines[i]) and "ticker" in lines[i].lower() and "status" in lines[i].lower():
            header = [c.strip() for c in lines[i].strip().strip("|").split("|")]
            return i, header
    # Fallback: first table row after heading.
    for i in range(start_idx, end):
        if _is_table_row(lines[i]) and "|" in lines[i]:
            header = [c.strip() for c in lines[i].strip().strip("|").split("|")]
            return i, header
    return None, None


def _parse_company_pool(investing_path: Path) -> tuple[list[dict[str, str]], list[str]]:
    warnings: list[str] = []
    raw = investing_path.read_text(encoding="utf-8")
    lines = raw.splitlines()

    heading_idx = _find_heading(lines, startswith="## 公司池")
    if heading_idx is None:
        warnings.append("investing.md: missing '## 公司池' section")
        return [], warnings

    header_idx, header = _find_first_table_after(lines, heading_idx + 1)
    if header_idx is None or not header:
        warnings.append("investing.md: company pool table not found under '## 公司池'")
        return [], warnings

    # Map header -> index.
    header_norm = [h.strip().lower() for h in header]
    def _idx_of(*names: str) -> int | None:
        for n in names:
            n2 = n.lower()
            for i, h in enumerate(header_norm):
                if h == n2:
                    return i
        return None

    idx_company = _idx_of("公司", "company")
    idx_ticker = _idx_of("ticker", "代码", "证券代码")
    idx_market = _idx_of("市场", "market")
    idx_segment = _idx_of("细分赛道", "segment")
    idx_hyp = _idx_of("暴露/投资假设（可证伪）", "暴露/投资假设", "投资假设", "hypothesis")
    idx_level = _idx_of("证据等级", "level", "evidence")
    idx_status = _idx_of("status", "状态")
    idx_priority = _idx_of("priority", "优先级")
    idx_gaps = _idx_of("关键缺口（需任务化）", "关键缺口", "gaps")

    if idx_company is None:
        warnings.append("investing.md: company pool table missing '公司' column")
        return [], warnings
    if idx_status is None:
        warnings.append("investing.md: company pool table missing 'status' column")
        return [], warnings

    # Walk table rows.
    rows: list[dict[str, str]] = []
    for ln in lines[header_idx + 2 :]:
        if not _is_table_row(ln):
            break
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        # Skip placeholder empty row.
        if all(not _normalize_cell(c) for c in cells):
            continue

        company = _normalize_cell(cells[idx_company]) if idx_company < len(cells) else ""
        if not company:
            continue
        row = {
            "segment": _normalize_cell(cells[idx_segment]) if idx_segment is not None and idx_segment < len(cells) else "",
            "company": company,
            "ticker": _normalize_cell(cells[idx_ticker]) if idx_ticker is not None and idx_ticker < len(cells) else "",
            "market": _normalize_cell(cells[idx_market]) if idx_market is not None and idx_market < len(cells) else "",
            "hypothesis": _normalize_cell(cells[idx_hyp]) if idx_hyp is not None and idx_hyp < len(cells) else "",
            "evidence_level": _normalize_cell(cells[idx_level]) if idx_level is not None and idx_level < len(cells) else "",
            "status": _normalize_status(cells[idx_status]) if idx_status is not None and idx_status < len(cells) else "",
            "priority": _normalize_priority(cells[idx_priority]) if idx_priority is not None and idx_priority < len(cells) else "",
            "gaps": _normalize_cell(cells[idx_gaps]) if idx_gaps is not None and idx_gaps < len(cells) else "",
        }
        rows.append(row)

    return rows, warnings


def _count_investing_tasks(tasks_db: Path, *, topic_id: str) -> tuple[int, int, list[str]]:
    """
    Returns (open_investing_tasks, open_all_tasks, warnings).

    We consider open tasks: pending|in_progress.
    """
    warnings: list[str] = []
    if not tasks_db.exists():
        warnings.append(f"tasks db not found: {tasks_db}")
        return 0, 0, warnings

    conn = sqlite3.connect(str(tasks_db))
    try:
        cur = conn.execute(
            """
            SELECT COUNT(*)
            FROM tasks
            WHERE topic_id = ?
              AND status IN ('pending','in_progress')
            """,
            (topic_id,),
        )
        open_all = int(cur.fetchone()[0] or 0)

        cur2 = conn.execute(
            """
            SELECT COUNT(*)
            FROM tasks
            WHERE topic_id = ?
              AND category = 'investing'
              AND status IN ('pending','in_progress')
            """,
            (topic_id,),
        )
        open_investing = int(cur2.fetchone()[0] or 0)
        return open_investing, open_all, warnings
    except sqlite3.Error as e:
        warnings.append(f"tasks db error: {e}")
        return 0, 0, warnings
    finally:
        conn.close()


@dataclass(frozen=True)
class GateResult:
    topic_id: str
    ok: bool
    errors: list[str]
    warnings: list[str]
    stats: dict[str, Any]


def _gate_one(
    topic_dir: Path,
    *,
    tasks_db: Path,
    min_companies: int,
    min_thesis_candidates: int,
    min_investing_tasks: int,
    allow_missing_tasks_db: bool,
) -> GateResult:
    topic_id = topic_dir.name
    errors: list[str] = []
    warnings: list[str] = []

    investing_path = topic_dir / "investing.md"
    if not investing_path.exists():
        errors.append("missing investing.md (run Investability Gate first)")
        return GateResult(topic_id=topic_id, ok=False, errors=errors, warnings=warnings, stats={})

    companies, pool_warnings = _parse_company_pool(investing_path)
    warnings.extend(pool_warnings)

    thesis = [r for r in companies if r.get("status") == "thesis_candidate"]
    candidates = [r for r in companies if r.get("status") in {"candidate", "thesis_candidate"}]

    open_investing, open_all, task_warnings = _count_investing_tasks(tasks_db, topic_id=topic_id)
    warnings.extend(task_warnings)

    if len(companies) < min_companies:
        errors.append(f"company pool size {len(companies)} < min_companies={min_companies}")
    if len(thesis) < min_thesis_candidates:
        errors.append(f"thesis_candidate count {len(thesis)} < min_thesis_candidates={min_thesis_candidates}")

    if not tasks_db.exists() and allow_missing_tasks_db:
        warnings.append("tasks db missing: skipping investing task gate (allow_missing_tasks_db=1)")
    else:
        if open_investing < min_investing_tasks:
            errors.append(
                f"open investing tasks {open_investing} < min_investing_tasks={min_investing_tasks} (open_all={open_all})"
            )

    stats = {
        "topic_dir": str(topic_dir),
        "investing_path": str(investing_path),
        "companies_total": len(companies),
        "companies_candidate_or_thesis": len(candidates),
        "thesis_candidates": len(thesis),
        "open_investing_tasks": open_investing,
        "open_all_tasks": open_all,
        "min_companies": min_companies,
        "min_thesis_candidates": min_thesis_candidates,
        "min_investing_tasks": min_investing_tasks,
    }
    ok = not errors
    return GateResult(topic_id=topic_id, ok=ok, errors=errors, warnings=warnings, stats=stats)


def _emit_human(result: GateResult) -> None:
    status = "OK" if result.ok else "FAIL"
    sys.stdout.write(f"[{status}] topic={result.topic_id}\n")
    for e in result.errors:
        sys.stdout.write(f"  - ERROR: {e}\n")
    for w in result.warnings:
        sys.stdout.write(f"  - WARN: {w}\n")
    sys.stdout.write(f"  - stats: {json.dumps(result.stats, ensure_ascii=False)}\n")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Investability Gate check for topic investing.md.")
    parser.add_argument("topic_id", nargs="?", help="Topic id under topic-root.")
    parser.add_argument("--topic-root", default="archives/topics", help="Topic root (default: archives/topics)")
    parser.add_argument("--tasks-db", default="state/tasks.sqlite", help="Tasks SQLite path (default: state/tasks.sqlite)")
    parser.add_argument("--all", action="store_true", help="Check all topics under topic-root.")
    parser.add_argument("--min-companies", type=int, default=10)
    parser.add_argument("--min-thesis-candidates", type=int, default=1)
    parser.add_argument("--min-investing-tasks", type=int, default=3)
    parser.add_argument("--allow-missing-tasks-db", action="store_true", help="Do not fail when tasks db is missing.")
    parser.add_argument("--json", action="store_true", help="Print JSON only.")
    parser.add_argument("--out", help="Write JSON result to a file.")
    parser.add_argument("--fail-on-warn", action="store_true", help="Exit non-zero if any warnings.")
    args = parser.parse_args(argv)

    topic_root = Path(args.topic_root).expanduser().resolve()
    tasks_db = Path(args.tasks_db).expanduser().resolve()
    if not topic_root.exists():
        raise SystemExit(f"topic-root not found: {topic_root}")

    results: list[GateResult] = []
    if args.all:
        for child in sorted(topic_root.iterdir()):
            if not child.is_dir():
                continue
            if not _TOPIC_ID_RE.match(child.name):
                continue
            results.append(
                _gate_one(
                    child,
                    tasks_db=tasks_db,
                    min_companies=args.min_companies,
                    min_thesis_candidates=args.min_thesis_candidates,
                    min_investing_tasks=args.min_investing_tasks,
                    allow_missing_tasks_db=args.allow_missing_tasks_db,
                )
            )
    else:
        tid = (args.topic_id or "").strip()
        if not tid or not _TOPIC_ID_RE.match(tid):
            raise SystemExit("topic_id required (or use --all)")
        topic_dir = (topic_root / tid).resolve()
        if not topic_dir.exists():
            raise SystemExit(f"topic not found: {topic_dir}")
        results.append(
            _gate_one(
                topic_dir,
                tasks_db=tasks_db,
                min_companies=args.min_companies,
                min_thesis_candidates=args.min_thesis_candidates,
                min_investing_tasks=args.min_investing_tasks,
                allow_missing_tasks_db=args.allow_missing_tasks_db,
            )
        )

    payload = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "topic_root": str(topic_root),
        "tasks_db": str(tasks_db),
        "results": [
            {
                "topic_id": r.topic_id,
                "ok": r.ok,
                "errors": r.errors,
                "warnings": r.warnings,
                "stats": r.stats,
            }
            for r in results
        ],
    }

    if args.out:
        out_path = Path(args.out).expanduser()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if args.json:
        sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    else:
        for r in results:
            _emit_human(r)

    any_errors = any(not r.ok for r in results)
    any_warnings = any(bool(r.warnings) for r in results)
    if any_errors:
        return 1
    if args.fail_on_warn and any_warnings:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

