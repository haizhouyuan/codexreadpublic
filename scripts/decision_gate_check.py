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


_REF_RE = re.compile(
    r"topic=(?P<topic>[a-z0-9_][a-z0-9_-]{0,63})\s*;\s*digest=(?P<digest>[^;]+?)\s*;\s*claim_id=(?P<claim_id>[A-Za-z0-9_.:-]+)"
)


def _strip_quotes(value: str) -> str:
    v = value.strip()
    if len(v) >= 2 and ((v[0] == v[-1] == '"') or (v[0] == v[-1] == "'")):
        return v[1:-1]
    return v


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
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

    fm: dict[str, Any] = {}
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


def _is_table_row(line: str) -> bool:
    s = line.strip()
    return s.startswith("|") and s.endswith("|")


def _normalize_level(value: str) -> str:
    v = (value or "").strip()
    v2 = v.lower().replace(" ", "")
    if "levela" in v2 or v2 == "a":
        return "Level A"
    if "levelb" in v2 or v2 == "b":
        return "Level B"
    if "levelc" in v2 or v2 == "c":
        return "Level C"
    if v.startswith("Level "):
        return v
    return v


def _level_rank(level: str) -> int:
    if level == "Level A":
        return 3
    if level == "Level B":
        return 2
    if level == "Level C":
        return 1
    return 0


def _find_section(lines: list[str], *, heading: str) -> int | None:
    for i, ln in enumerate(lines):
        if ln.strip() == heading:
            return i
    return None


def _find_table_after(lines: list[str], start_idx: int, *, max_lookahead: int = 160) -> tuple[int | None, list[str] | None]:
    end = min(len(lines), start_idx + max_lookahead)
    for i in range(start_idx, end):
        if _is_table_row(lines[i]):
            header = [c.strip() for c in lines[i].strip().strip("|").split("|")]
            return i, header
    return None, None


def _extract_claim_ids(digest_path: Path) -> set[str]:
    raw = digest_path.read_text(encoding="utf-8")
    _fm, body = _parse_frontmatter(raw)
    lines = body.splitlines()

    # Find claim ledger section then table.
    idx = None
    for i, ln in enumerate(lines):
        if ln.strip().startswith("## Claim Ledger"):
            idx = i
            break
    if idx is None:
        return set()

    header_idx, header = _find_table_after(lines, idx + 1)
    if header_idx is None or not header:
        return set()

    header_norm = [h.strip().lower().replace(" ", "") for h in header]
    if "claim_id" in header_norm:
        claim_id_idx = header_norm.index("claim_id")
    elif "claimid" in header_norm:
        claim_id_idx = header_norm.index("claimid")
    else:
        return set()

    out: set[str] = set()
    for ln in lines[header_idx + 2 :]:
        if not _is_table_row(ln):
            break
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        if claim_id_idx >= len(cells):
            continue
        cid = cells[claim_id_idx].strip("`").strip()
        if cid:
            out.add(cid)
    return out


@dataclass(frozen=True)
class DecisionGateResult:
    path: str
    ok: bool
    errors: list[str]
    warnings: list[str]
    stats: dict[str, Any]


def _parse_topic_ids(raw: str) -> list[str]:
    s = (raw or "").strip()
    if not s:
        return []
    if s.startswith("[") and s.endswith("]"):
        s = s[1:-1]
    parts = []
    for p in s.split(","):
        p2 = p.strip().strip("'\"")
        if p2:
            parts.append(p2)
    return parts


def _count_task_ids(tasks_db: Path, task_ids: list[str]) -> tuple[int, list[str]]:
    warnings: list[str] = []
    if not task_ids:
        return 0, warnings
    if not tasks_db.exists():
        warnings.append(f"tasks db not found: {tasks_db}")
        return 0, warnings
    conn = sqlite3.connect(str(tasks_db))
    try:
        found = 0
        for tid in task_ids:
            cur = conn.execute("SELECT 1 FROM tasks WHERE id = ? LIMIT 1", (tid,))
            if cur.fetchone():
                found += 1
        return found, warnings
    except sqlite3.Error as e:
        warnings.append(f"tasks db error: {e}")
        return 0, warnings
    finally:
        conn.close()


