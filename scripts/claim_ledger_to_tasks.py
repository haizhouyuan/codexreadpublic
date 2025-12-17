#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


REPO_ROOT = Path(__file__).resolve().parents[1]


def _die(msg: str, code: int = 2) -> None:
    raise SystemExit(f"{msg}\n(exit {code})")


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _clamp(s: str, n: int) -> str:
    s = s.strip()
    if len(s) <= n:
        return s
    return s[: n - 1].rstrip() + "…"


def _has_digits(s: str) -> bool:
    return bool(re.search(r"\d", s))


def _looks_date_like_only(claim: str) -> bool:
    c = claim.replace(" ", "")
    # Common date/time patterns in CN speech; treat as low-signal for "needs verification" tasks.
    if re.search(r"\d{4}年\d{1,2}月\d{1,2}(日|号)?", c):
        return True
    if re.search(r"\d{1,2}月\d{1,2}(日|号)", c):
        return True
    if re.search(r"\d{1,2}月份", c):
        return True
    if re.search(r"\d{1,2}点\d{1,2}分", c):
        return True
    return False


def _has_numeric_unit(claim: str) -> bool:
    c = claim
    units = [
        r"[%％]",
        r"亿",
        r"万",
        r"千",
        r"百",
        r"元",
        r"美元",
        r"USD",
        r"CNY",
        r"人民币",
        r"GB",
        r"TB",
        r"MB",
        r"TOPS",
        r"bps",
        r"GHz",
        r"nm",
        r"倍",
        r"T(?![a-zA-Z])",
    ]
    return bool(re.search("|".join(units), c))


def _has_compact_numeric_fact(claim: str) -> bool:
    c = claim.replace(" ", "")
    return bool(
        re.search(
            r"\d+(?:\.\d+)?(?:[%％]|GB|TB|MB|TOPS|bps|GHz|nm|倍|亿|万|千|百|元|美元|USD|CNY|T(?![A-Za-z]))",
            c,
        )
    )


def _has_domain_keyword(claim: str) -> bool:
    keywords = [
        "同比",
        "环比",
        "增长",
        "下降",
        "市场",
        "份额",
        "营收",
        "利润",
        "毛利",
        "毛利率",
        "指引",
        "订单",
        "渗透率",
        "算力",
        "显存",
        "带宽",
        "功耗",
        "价格",
        "成本",
        "H200",
        "H20",
    ]
    return any(k in claim for k in keywords)


def _score_claim(claim: str) -> int:
    c = claim.strip()
    if not c:
        return -10_000
    if not _has_digits(c):
        return -10_000
    if _looks_date_like_only(c) and not _has_numeric_unit(c):
        return -10_000

    score = 0
    if _has_compact_numeric_fact(c):
        score += 10
    if _has_domain_keyword(c):
        score += 6

    # Prefer concise, “fact-like” lines.
    n = len(c)
    if n <= 40:
        score += 6
    elif n <= 80:
        score += 4
    elif n <= 140:
        score += 1
    elif n <= 220:
        score -= 2
    else:
        score -= 6

    # Penalize excessive punctuation/rambling.
    if c.count(",") + c.count("，") + c.count("、") >= 6:
        score -= 3
    if c.count("。") + c.count(".") >= 3:
        score -= 2

    return score


def _looks_high_impact(claim: str) -> bool:
    claim = claim.strip()
    if not _has_digits(claim):
        return False
    # Avoid date-only claims.
    if _looks_date_like_only(claim) and not _has_numeric_unit(claim):
        return False
    # Keep the filter permissive; final selection uses scoring.
    return _has_compact_numeric_fact(claim) and (_has_domain_keyword(claim) or len(claim) <= 120)


def _focus_claim(claim: str, *, max_len: int = 120) -> str:
    c = claim.strip()
    if len(c) <= max_len:
        return c
    m = re.search(r"\d", c)
    if not m:
        return _clamp(c, max_len)
    i = m.start()
    start = max(0, i - 40)
    end = min(len(c), i + 80)
    snippet = c[start:end].strip()
    if start > 0:
        snippet = "…" + snippet
    if end < len(c):
        snippet = snippet + "…"
    return _clamp(snippet, max_len)


@dataclass(frozen=True)
class ClaimRow:
    idx: int
    claim_id: str
    claim: str
    impact: str
    confidence: str
    verify_status: str
    evidence: str


