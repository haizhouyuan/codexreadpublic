#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
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


def _normalize_level(value: str) -> str:
    v = _normalize_cell(value)
    if not v:
        return ""
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
    lvl = (level or "").strip()
    if lvl == "Level A":
        return 3
    if lvl == "Level B":
        return 2
    if lvl == "Level C":
        return 1
    return 0


def _priority_rank(priority: str) -> int:
    p = (priority or "").strip()
    if p == "high":
        return 3
    if p == "medium":
        return 2
    if p == "low":
        return 1
    return 0


def _status_rank(status: str) -> int:
    s = (status or "").strip()
    if s == "thesis_candidate":
        return 4
    if s == "candidate":
        return 3
    if s == "parked":
        return 2
    if s == "rejected":
        return 1
    return 0


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
        warnings.append("missing '## 公司池' section")
        return [], warnings

    header_idx, header = _find_first_table_after(lines, heading_idx + 1)
    if header_idx is None or not header:
        warnings.append("company pool table not found under '## 公司池'")
        return [], warnings

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

    if idx_company is None or idx_status is None:
        warnings.append("company pool table missing required columns (公司/status)")
        return [], warnings

    rows: list[dict[str, str]] = []
    for ln in lines[header_idx + 2 :]:
        if not _is_table_row(ln):
            break
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
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
            "evidence_level": _normalize_level(cells[idx_level]) if idx_level is not None and idx_level < len(cells) else "",
            "status": _normalize_status(cells[idx_status]) if idx_status is not None and idx_status < len(cells) else "",
            "priority": _normalize_priority(cells[idx_priority]) if idx_priority is not None and idx_priority < len(cells) else "",
            "gaps": _normalize_cell(cells[idx_gaps]) if idx_gaps is not None and idx_gaps < len(cells) else "",
        }
        rows.append(row)

    return rows, warnings


@dataclass
class UniverseItem:
    key: str
    ticker: str
    name: str
    markets: set[str]
    topics: set[str]
    segments: set[str]
    hypothesis_samples: list[str]
    best_level: str
    best_status: str
    best_priority: str
    gaps_samples: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "ticker": self.ticker,
            "name": self.name,
            "markets": sorted([m for m in self.markets if m]),
            "topics": sorted([t for t in self.topics if t]),
            "segments": sorted([s for s in self.segments if s]),
            "hypothesis_samples": [h for h in self.hypothesis_samples if h][:3],
            "evidence_level": self.best_level,
            "status": self.best_status,
            "priority": self.best_priority,
            "gaps_samples": [g for g in self.gaps_samples if g][:3],
        }


def _item_key(*, ticker: str, name: str) -> str:
    t = (ticker or "").strip().upper()
    if t:
        return f"ticker:{t}"
    n = (name or "").strip().lower()
    n = re.sub(r"\s+", " ", n)
    return f"name:{n}"


def _update_best(current: str, new: str, ranker) -> str:
    if ranker(new) > ranker(current):
        return new
    return current