def _gate_one(path: Path, *, topics_root: Path, tasks_db: Path) -> DecisionGateResult:
    errors: list[str] = []
    warnings: list[str] = []

    raw = path.read_text(encoding="utf-8")
    fm, body = _parse_frontmatter(raw)
    ticker = str(fm.get("ticker") or "").strip()
    status = str(fm.get("status") or "").strip().lower()
    topic_ids = _parse_topic_ids(str(fm.get("topic_ids") or ""))

    if not ticker:
        errors.append("frontmatter missing ticker")
    if not topic_ids:
        errors.append("frontmatter missing topic_ids")

    # Required sections.
    lines = body.splitlines()
    required_sections = [
        "## Thesis",
        "## Evidence Map（强制引用）",
        "## Bull / Base / Bear",
        "## Trade Plan（规则化）",
        "## Monitoring Plan",
        "## Open Gaps & Tasks",
        "## Decision Log",
    ]
    for sec in required_sections:
        if _find_section(lines, heading=sec) is None:
            errors.append(f"missing section: {sec}")

    # Evidence Map table.
    evidence_idx = _find_section(lines, heading="## Evidence Map（强制引用）")
    evidence_rows: list[dict[str, str]] = []
    unique_level_b_digests: set[str] = set()
    level_counts = {"Level A": 0, "Level B": 0, "Level C": 0, "": 0}

    if evidence_idx is not None:
        header_idx, header = _find_table_after(lines, evidence_idx + 1)
        if header_idx is None or not header:
            errors.append("Evidence Map: table not found")
        else:
            header_norm = [h.strip().lower() for h in header]
            def _idx(name: str) -> int | None:
                n = name.lower()
                for i, h in enumerate(header_norm):
                    if h == n:
                        return i
                return None

            idx_ref = _idx("ref（topic/digest/claim_id）") or _idx("ref")  # allow shortened header
            idx_level = _idx("level（a/b/c）") or _idx("level")

            if idx_ref is None:
                errors.append("Evidence Map: missing ref column")
            if idx_level is None:
                errors.append("Evidence Map: missing level column")

            if idx_ref is not None and idx_level is not None:
                for ln in lines[header_idx + 2 :]:
                    if not _is_table_row(ln):
                        break
                    cells = [c.strip() for c in ln.strip().strip("|").split("|")]
                    if all(not c.strip() for c in cells):
                        continue
                    ref_raw = cells[idx_ref] if idx_ref < len(cells) else ""
                    level_raw = cells[idx_level] if idx_level < len(cells) else ""
                    ref_raw = ref_raw.strip("`").strip()
                    level = _normalize_level(level_raw)
                    if not ref_raw:
                        continue
                    m = _REF_RE.search(ref_raw)
                    if not m:
                        errors.append(f"Evidence Map: invalid ref format: {ref_raw}")
                        continue
                    topic = m.group("topic")
                    digest = m.group("digest").strip()
                    claim_id = m.group("claim_id").strip()
                    if not claim_id:
                        errors.append(f"Evidence Map: empty claim_id in ref: {ref_raw}")
                        continue

                    digest_path = (topics_root / topic / "digests" / digest).resolve()
                    if not digest_path.exists():
                        errors.append(f"Evidence Map: digest not found: {topic}/digests/{digest}")
                        continue
                    claim_ids = _extract_claim_ids(digest_path)
                    if claim_id not in claim_ids:
                        errors.append(f"Evidence Map: claim_id not found in digest: {claim_id} ({topic}/digests/{digest})")
                        continue

                    evidence_rows.append({"topic": topic, "digest": digest, "claim_id": claim_id, "level": level})
                    level_counts[level if level in level_counts else ""] = level_counts.get(level, 0) + 1
                    if level == "Level B":
                        unique_level_b_digests.add(f"{topic}/{digest}")

    # Decision Gate rule: >=1 Level A OR >=2 independent Level B.
    has_level_a = any(r["level"] == "Level A" for r in evidence_rows)
    level_b_unique = len(unique_level_b_digests)
    if not (has_level_a or level_b_unique >= 2):
        errors.append(f"Decision Gate: evidence threshold not met (LevelA={int(has_level_a)} LevelB_unique_digests={level_b_unique})")

    # Open gaps/tasks: require tasks only when status moves beyond draft.
    gaps_idx = _find_section(lines, heading="## Open Gaps & Tasks")
    task_ids: list[str] = []
    if gaps_idx is not None:
        header_idx, header = _find_table_after(lines, gaps_idx + 1)
        if header_idx is None or not header:
            warnings.append("Open Gaps & Tasks: table not found")
        else:
            header_norm = [h.strip().lower() for h in header]
            task_col = None
            for i, h in enumerate(header_norm):
                if "task" in h:
                    task_col = i
                    break
            if task_col is None:
                warnings.append("Open Gaps & Tasks: tasks column not found")
            else:
                for ln in lines[header_idx + 2 :]:
                    if not _is_table_row(ln):
                        break
                    cells = [c.strip() for c in ln.strip().strip("|").split("|")]
                    if task_col >= len(cells):
                        continue
                    cell = cells[task_col].strip()
                    # accept multiple ids separated by comma/space
                    for m in re.finditer(r"task_[0-9a-f]{8,}", cell):
                        task_ids.append(m.group(0))

    found_tasks, task_warnings = _count_task_ids(tasks_db, task_ids)
    warnings.extend(task_warnings)
    if status in {"reviewed", "active"}:
        if not task_ids:
            errors.append("Decision Gate: status requires tasks, but no task ids found in Open Gaps & Tasks")
        elif tasks_db.exists() and found_tasks < len(set(task_ids)):
            errors.append(f"Decision Gate: some task ids not found in tasks db (found={found_tasks} listed={len(set(task_ids))})")
    else:
        if not task_ids:
            warnings.append("status is draft/closed: no task ids found (recommended to taskify key gaps before reviewed)")

    stats = {
        "ticker": ticker,
        "status": status,
        "topic_ids": topic_ids,
        "evidence_rows": len(evidence_rows),
        "level_a": sum(1 for r in evidence_rows if r["level"] == "Level A"),
        "level_b": sum(1 for r in evidence_rows if r["level"] == "Level B"),
        "level_c": sum(1 for r in evidence_rows if r["level"] == "Level C"),
        "level_b_unique_digests": level_b_unique,
        "tasks_listed": len(set(task_ids)),
        "tasks_found": found_tasks,
    }

    ok = not errors
    return DecisionGateResult(path=str(path), ok=ok, errors=errors, warnings=warnings, stats=stats)