def _extract_bvid(digest_path: Path) -> str:
    m = re.search(r"(BV[0-9A-Za-z]+)", digest_path.name)
    return m.group(1) if m else ""


def _normalize_verify_status(value: str) -> str:
    s = str(value or "").strip()
    low = s.lower()
    if low in {"unverified", "verified", "partially_verified", "falsified", "needs_source"}:
        return low
    if s in {"未核验", "待核验", "未验证", "待验证"}:
        return "unverified"
    if s in {"部分核验", "部分验证"}:
        return "partially_verified"
    if s in {"已核验", "已验证", "核验通过"}:
        return "verified"
    if s in {"证伪", "已证伪"}:
        return "falsified"
    return low


def _is_table_sep(line: str) -> bool:
    s = line.strip()
    if not s.startswith("|"):
        return False
    stripped = s.strip("|").strip()
    return bool(stripped) and all(ch in "-: " for ch in stripped)


def _parse_claim_ledger_rows(md: str) -> List[ClaimRow]:
    lines = md.splitlines()
    in_section = False
    header: List[str] | None = None
    header_norm: List[str] | None = None
    idx_col = -1
    claim_id_col = -1
    claim_col = -1
    impact_col = -1
    confidence_col = -1
    status_col = -1
    evidence_col = -1

    rows: List[ClaimRow] = []
    for ln in lines:
        if ln.startswith("## Claim Ledger"):
            in_section = True
            header = None
            header_norm = None
            continue
        if not in_section:
            continue

        if header is None:
            if not ln.strip().startswith("|"):
                continue
            if "核验状态" not in ln:
                continue
            header = [p.strip() for p in ln.strip().strip("|").split("|")]
            header_norm = [p.strip().lower().replace(" ", "") for p in header]

            def _find_exact(name: str) -> int:
                want = name.strip().lower().replace(" ", "")
                if not want:
                    return -1
                for i, col in enumerate(header_norm or []):
                    if col == want:
                        return i
                return -1

            idx_col = _find_exact("#")
            claim_id_col = _find_exact("claim_id")
            if claim_id_col < 0:
                claim_id_col = _find_exact("claimid")
            claim_col = _find_exact("claim")
            impact_col = _find_exact("影响范围")
            confidence_col = _find_exact("置信度")
            status_col = _find_exact("核验状态")

            evidence_col = -1
            for i, col in enumerate(header):
                if "来源" in col or "证据" in col:
                    evidence_col = i
                    break
            continue

        if not ln.strip().startswith("|"):
            if rows:
                break
            continue
        if ln.startswith("|---") or _is_table_sep(ln):
            continue

        parts = [p.strip() for p in ln.strip().strip("|").split("|")]
        if not parts:
            continue

        try:
            idx_raw = parts[idx_col] if 0 <= idx_col < len(parts) else parts[0]
            idx = int(idx_raw)
        except Exception:
            continue

        claim_id = parts[claim_id_col] if 0 <= claim_id_col < len(parts) else ""
        if 0 <= claim_col < len(parts):
            claim = parts[claim_col]
        elif 0 <= claim_id_col < len(parts) and claim_id_col + 1 < len(parts):
            claim = parts[claim_id_col + 1]
        else:
            claim = parts[1] if len(parts) > 1 else ""

        impact = parts[impact_col] if 0 <= impact_col < len(parts) else ""
        confidence = parts[confidence_col] if 0 <= confidence_col < len(parts) else ""
        verify_status = parts[status_col] if 0 <= status_col < len(parts) else ""
        evidence = parts[evidence_col] if 0 <= evidence_col < len(parts) else ""

        rows.append(
            ClaimRow(
                idx=idx,
                claim_id=claim_id,
                claim=claim,
                impact=impact,
                confidence=confidence,
                verify_status=verify_status,
                evidence=evidence,
            )
        )
    return rows


