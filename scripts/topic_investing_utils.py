from __future__ import annotations

import re
from pathlib import Path


def _is_table_row(line: str) -> bool:
    s = line.strip()
    return s.startswith("|") and s.endswith("|")


def _strip_code(value: str) -> str:
    return (value or "").strip().strip("`").strip()


def _normalize_cell(value: str) -> str:
    return _strip_code(value).strip()


def normalize_status(value: str) -> str:
    return _normalize_cell(value).lower().replace(" ", "_")


def normalize_priority(value: str) -> str:
    return _normalize_cell(value).lower().replace(" ", "_")


def priority_rank(priority: str) -> int:
    p = normalize_priority(priority)
    if p == "high":
        return 3
    if p == "medium":
        return 2
    if p == "low":
        return 1
    return 0


def _find_heading(lines: list[str], *, startswith: str) -> int | None:
    needle = startswith.strip()
    for idx, line in enumerate(lines):
        if line.strip().startswith(needle):
            return idx
    return None


def _find_first_table_after(lines: list[str], start_idx: int, *, max_lookahead: int = 200) -> tuple[int | None, list[str] | None]:
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


def parse_company_pool(investing_path: Path) -> tuple[list[dict[str, str]], list[str]]:
    warnings: list[str] = []
    raw = investing_path.read_text(encoding="utf-8", errors="replace")
    lines = raw.splitlines()

    heading_idx = _find_heading(lines, startswith="## 公司池")
    if heading_idx is None:
        warnings.append("investing.md: missing '## 公司池' section")
        return [], warnings

    header_idx, header = _find_first_table_after(lines, heading_idx + 1)
    if header_idx is None or not header:
        warnings.append("investing.md: company pool table not found under '## 公司池'")
        return [], warnings

    header_norm = [h.strip().lower() for h in header]

    def _idx_of(*names: str) -> int | None:
        for n in names:
            needle = n.lower()
            for i, h in enumerate(header_norm):
                if h == needle:
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

    rows: list[dict[str, str]] = []
    for line in lines[header_idx + 2 :]:
        if not _is_table_row(line):
            break
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if all(not _normalize_cell(c) for c in cells):
            continue

        company = _normalize_cell(cells[idx_company]) if idx_company < len(cells) else ""
        if not company:
            continue

        rows.append(
            {
                "segment": _normalize_cell(cells[idx_segment]) if idx_segment is not None and idx_segment < len(cells) else "",
                "company": company,
                "ticker": _normalize_cell(cells[idx_ticker]) if idx_ticker is not None and idx_ticker < len(cells) else "",
                "market": _normalize_cell(cells[idx_market]) if idx_market is not None and idx_market < len(cells) else "",
                "hypothesis": _normalize_cell(cells[idx_hyp]) if idx_hyp is not None and idx_hyp < len(cells) else "",
                "evidence_level": _normalize_cell(cells[idx_level]) if idx_level is not None and idx_level < len(cells) else "",
                "status": normalize_status(cells[idx_status]) if idx_status is not None and idx_status < len(cells) else "",
                "priority": normalize_priority(cells[idx_priority]) if idx_priority is not None and idx_priority < len(cells) else "",
                "gaps": _normalize_cell(cells[idx_gaps]) if idx_gaps is not None and idx_gaps < len(cells) else "",
            }
        )

    return rows, warnings