def _emit_human(result: DecisionGateResult) -> None:
    status = "OK" if result.ok else "FAIL"
    sys.stdout.write(f"[{status}] decision={result.path}\n")
    for e in result.errors:
        sys.stdout.write(f"  - ERROR: {e}\n")
    for w in result.warnings:
        sys.stdout.write(f"  - WARN: {w}\n")
    sys.stdout.write(f"  - stats: {json.dumps(result.stats, ensure_ascii=False)}\n")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Decision Gate check for Decision Package markdown.")
    parser.add_argument("path", nargs="?", help="Decision package path (or use --all).")
    parser.add_argument("--topics-root", default="archives/topics")
    parser.add_argument("--tasks-db", default="state/tasks.sqlite")
    parser.add_argument("--all", action="store_true", help="Check all decision packages under archives/investing/decisions/")
    parser.add_argument("--json", action="store_true", help="Print JSON only.")
    parser.add_argument("--out", help="Write JSON result to a file.")
    parser.add_argument("--fail-on-warn", action="store_true")
    args = parser.parse_args(argv)

    topics_root = Path(args.topics_root).expanduser().resolve()
    tasks_db = Path(args.tasks_db).expanduser().resolve()

    results: list[DecisionGateResult] = []
    if args.all:
        decisions_dir = Path("archives/investing/decisions").expanduser().resolve()
        if decisions_dir.exists():
            for p in sorted(decisions_dir.glob("*.md")):
                results.append(_gate_one(p, topics_root=topics_root, tasks_db=tasks_db))
        else:
            raise SystemExit(f"decisions dir not found: {decisions_dir}")
    else:
        if not args.path:
            raise SystemExit("path required (or use --all)")
        p = Path(args.path).expanduser().resolve()
        if not p.exists():
            raise SystemExit(f"decision package not found: {p}")
        results.append(_gate_one(p, topics_root=topics_root, tasks_db=tasks_db))

    payload = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "topics_root": str(topics_root),
        "tasks_db": str(tasks_db),
        "results": [
            {
                "path": r.path,
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

