#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import bisect


REPO_ROOT = Path(__file__).resolve().parents[1]


def _die(msg: str, code: int = 2) -> None:
    raise SystemExit(f"{msg}\n(exit {code})")


def _read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _maybe_parse_upload_date(upload_date: Optional[str]) -> str:
    if not upload_date:
        return ""
    s = str(upload_date).strip()
    if re.fullmatch(r"\d{8}", s):
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return s


def _safe_slug(value: str, max_len: int = 80) -> str:
    s = str(value).strip().lower()
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r"[^0-9a-z_\\-]+", "", s)
    s = s.strip("_-")
    return (s or "video")[:max_len]


def _extract_bvid_from_path(video_path: Path) -> Optional[str]:
    m = re.search(r"(BV[0-9A-Za-z]+)", video_path.name)
    return m.group(1) if m else None


def _find_info_json(video_path: Path) -> Optional[Path]:
    candidate = video_path.with_suffix(".info.json")
    if candidate.exists():
        return candidate
    # fallback: find by bvid in same dir
    bvid = _extract_bvid_from_path(video_path)
    if not bvid:
        return None
    matches = sorted(video_path.parent.glob(f"*_{bvid}.info.json"))
    return matches[0] if matches else None


def _read_lines(path: Path) -> List[str]:
    return path.read_text(encoding="utf-8", errors="replace").splitlines()


def _parse_evidence_compact(evidence_compact_md: Path) -> Tuple[List[str], List[str]]:
    lines = _read_lines(evidence_compact_md)
    transcript: List[str] = []
    ocr: List[str] = []
    section = None
    for ln in lines:
        if ln.startswith("## Transcript Highlights"):
            section = "transcript"
            continue
        if ln.startswith("## OCR Numeric Hits"):
            section = "ocr"
            continue
        if section == "transcript" and ln.startswith("- ["):
            transcript.append(ln)
        if section == "ocr" and (ln.startswith("- [") or ln.startswith("  - ")):
            ocr.append(ln)
    return transcript, ocr


@dataclass(frozen=True)
class MetricRow:
    source: str
    start_sec: float
    end_sec: Optional[float]
    timecode: str
    frame_file: str
    text: str
    confidence: Optional[float]
    score: int


def _coerce_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except Exception:
        return None


def _seconds_to_timecode(seconds: float) -> str:
    if seconds < 0:
        seconds = 0
    ms = int(round(seconds * 1000))
    s, ms = divmod(ms, 1000)
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def _normalize_text(text: str) -> str:
    t = str(text or "").strip().lower()
    t = re.sub(r"\s+", "", t)
    t = t.replace("：", ":")
    return t


def _clean_asr_text(text: str) -> str:
    t = str(text or "").strip()
    t = re.sub(r"\s+", " ", t)
    # Light cleanup for common ASR artifacts without changing meaning.
    t = t.replace(" ,", ",").replace(" 。", "。").replace(" ，", "，")
    return t.strip()


def _clamp(text: str, n: int) -> str:
    s = str(text or "").strip()
    if len(s) <= n:
        return s
    return s[: max(0, n - 1)].rstrip() + "…"


def _focus_around_digit(text: str, *, max_len: int = 140) -> str:
    c = _clean_asr_text(text)
    if len(c) <= max_len:
        return c
    m = re.search(r"\d", c)
    if not m:
        return _clamp(c, max_len)
    i = m.start()
    start = max(0, i - 50)
    end = min(len(c), i + 110)
    snippet = c[start:end].strip()
    if start > 0:
        snippet = "…" + snippet
    if end < len(c):
        snippet = snippet + "…"
    return _clamp(snippet, max_len)


def _looks_date_like_only(claim: str) -> bool:
    c = claim.replace(" ", "")
    if re.search(r"\d{4}年\d{1,2}月\d{1,2}(日|号)?", c):
        return True
    if re.search(r"\d{1,2}月\d{1,2}(日|号)", c):
        return True
    if re.search(r"\d{1,2}月份", c):
        return True
    if re.search(r"\d{1,2}点\d{1,2}分", c):
        return True
    return False


def _has_numeric_unit(text: str) -> bool:
    c = text
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


