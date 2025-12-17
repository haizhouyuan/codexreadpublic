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


def _normalize_date_key(raw: str) -> str:
    s = (raw or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return s
    if re.fullmatch(r"\d{4}-\d{2}", s):
        return s + "-01"
    if re.fullmatch(r"\d{4}", s):
        return s + "-01-01"
    return ""


def _extract_digest_path(cell: str) -> str:
    s = (cell or "").strip()
    s = s.strip("`").strip()
    # allow “digests/foo.md” or “`digests/foo.md`”
    return s


@dataclass(frozen=True)
class ValidationResult:
    topic_id: str
    ok: bool
    errors: list[str]
    warnings: list[str]
    stats: dict[str, Any]


def _validate_sources(topic_dir: Path, *, topic_id: str) -> tuple[list[str], list[str], dict[str, Any]]:
    errors: list[str] = []
    warnings: list[str] = []
    stats: dict[str, Any] = {"sources_rows": 0, "sources_missing_digests": 0, "sources_unsorted": False}

    path = topic_dir / "sources.md"
    if not path.exists():
        errors.append(f"missing sources.md: {path}")
        return errors, warnings, stats

    lines = path.read_text(encoding="utf-8").splitlines()
    header_idx = None
    for i, ln in enumerate(lines):
        if "| 日期 |" in ln and "| Digest |" in ln:
            header_idx = i
            break
    if header_idx is None:
        warnings.append("sources.md: sources table header not found")
        return errors, warnings, stats

    sep_idx = header_idx + 1
    if sep_idx >= len(lines) or not _is_table_row(lines[sep_idx]):
        warnings.append("sources.md: sources table separator not found")
        return errors, warnings, stats

    row_start = sep_idx + 1
    row_end = row_start
    while row_end < len(lines) and _is_table_row(lines[row_end]):
        row_end += 1

    rows = [ln.strip() for ln in lines[row_start:row_end] if _is_table_row(ln)]
    # Drop placeholder blank row.
    rows = [r for r in rows if not re.fullmatch(r"\|\s*\|\s*\|\s*\|\s*\|\s*\|\s*\|", r)]
    stats["sources_rows"] = len(rows)

    seen_digest: set[str] = set()
    date_keys: list[str] = []
    for row in rows:
        cells = [c.strip() for c in row.strip("|").split("|")]
        if len(cells) < 5:
            warnings.append(f"sources.md: malformed row (expected 5 cols): {row}")
            continue
        date_raw, _typ, _title, _link, digest_cell = cells[:5]
        digest_rel = _extract_digest_path(digest_cell)
        if not digest_rel:
            warnings.append(f"sources.md: empty digest cell: {row}")
        else:
            if digest_rel in seen_digest:
                warnings.append(f"sources.md: duplicate digest entry: {digest_rel}")
            seen_digest.add(digest_rel)
            digest_path = (topic_dir / digest_rel).resolve()
            if not digest_path.exists():
                errors.append(f"sources.md: digest path not found: {digest_rel}")
                stats["sources_missing_digests"] += 1

        dk = _normalize_date_key(date_raw)
        if dk:
            date_keys.append(dk)
        else:
            warnings.append(f"sources.md: non-standard date '{date_raw}' (prefer YYYY-MM-DD or YYYY)")

    # Newest-first is recommended for sources.md; only flag when we have comparable keys.
    comparable = [d for d in date_keys if d]
    if comparable and comparable != sorted(comparable, reverse=True):
        stats["sources_unsorted"] = True
        warnings.append("sources.md: rows are not sorted by date (recommended: newest first)")

    return errors, warnings, stats


def _validate_timeline(topic_dir: Path) -> tuple[list[str], list[str], dict[str, Any]]:
    errors: list[str] = []
    warnings: list[str] = []
    stats: dict[str, Any] = {"timeline_entries": 0, "timeline_unsorted": False}

    path = topic_dir / "timeline.md"
    if not path.exists():
        errors.append(f"missing timeline.md: {path}")
        return errors, warnings, stats

    lines = path.read_text(encoding="utf-8").splitlines()
    dates: list[str] = []
    for ln in lines:
        m = re.match(r"^- (\d{4}-\d{2}-\d{2})：", ln.strip())
        if not m:
            continue
        dates.append(m.group(1))
    stats["timeline_entries"] = len(dates)
    if dates and dates != sorted(dates):
        stats["timeline_unsorted"] = True
        warnings.append("timeline.md: entries are not sorted by date (recommended: oldest first)")
    return errors, warnings, stats


def _find_claim_ledger_table(lines: list[str]) -> tuple[int | None, list[str] | None]:
    for i, ln in enumerate(lines):
        if ln.strip() == "## Claim Ledger（断言清单，建议用于投研/行业研究）" or ln.strip() == "## Claim Ledger":
            # Search forward for the next table header row.
            for j in range(i + 1, min(len(lines), i + 80)):
                if _is_table_row(lines[j]) and "claim" in lines[j]:
                    header = [c.strip() for c in lines[j].strip().strip("|").split("|")]
                    return j, header
            return i, None
    return None, None


def _validate_digest(digest_path: Path, *, topic_id: str) -> tuple[list[str], list[str], dict[str, Any]]:
    errors: list[str] = []
    warnings: list[str] = []
    stats: dict[str, Any] = {"has_frontmatter": False, "has_claim_ledger": False, "has_claim_id": False}

    raw = digest_path.read_text(encoding="utf-8")
    fm, body = _parse_frontmatter(raw)
    if fm:
        stats["has_frontmatter"] = True
    else:
        errors.append(f"digest missing frontmatter: {digest_path}")
        return errors, warnings, stats

    got_topic = str(fm.get("topic_id") or "").strip()
    if not got_topic:
        warnings.append(f"digest missing topic_id frontmatter: {digest_path.name}")
    elif got_topic != topic_id:
        errors.append(f"digest topic_id mismatch: {digest_path.name} frontmatter={got_topic} dir={topic_id}")

    lines = body.splitlines()
    idx, header = _find_claim_ledger_table(lines)
    if idx is None:
        warnings.append(f"digest missing Claim Ledger section: {digest_path.name}")
        return errors, warnings, stats
    stats["has_claim_ledger"] = True

    if not header:
        warnings.append(f"digest Claim Ledger table header not found: {digest_path.name}")
        return errors, warnings, stats

    header_norm = [h.strip().lower().replace(" ", "") for h in header]
    required = {"claim", "核验状态", "来源/证据（url/出处/时间戳/帧）"}
    if "claim" not in header_norm:
        warnings.append(f"digest Claim Ledger missing 'claim' column: {digest_path.name}")

    has_claim_id = "claim_id" in header_norm or "claimid" in header_norm
    stats["has_claim_id"] = has_claim_id
    if not has_claim_id:
        warnings.append(f"digest Claim Ledger missing claim_id (recommended): {digest_path.name}")

    # If claim_id exists, ensure uniqueness for non-empty ids.
    if has_claim_id:
        claim_id_idx = header_norm.index("claim_id") if "claim_id" in header_norm else header_norm.index("claimid")
        # Walk table rows until it ends.
        seen: set[str] = set()
        for ln in lines[idx + 2 :]:
            if not _is_table_row(ln):
                break
            cells = [c.strip() for c in ln.strip().strip("|").split("|")]
            if claim_id_idx >= len(cells):
                continue
            cid = cells[claim_id_idx].strip("`").strip()
            if not cid:
                continue
            if cid in seen:
                warnings.append(f"digest duplicate claim_id '{cid}': {digest_path.name}")
            seen.add(cid)

    return errors, warnings, stats


def _validate_topic(topic_dir: Path) -> ValidationResult:
    topic_id = topic_dir.name
    errors: list[str] = []
    warnings: list[str] = []
    stats: dict[str, Any] = {
        "topic_dir": str(topic_dir),
        "digests": 0,
        "digests_with_claim_ledger": 0,
        "digests_with_claim_id": 0,
    }

    required = ["overview.md", "framework.md", "sources.md", "timeline.md", "open_questions.md"]
    for name in required:
        if not (topic_dir / name).exists():
            errors.append(f"missing required file: {name}")

    digests_dir = topic_dir / "digests"
    if not digests_dir.exists():
        errors.append("missing digests/ directory")
        digests: list[Path] = []
    else:
        digests = sorted(digests_dir.glob("*.md"), key=lambda p: p.name)
    stats["digests"] = len(digests)

    for digest in digests:
        d_err, d_warn, d_stats = _validate_digest(digest, topic_id=topic_id)
        errors.extend(d_err)
        warnings.extend(d_warn)
        if d_stats.get("has_claim_ledger"):
            stats["digests_with_claim_ledger"] += 1
        if d_stats.get("has_claim_id"):
            stats["digests_with_claim_id"] += 1

    s_err, s_warn, s_stats = _validate_sources(topic_dir, topic_id=topic_id)
    errors.extend(s_err)
    warnings.extend(s_warn)
    stats.update(s_stats)

    t_err, t_warn, t_stats = _validate_timeline(topic_dir)
    errors.extend(t_err)
    warnings.extend(t_warn)
    stats.update(t_stats)

    overview_path = topic_dir / "overview.md"
    if overview_path.exists():
        overview_text = overview_path.read_text(encoding="utf-8")
        if "digests/" not in overview_text:
            warnings.append("overview.md: no digest references found (recommended to cite digests)")

    ok = not errors
    return ValidationResult(topic_id=topic_id, ok=ok, errors=errors, warnings=warnings, stats=stats)


def _emit_human(result: ValidationResult) -> None:
    status = "OK" if result.ok else "FAIL"
    sys.stdout.write(f"[{status}] topic={result.topic_id}\n")
    for e in result.errors:
        sys.stdout.write(f"  - ERROR: {e}\n")
    for w in result.warnings:
        sys.stdout.write(f"  - WARN: {w}\n")
    sys.stdout.write(f"  - stats: {json.dumps(result.stats, ensure_ascii=False)}\n")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Validate a topic archive for basic quality gates.")
    parser.add_argument("topic_id", nargs="?", help="Topic id (directory name under topic-root).")
    parser.add_argument("--topic-root", default="archives/topics", help="Topic root (default: archives/topics)")
    parser.add_argument("--all", action="store_true", help="Validate all topics under topic-root.")
    parser.add_argument("--json", action="store_true", help="Print JSON only.")
    parser.add_argument("--out", help="Write JSON result to a file.")
    parser.add_argument("--fail-on-warn", action="store_true", help="Exit non-zero if any warnings.")
    args = parser.parse_args(argv)

    topic_root = Path(args.topic_root).expanduser().resolve()
    if not topic_root.exists():
        raise SystemExit(f"topic-root not found: {topic_root}")

    results: list[ValidationResult] = []
    if args.all:
        for child in sorted(topic_root.iterdir()):
            if not child.is_dir():
                continue
            if not _TOPIC_ID_RE.match(child.name):
                continue
            results.append(_validate_topic(child))
    else:
        tid = (args.topic_id or "").strip()
        if not tid or not _TOPIC_ID_RE.match(tid):
            raise SystemExit("topic_id required (or use --all)")
        results.append(_validate_topic((topic_root / tid).resolve()))

    payload = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "topic_root": str(topic_root),
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