def _score(item: UniverseItem) -> int:
    return (
        _status_rank(item.best_status) * 100
        + _priority_rank(item.best_priority) * 10
        + _level_rank(item.best_level) * 10
        + len(item.topics)
    )


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _write_watchlist(path: Path, *, items: list[UniverseItem], max_items: int) -> None:
    _ensure_parent(path)
    ts = datetime.now().astimezone().isoformat()
    lines: list[str] = []
    lines.append(f"# Watchlist（自动聚合）")
    lines.append("")
    lines.append(f"- generated_at: `{ts}`")
    lines.append(f"- source: `archives/topics/*/investing.md`")
    lines.append("")
    lines.append("> 说明：watchlist 仅用于优先级排序与跟踪入口；任何“要影响仓位”的结论必须进入决策包并通过 Decision Gate。")
    lines.append("")
    lines.append("| ticker | 公司 | 市场 | topics | status | priority | level | gaps |")
    lines.append("|--------|------|------|--------|--------|----------|-------|------|")

    for it in sorted(items, key=_score, reverse=True)[:max_items]:
        topics = ", ".join(sorted(it.topics))[:120]
        markets = ", ".join(sorted(it.markets))[:60]
        gaps = (it.gaps_samples[0] if it.gaps_samples else "")[:80]
        lines.append(
            f"| `{it.ticker}` | {it.name} | {markets} | {topics} | {it.best_status} | {it.best_priority} | {it.best_level} | {gaps} |"
        )

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Build global investing universe + watchlist from topic investing.md files.")
    parser.add_argument("--topics-root", default="archives/topics", help="Topics root (default: archives/topics)")
    parser.add_argument("--out-json", default="archives/investing/universe.json")
    parser.add_argument("--out-watchlist", default="archives/investing/watchlist.md")
    parser.add_argument("--max-watchlist", type=int, default=20)
    parser.add_argument(
        "--include-status",
        default="candidate,thesis_candidate",
        help="Comma-separated statuses to include (default: candidate,thesis_candidate)",
    )
    args = parser.parse_args(argv)

    topics_root = Path(args.topics_root).expanduser().resolve()
    if not topics_root.exists():
        raise SystemExit(f"topics root not found: {topics_root}")

    include_status = {s.strip().lower() for s in (args.include_status or "").split(",") if s.strip()}

    items_by_key: dict[str, UniverseItem] = {}
    warnings: list[str] = []
    raw_rows = 0

    for topic_dir in sorted(topics_root.iterdir()):
        if not topic_dir.is_dir():
            continue
        topic_id = topic_dir.name
        if not _TOPIC_ID_RE.match(topic_id):
            continue
        investing_path = topic_dir / "investing.md"
        if not investing_path.exists():
            continue

        rows, row_warnings = _parse_company_pool(investing_path)
        warnings.extend([f"{topic_id}: {w}" for w in row_warnings])
        for r in rows:
            raw_rows += 1
            status = (r.get("status") or "").strip().lower()
            if include_status and status and status not in include_status:
                continue

            ticker = (r.get("ticker") or "").strip().upper()
            name = (r.get("company") or "").strip()
            key = _item_key(ticker=ticker, name=name)

            it = items_by_key.get(key)
            if it is None:
                it = UniverseItem(
                    key=key,
                    ticker=ticker,
                    name=name,
                    markets=set(),
                    topics=set(),
                    segments=set(),
                    hypothesis_samples=[],
                    best_level="",
                    best_status="",
                    best_priority="",
                    gaps_samples=[],
                )
                items_by_key[key] = it

            it.topics.add(topic_id)
            if r.get("market"):
                it.markets.add(r["market"])
            if r.get("segment"):
                it.segments.add(r["segment"])
            if r.get("hypothesis"):
                it.hypothesis_samples.append(r["hypothesis"])
            if r.get("gaps"):
                it.gaps_samples.append(r["gaps"])

            it.best_level = _update_best(it.best_level, r.get("evidence_level") or "", _level_rank)
            it.best_status = _update_best(it.best_status, status, _status_rank)
            it.best_priority = _update_best(it.best_priority, r.get("priority") or "", _priority_rank)

    universe = list(items_by_key.values())
    universe_sorted = sorted(universe, key=_score, reverse=True)

    payload: dict[str, Any] = {
        "schema_version": "1.0",
        "generated_at": datetime.now().astimezone().isoformat(),
        "topics_root": str(topics_root),
        "counts": {
            "raw_rows": raw_rows,
            "items": len(universe_sorted),
            "included_status": sorted(include_status),
        },
        "warnings": warnings,
        "items": [it.to_dict() for it in universe_sorted],
    }

    out_json = Path(args.out_json).expanduser()
    out_watchlist = Path(args.out_watchlist).expanduser()
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _write_watchlist(out_watchlist, items=universe_sorted, max_items=args.max_watchlist)

    sys.stdout.write(f"[ok] wrote {out_json} and {out_watchlist} (items={len(universe_sorted)})\n")
    if warnings:
        sys.stdout.write(f"[warn] {len(warnings)} warnings (see universe.json)\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