def _has_compact_numeric_fact(text: str) -> bool:
    c = text.replace(" ", "")
    return bool(
        re.search(
            r"\d+(?:\.\d+)?(?:[%％]|GB|TB|MB|TOPS|bps|GHz|nm|倍|亿|万|千|百|元|美元|USD|CNY|T(?![A-Za-z]))",
            c,
            flags=re.IGNORECASE,
        )
    )


def _has_domain_keyword(text: str) -> bool:
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
        "A100",
        "H100",
        "NVLink",
        "GPU",
        "AI",
        "推理",
        "训练",
        "国产",
        "替代",
    ]
    return any(k in text for k in keywords)


def _is_noise_ocr_line(text: str) -> bool:
    t = str(text).strip()
    if not t:
        return True
    compact = t.replace(" ", "")
    if compact.startswith("录制时间") or compact.startswith("录制日期"):
        return True
    if re.match(r"^[：:]*\d{1,2}月\d{1,2}(号|日)$", compact):
        return True
    if re.match(r"^[：:]*\d{4}年\d{1,2}月\d{1,2}(号|日)?$", compact):
        return True
    if re.search(r"\d{4}[./-]\d{1,2}[./-]\d{1,2}更新", compact):
        return True
    if "更新日期" in compact or compact.startswith("更新"):
        return True
    if "执业编号" in compact or "证书编号" in compact:
        return True
    if "免责声明" in compact or "仅供参考" in compact or "不构成投资建议" in compact:
        return True
    if "理财有风险" in compact or "投资需谨慎" in compact or "风险提示" in compact:
        return True
    if re.match(r"^科技\\d+", compact):
        return True
    return False


def _score_snippet(text: str, *, allow_no_digits: bool) -> int:
    c = text.strip()
    if not c:
        return -10_000

    has_digits = bool(re.search(r"\d", c))
    if not has_digits and not allow_no_digits:
        return -10_000
    if has_digits and _looks_date_like_only(c) and not _has_numeric_unit(c):
        return -10_000

    score = 0
    if has_digits:
        score += 2
    if _has_compact_numeric_fact(c):
        score += 10
    if _has_domain_keyword(c):
        score += 6
    if any(k in c for k in ["影响", "结论", "所以", "建议", "风险", "逻辑", "判断", "预计", "预期", "怎么看"]):
        score += 2

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

    if c.count(",") + c.count("，") + c.count("、") >= 6:
        score -= 3
    if c.count("。") + c.count(".") >= 3:
        score -= 2
    return score


THESIS_MARKERS = [
    "我认为",
    "我們認為",
    "我们认为",
    "核心",
    "最重要",
    "关键",
    "结论",
    "邏輯",
    "逻辑",
    "因此",
    "所以",
    "必须",
    "一定要",
    "风险",
    "機會",
    "机会",
    "催化",
    "优势",
    "劣势",
    "问题",
    "限制",
]


def _score_thesis(text: str) -> int:
    base = _score_snippet(text, allow_no_digits=True)
    if base < -1000:
        return base
    bonus = 0
    for m in THESIS_MARKERS:
        if m in text:
            bonus += 3
    if "太空算力" in text or "商业航天" in text:
        bonus += 3
    return base + bonus


def _read_key_metrics(key_metrics_csv: Path, limit: int = 0) -> List[MetricRow]:
    if not key_metrics_csv.exists():
        return []
    rows: List[MetricRow] = []
    with key_metrics_csv.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for idx, r in enumerate(reader):
            if limit and idx >= limit:
                break
            text = str(r.get("text") or "").strip()
            if not text:
                continue
            source = str(r.get("source") or "")
            start_sec = _coerce_float(r.get("start_sec")) or 0.0
            end_sec = _coerce_float(r.get("end_sec"))
            timecode = str(r.get("timecode") or "")
            frame_file = str(r.get("frame_file") or "")
            confidence = _coerce_float(r.get("score"))
            score = _score_snippet(text, allow_no_digits=(source == "asr"))
            rows.append(
                MetricRow(
                    source=source,
                    start_sec=float(start_sec),
                    end_sec=float(end_sec) if end_sec is not None else None,
                    timecode=timecode,
                    frame_file=frame_file,
                    text=text,
                    confidence=confidence,
                    score=score,
                )
            )
    return rows