def _load_task_store(db_path: Path):
    # Use the repo's TaskStore implementation to keep schema consistent.
    import sys

    sys.path.insert(0, str(REPO_ROOT / "mcp-servers" / "tasks"))
    from task_store import TaskStore  # type: ignore

    store = TaskStore(db_path=str(db_path))
    store.ensure_schema()
    return store


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(description="Create tasks from digest Claim Ledgers (unverified, high-impact).")
    ap.add_argument("--topic", required=True, help="topic_id under archives/topics/<topic>/")
    ap.add_argument("--db", default="state/tasks.sqlite", help="tasks sqlite path (default: state/tasks.sqlite)")
    ap.add_argument("--max-per-digest", type=int, default=3, help="Max tasks created per digest (default: 3)")
    ap.add_argument("--max-total", type=int, default=80, help="Max tasks created total (default: 80)")
    ap.add_argument("--dry-run", action="store_true", help="Do not write tasks; just report.")
    args = ap.parse_args(argv)

    topic_id = str(args.topic).strip()
    if not topic_id:
        _die("--topic is required")
    topic_dir = REPO_ROOT / "archives" / "topics" / topic_id
    digests_dir = topic_dir / "digests"
    if not digests_dir.is_dir():
        _die(f"digests dir not found: {digests_dir}")

    db_path = (REPO_ROOT / args.db).resolve() if not Path(args.db).is_absolute() else Path(args.db).resolve()
    store = _load_task_store(db_path)

    existing_titles = {t.title for t in store.list_tasks(topic_id=topic_id, limit=200)}

    created: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []

    total_budget = max(0, int(args.max_total))
    max_per_digest = max(0, int(args.max_per_digest))

    digest_paths = sorted(digests_dir.glob("*.md"))
    for digest_path in digest_paths:
        if total_budget <= 0:
            break
        md = _read_text(digest_path)
        rows = _parse_claim_ledger_rows(md)
        if not rows:
            continue

        bvid = _extract_bvid(digest_path)
        candidates: List[ClaimRow] = []
        for row in rows:
            if _normalize_verify_status(row.verify_status) != "unverified":
                continue
            claim = row.claim.strip()
            if not claim:
                continue
            if not _looks_high_impact(claim):
                continue
            candidates.append(row)

        if not candidates:
            continue

        candidates.sort(key=lambda r: _score_claim(r.claim), reverse=True)

        per_budget = max_per_digest
        for row in candidates:
            if total_budget <= 0 or per_budget <= 0:
                break
            claim = row.claim.strip()
            if _score_claim(claim) <= 0:
                skipped.append({"digest": str(digest_path), "idx": row.idx, "reason": "low_score", "claim": claim})
                continue

            claim_ref = (row.claim_id or "").strip().strip("`") or f"#{row.idx}"
            title_prefix = f"核验：{bvid} {claim_ref}" if bvid else f"核验：{claim_ref}"
            focused = _focus_claim(claim, max_len=120)
            title = f"{title_prefix} {_clamp(focused, 60)}"
            if title in existing_titles:
                skipped.append({"digest": str(digest_path), "idx": row.idx, "reason": "duplicate_title", "title": title})
                continue

            priority = "high" if _has_digits(claim) else "medium"
            description = "\n".join(
                [
                    f"- topic_id: {topic_id}",
                    f"- digest: {digest_path}",
                    f"- claim_row: #{row.idx}",
                    f"- claim_id: {claim_ref}",
                    f"- claim: {_clamp(claim, 400)}",
                    f"- evidence: {row.evidence}",
                    "",
                    "核验建议：回看视频对应时间戳/帧，并尽量找到 Level A/B 的一手来源（公告/财报/官方说明/白皮书等）。",
                ]
            ).strip()

            payload = {
                "title": title,
                "description": description,
                "category": "investing",
                "priority": priority,
                "tags": ["claim_ledger", "bilibili", f"topic:{topic_id}"]
                + ([f"bvid:{bvid}"] if bvid else [])
                + ([f"claim_id:{claim_ref}"] if claim_ref and not claim_ref.startswith("#") else []),
                "topic_id": topic_id,
                "source": "claim_ledger",
            }

            if args.dry_run:
                created.append({"dry_run": True, **payload})
            else:
                task = store.create_task(**payload)
                created.append(task.to_dict())
                existing_titles.add(title)

            total_budget -= 1
            per_budget -= 1

    out = {
        "topic_id": topic_id,
        "db_path": str(db_path),
        "created_count": len(created),
        "skipped_count": len(skipped),
        "created": created,
        "skipped_sample": skipped[:20],
    }
    try:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    except BrokenPipeError:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main(__import__("sys").argv[1:]))