def _unique_by_text(rows: Iterable[MetricRow], limit: int) -> List[MetricRow]:
    out: List[MetricRow] = []
    seen: set[str] = set()
    for r in rows:
        t = r.text.strip()
        if not t or t in seen:
            continue
        seen.add(t)
        out.append(r)
        if len(out) >= limit:
            break
    return out


def _greedy_bucket_select(
    rows: List[MetricRow],
    *,
    bucket_size_sec: int,
    per_bucket: int,
    limit: int,
    min_score: int,
) -> List[MetricRow]:
    ranked = sorted(rows, key=lambda r: (r.score, r.start_sec), reverse=True)
    selected: List[MetricRow] = []
    bucket_counts: Dict[int, int] = {}
    seen: set[str] = set()
    for r in ranked:
        if r.score < min_score:
            continue
        key = _normalize_text(r.text)
        if not key or key in seen:
            continue
        bucket = int(max(0.0, float(r.start_sec)) // float(bucket_size_sec))
        if bucket_counts.get(bucket, 0) >= per_bucket:
            continue
        bucket_counts[bucket] = bucket_counts.get(bucket, 0) + 1
        seen.add(key)
        selected.append(r)
        if len(selected) >= limit:
            break
    selected.sort(key=lambda r: r.start_sec)
    return selected


def _build_asr_chunks(evidence: Dict[str, Any]) -> List[MetricRow]:
    transcript = evidence.get("transcript") or []
    if not isinstance(transcript, list) or not transcript:
        return []

    max_chars = 260
    max_sec = 32.0
    gap_sec = 1.2

    chunks: List[MetricRow] = []
    cur_texts: List[str] = []
    cur_start: Optional[float] = None
    cur_end: Optional[float] = None
    prev_end: Optional[float] = None

    def flush() -> None:
        nonlocal cur_texts, cur_start, cur_end, prev_end
        if cur_start is None or cur_end is None or not cur_texts:
            cur_texts = []
            cur_start = None
            cur_end = None
            prev_end = None
            return
        text = _clean_asr_text(" ".join(cur_texts))
        if len(text) >= 16:
            tc = f"{_seconds_to_timecode(cur_start)}-{_seconds_to_timecode(cur_end)}"
            chunks.append(
                MetricRow(
                    source="asr_chunk",
                    start_sec=float(cur_start),
                    end_sec=float(cur_end),
                    timecode=tc,
                    frame_file="",
                    text=text,
                    confidence=None,
                    score=_score_thesis(text),
                )
            )
        cur_texts = []
        cur_start = None
        cur_end = None
        prev_end = None

    for seg in transcript:
        if not isinstance(seg, dict):
            continue
        text = str(seg.get("text") or "").strip()
        if not text:
            continue
        start = _coerce_float(seg.get("start")) or 0.0
        end = _coerce_float(seg.get("end")) or start
        if cur_start is None:
            cur_start = float(start)
            cur_end = float(end)
            prev_end = float(end)
            cur_texts = [text]
            continue

        assert prev_end is not None
        cur_len = len(_clean_asr_text(" ".join(cur_texts)))
        if (start - prev_end) > gap_sec or (end - cur_start) > max_sec or (cur_len + len(text)) > max_chars:
            flush()
            cur_start = float(start)
            cur_end = float(end)
            prev_end = float(end)
            cur_texts = [text]
            continue

        cur_texts.append(text)
        cur_end = float(end)
        prev_end = float(end)

    flush()
    return chunks


def _context_chunk_for_time(chunks: List[MetricRow], t: float) -> Optional[MetricRow]:
    if not chunks:
        return None
    starts = [c.start_sec for c in chunks]
    i = bisect.bisect_right(starts, t) - 1
    if i < 0:
        return chunks[0]
    c = chunks[i]
    if c.end_sec is not None and t <= float(c.end_sec) + 1.0:
        return c
    if i + 1 < len(chunks):
        return chunks[i + 1]
    return c


def _load_evidence_rows(out_dir: Path) -> Tuple[List[MetricRow], Dict[str, Any]]:
    evidence_path = out_dir / "evidence.json"
    if not evidence_path.exists():
        return [], {}
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))

    rows: List[MetricRow] = []
    transcript = evidence.get("transcript") or []
    if isinstance(transcript, list):
        for seg in transcript:
            if not isinstance(seg, dict):
                continue
            text = str(seg.get("text") or "").strip()
            if not text:
                continue
            start = _coerce_float(seg.get("start")) or 0.0
            end = _coerce_float(seg.get("end"))
            tc = f"{_seconds_to_timecode(start)}-{_seconds_to_timecode(end or start)}"
            rows.append(
                MetricRow(
                    source="asr",
                    start_sec=float(start),
                    end_sec=float(end) if end is not None else None,
                    timecode=tc,
                    frame_file="",
                    text=text,
                    confidence=None,
                    score=_score_snippet(text, allow_no_digits=True),
                )
            )

    frames = evidence.get("frames") or []
    if isinstance(frames, list):
        # Pre-filter OCR noise and remove persistent overlays.
        per_frame_lines: List[Tuple[float, str, str, List[Tuple[str, Optional[float]]]]] = []
        for fr in frames:
            if not isinstance(fr, dict):
                continue
            approx_sec = _coerce_float(fr.get("approx_time_sec")) or 0.0
            approx_tc = str(fr.get("approx_timecode") or _seconds_to_timecode(approx_sec))
            frame_path = str(fr.get("frame_path") or "")
            frame_file = Path(frame_path).name if frame_path else ""
            ocr_lines = fr.get("ocr_numeric_lines") or []
            kept: List[Tuple[str, Optional[float]]] = []
            if isinstance(ocr_lines, list):
                for ln in ocr_lines:
                    if not isinstance(ln, dict):
                        continue
                    text = str(ln.get("text") or "").strip()
                    if not text or not re.search(r"\d", text) or _is_noise_ocr_line(text):
                        continue
                    kept.append((text, _coerce_float(ln.get("score"))))
            if kept:
                per_frame_lines.append((float(approx_sec), approx_tc, frame_file, kept))

        if per_frame_lines:
            total_frames = len(per_frame_lines)
            key_counts: Dict[str, int] = {}
            key_example: Dict[str, str] = {}
            for _sec, _tc, _ff, lines in per_frame_lines:
                keys = set()
                for text, _conf in lines:
                    k = _normalize_text(text)
                    if not k:
                        continue
                    key_example.setdefault(k, text)
                    keys.add(k)
                for k in keys:
                    key_counts[k] = key_counts.get(k, 0) + 1

            persistent_keys = {
                k
                for k, c in key_counts.items()
                if (c / max(1, total_frames)) >= 0.80 and not _has_compact_numeric_fact(key_example.get(k, ""))
            }

            seen_keys: set[str] = set()
            for sec, tc, frame_file, lines in per_frame_lines:
                for text, conf in lines:
                    k = _normalize_text(text)
                    if not k or k in persistent_keys or k in seen_keys:
                        continue
                    seen_keys.add(k)
                    rows.append(
                        MetricRow(
                            source="frame_ocr",
                            start_sec=float(sec),
                            end_sec=None,
                            timecode=str(tc),
                            frame_file=str(frame_file),
                            text=str(text),
                            confidence=conf,
                            score=_score_snippet(str(text), allow_no_digits=False),
                        )
                    )

    return rows, evidence


def _render_digest(
    *,
    title: str,
    source_url: str,
    source_path: str,
    published_at: str,
    duration_sec: Optional[float],
    asr_coverage_sec: Optional[float],
    transcript_segments: int,
    frames_extracted: Optional[int],
    ocr_frames_with_hits: Optional[int],
    analysis_warnings: List[str],
    topic_id: str,
    tags: List[str],
    entities: List[str],
    core_points: List[MetricRow],
    key_points: List[MetricRow],
    claim_rows: List[MetricRow],
) -> str:
    def bullet_block(items: List[str], *, max_items: int) -> str:
        items = [i for i in items if i.strip()]
        items = items[:max_items]
        if not items:
            return "- (空)"
        return "\n".join(f"- {i}" if not i.startswith("- ") else i for i in items)

    core = []
    for r in core_points[:12]:
        if r.end_sec is not None:
            pointer = f"{_seconds_to_timecode(r.start_sec)}-{_seconds_to_timecode(r.end_sec)}"
        else:
            pointer = r.timecode or _seconds_to_timecode(r.start_sec)
        core.append(f"[{pointer}] {r.text}".strip())

    evidence_lines: List[str] = []
    for r in key_points[:20]:
        pointer = r.timecode or _seconds_to_timecode(r.start_sec)
        if r.source == "frame_ocr" and r.frame_file:
            pointer = f"{pointer} {r.frame_file}".strip()
        evidence_lines.append(f"[{r.source}] {pointer} {r.text}".strip())

    claim_table_lines = ["| # | claim | 影响范围 | 置信度 | 核验状态 | 来源/证据（URL/出处/时间戳/帧） | 建议核验动作 |", "|---|-------|----------|--------|----------|----------------------------------|--------------|"]
    for i, r in enumerate(claim_rows, start=1):
        pointer = r.timecode or _seconds_to_timecode(r.start_sec)
        if r.source == "frame_ocr" and r.frame_file:
            pointer = f"{pointer} {r.frame_file}".strip()
        src = f"{source_url} ; {pointer}".strip(" ;")
        claim = _focus_around_digit(r.text, max_len=120) if r.source != "frame_ocr" else _clamp(r.text, 120)
        claim_table_lines.append(f"| {i} | {claim} | 中 | 中 | unverified | {src} | 对照原视频画面/字幕复核 |")

    quality_lines: List[str] = []
    if duration_sec is not None:
        quality_lines.append(f"- duration_sec: `{duration_sec:.2f}`")
    if asr_coverage_sec is not None:
        quality_lines.append(f"- asr_end_sec: `{asr_coverage_sec:.2f}`")
        if duration_sec is not None and duration_sec > 0:
            ratio = max(0.0, min(1.0, asr_coverage_sec / duration_sec))
            quality_lines.append(f"- asr_coverage: `{ratio:.2%}`")
    quality_lines.append(f"- transcript_segments: `{transcript_segments}`")
    if frames_extracted is not None:
        quality_lines.append(f"- frames_extracted: `{frames_extracted}`")
    if ocr_frames_with_hits is not None:
        quality_lines.append(f"- ocr_frames_with_hits: `{ocr_frames_with_hits}`")
    if analysis_warnings:
        quality_lines.append(f"- warnings: {json.dumps(analysis_warnings[:6], ensure_ascii=False)}")

    doc = f"""---
title: {json.dumps(title, ensure_ascii=False)}
source_type: "video"
source_url: {json.dumps(source_url, ensure_ascii=False)}
source_path: {json.dumps(source_path, ensure_ascii=False)}
published_at: {json.dumps(published_at, ensure_ascii=False)}
topic_id: {json.dumps(topic_id, ensure_ascii=False)}
tags: {json.dumps(tags, ensure_ascii=False)}
entities: {json.dumps(entities, ensure_ascii=False)}
---

## 运行质量（自动）

{bullet_block(quality_lines, max_items=12)}

## 核心观点

{bullet_block(core, max_items=7)}

## 关键证据/数据点（可引用来源）

{bullet_block(evidence_lines, max_items=12)}

## 反驳点/局限性

- 本 digest 为“机器流水线产物”：核心观点来自 ASR/OCR 片段抽取，可能存在误识别；所有数字/结论需回看原视频核验。
- 若视频为情绪/观点类口播，ASR 片段可能缺少上下文，需结合完整转写（`transcript.json`）复核。

## 对主题框架的影响

- (待整理：将该视频的观点/数据归并到主题的框架维度)

## 建议写入 mem0 的长期结论候选（可选）

- (待人工复核后再写入)

## Claim Ledger（断言清单，建议用于投研/行业研究）

{chr(10).join(claim_table_lines)}
"""
    return doc.rstrip() + "\n"


def _append_sources_row(sources_md: Path, *, date: str, typ: str, title: str, link: str, digest_rel: str) -> None:
    lines = _read_lines(sources_md)
    row = f"| {date} | {typ} | {title} | {link} | {digest_rel} |"
    # Append after the header separator line (keep template row if still empty).
    out: List[str] = []
    inserted = False
    for ln in lines:
        if ln.strip() == "|  |  |  |  |  |":
            # drop placeholder row
            continue
        out.append(ln)
    out.append(row)
    sources_md.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(description="Generate digest markdowns from a bilibili_up_batch run record.")
    ap.add_argument("--run", required=True, help="Path to state/runs/bilibili_up_batch/*.json")
    ap.add_argument("--topic", default="", help="Optional topic_id to write into archives/topics/<topic>/digests/")
    ap.add_argument("--write-sources", action="store_true", help="Append rows to topic sources.md (requires --topic).")
    args = ap.parse_args(argv)

    run_path = Path(args.run)
    run = _read_json(run_path)
    topic_id = str(args.topic).strip()
    run_params = run.get("params") if isinstance(run.get("params"), dict) else {}
    run_out_dir = str(run_params.get("out_dir") or "").strip()
    run_out_dir_p = Path(run_out_dir) if run_out_dir else None

    if topic_id:
        digest_dir = REPO_ROOT / "archives" / "topics" / topic_id / "digests"
        sources_md = REPO_ROOT / "archives" / "topics" / topic_id / "sources.md"
        if args.write_sources and not sources_md.exists():
            _die(f"sources.md not found: {sources_md}")
    else:
        digest_dir = REPO_ROOT / "exports" / "digests"
        sources_md = None

    digest_dir.mkdir(parents=True, exist_ok=True)

    items = run.get("items") or []
    if not isinstance(items, list):
        _die("run.items invalid")

    written: List[Path] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        analysis = item.get("analysis") or {}
        if not isinstance(analysis, dict) or analysis.get("is_error") or analysis.get("skipped"):
            continue
        out_dir = analysis.get("out_dir")
        if not out_dir:
            continue
        out_dir_p = Path(str(out_dir))
        evidence_compact_md = out_dir_p / "evidence_compact.md"
        key_metrics_csv = out_dir_p / "key_metrics.csv"

        download = item.get("download") or {}
        video_path_s = download.get("video_path") or ""
        video_path = Path(video_path_s) if video_path_s else None
        bvid = str(item.get("bvid") or "") or (_extract_bvid_from_path(video_path) if video_path else "")
        if not bvid:
            continue
        if video_path is None and run_out_dir_p is not None:
            candidates = sorted(run_out_dir_p.glob(f"*_{bvid}.*"))
            candidates = [p for p in candidates if p.is_file() and p.suffix.lower() in {".mp4", ".mkv", ".flv", ".webm"}]
            video_path = candidates[0] if candidates else None

        info_json = _find_info_json(video_path) if video_path else None
        info = _read_json(info_json) if info_json and info_json.exists() else {}
        title = str(info.get("title") or bvid)
        published_at = _maybe_parse_upload_date(info.get("upload_date"))
        source_url = str(info.get("webpage_url") or f"https://www.bilibili.com/video/{bvid}")

        date_for_name = published_at or datetime.now().strftime("%Y-%m-%d")
        filename = f"{date_for_name}_bilibili_{bvid}.md"
        digest_path = digest_dir / filename

        transcript_lines: List[str] = []
        if evidence_compact_md.exists():
            transcript_lines, _ocr_lines = _parse_evidence_compact(evidence_compact_md)
        metrics, evidence = _load_evidence_rows(out_dir_p)
        if not metrics:
            metrics = _read_key_metrics(key_metrics_csv, limit=0)

        asr_chunks: List[MetricRow] = _build_asr_chunks(evidence) if evidence else []

        # Core points: pick thesis-like ASR chunks across the video timeline.
        if asr_chunks:
            core_points_raw = _greedy_bucket_select(asr_chunks, bucket_size_sec=600, per_bucket=1, limit=7, min_score=8)
            if not core_points_raw:
                core_points_raw = _greedy_bucket_select(asr_chunks, bucket_size_sec=900, per_bucket=1, limit=7, min_score=6)
            core_points = [
                MetricRow(
                    source=r.source,
                    start_sec=r.start_sec,
                    end_sec=r.end_sec,
                    timecode=r.timecode,
                    frame_file=r.frame_file,
                    text=_clamp(_clean_asr_text(r.text), 180),
                    confidence=r.confidence,
                    score=r.score,
                )
                for r in core_points_raw
            ]
        else:
            core_candidates = [r for r in metrics if r.source == "asr"]
            core_points = _greedy_bucket_select(core_candidates, bucket_size_sec=180, per_bucket=1, limit=7, min_score=6)

        if not core_points and transcript_lines:
            core_points = [
                MetricRow(
                    source="asr",
                    start_sec=0.0,
                    end_sec=None,
                    timecode="",
                    frame_file="",
                    text=_clamp(ln.replace("- ", "", 1), 180),
                    confidence=None,
                    score=0,
                )
                for ln in transcript_lines[:7]
            ]

        # Key points: prefer compact numeric facts, but keep context by using ASR chunks.
        key_candidates: List[MetricRow] = []
        if asr_chunks:
            for c in asr_chunks:
                if not re.search(r"\d", c.text):
                    continue
                if _looks_date_like_only(c.text) and not _has_numeric_unit(c.text):
                    continue
                key_candidates.append(
                    MetricRow(
                        source="asr_chunk",
                        start_sec=c.start_sec,
                        end_sec=c.end_sec,
                        timecode=c.timecode,
                        frame_file="",
                        text=_focus_around_digit(c.text, max_len=180),
                        confidence=None,
                        score=_score_snippet(c.text, allow_no_digits=False),
                    )
                )

        # OCR rows (if any) already pre-filtered in evidence; keep them as candidates too.
        for r in metrics:
            if r.source != "frame_ocr":
                continue
            if not re.search(r"\d", r.text):
                continue
            if _looks_date_like_only(r.text) and not _has_numeric_unit(r.text):
                continue
            key_candidates.append(r)

        key_points = _greedy_bucket_select(key_candidates, bucket_size_sec=600, per_bucket=2, limit=12, min_score=6)

        # Claim rows: select highest-scoring candidates (numeric facts first).
        claim_candidates = [r for r in key_candidates if _has_compact_numeric_fact(r.text) or _has_domain_keyword(r.text)]
        claim_candidates.sort(key=lambda r: (r.score, r.start_sec), reverse=True)
        claim_rows = _unique_by_text(claim_candidates, limit=10)

        tags = ["bilibili", f"bvid:{bvid}"]
        info_tags = info.get("tags") if isinstance(info, dict) else None
        if isinstance(info_tags, list):
            tags.extend([str(t) for t in info_tags if str(t).strip()])
        entities = []

        duration_sec = None
        asr_end_sec = None
        transcript_segments = 0
        frames_extracted = None
        ocr_frames_with_hits = None
        analysis_warnings: List[str] = []
        try:
            duration_sec = _coerce_float((evidence or {}).get("video", {}).get("duration_sec"))
        except Exception:
            pass
        if evidence and isinstance(evidence.get("transcript"), list):
            segs = [s for s in evidence.get("transcript") if isinstance(s, dict)]
            transcript_segments = len(segs)
            ends = [float(s.get("end")) for s in segs if isinstance(s.get("end"), (int, float))]
            asr_end_sec = max(ends) if ends else None
        if evidence and isinstance(evidence.get("stats"), dict):
            frames_extracted = _coerce_float(evidence.get("stats", {}).get("frames_extracted"))
            if frames_extracted is not None:
                frames_extracted = int(frames_extracted)
        if evidence and isinstance(evidence.get("stats"), dict):
            ocr_frames_with_hits = _coerce_float(evidence.get("stats", {}).get("ocr_frames_with_numeric_hits"))
            if ocr_frames_with_hits is not None:
                ocr_frames_with_hits = int(ocr_frames_with_hits)
        if evidence and isinstance(evidence.get("warnings"), list):
            analysis_warnings = [str(w) for w in evidence.get("warnings") if str(w).strip()]

        digest_text = _render_digest(
            title=title,
            source_url=source_url,
            source_path=str(video_path) if video_path else "",
            published_at=published_at,
            duration_sec=duration_sec,
            asr_coverage_sec=asr_end_sec,
            transcript_segments=transcript_segments,
            frames_extracted=frames_extracted,
            ocr_frames_with_hits=ocr_frames_with_hits,
            analysis_warnings=analysis_warnings,
            topic_id=topic_id,
            tags=tags,
            entities=entities,
            core_points=core_points,
            key_points=key_points,
            claim_rows=claim_rows,
        )
        digest_path.write_text(digest_text, encoding="utf-8")
        written.append(digest_path)

        if topic_id and args.write_sources and sources_md is not None:
            digest_rel = f"digests/{digest_path.name}"
            _append_sources_row(
                sources_md,
                date=published_at,
                typ="video",
                title=title.replace("|", " "),
                link=source_url,
                digest_rel=digest_rel,
            )

    print(json.dumps({"written": [str(p) for p in written]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
