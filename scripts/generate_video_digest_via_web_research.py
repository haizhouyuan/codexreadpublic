#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from mcp_streamable_http_client import McpHttpError, mcp_http_call_tool, mcp_http_initialize


REPO_ROOT = Path(__file__).resolve().parents[1]


def _die(msg: str, code: int = 2) -> None:
    print(msg, file=sys.stderr)
    raise SystemExit(code)


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _seconds_to_timecode(seconds: float) -> str:
    if seconds < 0:
        seconds = 0
    ms = int(round(seconds * 1000))
    s, ms = divmod(ms, 1000)
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def _extract_video_path_from_manifest(manifest: Dict[str, Any]) -> Optional[Path]:
    p = str(manifest.get("video_path") or "").strip()
    if not p:
        return None
    return Path(p)


def _infer_bilibili_url_from_path(video_path: Optional[Path]) -> str:
    if not video_path:
        return ""
    m = re.search(r"(BV[0-9A-Za-z]+)", video_path.name)
    if not m:
        return ""
    return f"https://www.bilibili.com/video/{m.group(1)}"


def _infer_source_url(video_path: Optional[Path]) -> str:
    if not video_path:
        return ""
    url_txt = video_path.with_suffix(".url.txt")
    if url_txt.exists():
        try:
            return _read_text(url_txt).strip()
        except Exception:
            return ""
    return _infer_bilibili_url_from_path(video_path) or ""


def _sanitize_llm_markdown(md: str) -> str:
    s = (md or "").strip()
    # Strip fenced wrappers if the model returns ```markdown ... ```
    m = re.match(r"^```(?:markdown)?\\s*(.*?)\\s*```\\s*$", s, flags=re.DOTALL | re.IGNORECASE)
    if m:
        s = (m.group(1) or "").strip()
    return s.rstrip() + "\n"


def _validate_digest(md: str) -> List[str]:
    errs: List[str] = []
    s = (md or "").strip()
    if not s.startswith("---"):
        errs.append("missing_yaml_frontmatter")
    required = [
        "## 核心观点",
        "## 关键证据/数据点",
        "## 反驳点/局限性",
        "## 对主题框架的影响",
        "## 建议写入 mem0 的长期结论候选",
        "## Claim Ledger",
    ]
    for r in required:
        if r not in s:
            errs.append(f"missing_heading:{r}")
    if "| claim_id |" not in s:
        errs.append("missing_claim_ledger_table_header")
    return errs


def _mcp_initialize_with_retry(url: str, *, attempts: int = 3) -> Any:
    last: Exception | None = None
    for attempt in range(1, max(1, attempts) + 1):
        try:
            return mcp_http_initialize(url, client_name="codexread_video_digest", client_version="0.1", timeout_sec=30.0)
        except Exception as exc:
            last = exc
            if attempt >= attempts:
                break
            time.sleep(5)
    raise RuntimeError(f"failed to initialize MCP at {url}: {last}") from last


def _is_transient_mcp_error(msg: str) -> bool:
    s = (msg or "").lower()
    return any(
        token in s
        for token in [
            "connection refused",
            "sse stream ended without a json-rpc response",
            "locator.fill",
            "timeout",
            "target closed",
            "browser has been closed",
            "net::err",
            "ecconnreset",
        ]
    )


def _call_tool_with_retry(
    session: Any,
    *,
    url: str,
    tool_name: str,
    tool_args: Dict[str, Any],
    timeout_seconds: int,
    attempts: int = 2,
) -> Dict[str, Any]:
    last: Exception | None = None
    sess = session
    for attempt in range(1, max(1, attempts) + 1):
        try:
            return mcp_http_call_tool(
                sess,
                tool_name=tool_name,
                tool_args=tool_args,
                timeout_sec=float(timeout_seconds) + 30.0,
            )
        except McpHttpError as exc:
            last = exc
            msg = str(exc)
            if attempt >= attempts or not _is_transient_mcp_error(msg):
                break
            # Re-init the MCP session in case the previous SSE stream died mid-flight.
            time.sleep(10)
            try:
                sess = _mcp_initialize_with_retry(url, attempts=2)
            except Exception:
                pass
        except Exception as exc:
            last = exc
            if attempt >= attempts:
                break
            time.sleep(10)
    raise RuntimeError(f"{tool_name} failed: {last}") from last


def _normalize_chatgpt_output(
    md: str,
    *,
    topic_id: str,
    digest_stem: str,
    source_url: str,
    source_path: str,
    published_at: str,
) -> str:
    """
    Best-effort normalization for common ChatGPT formatting drift:
    - Missing `---` YAML markers but starts with `title:` etc
    - Headings missing `## `
    - Claim Ledger emitted as tab-separated rows instead of pipe table
    """
    raw = (md or "").strip("\n")
    if not raw.strip():
        return md

    lines = raw.splitlines()

    # 0) Extract and rebuild frontmatter deterministically (use JSON-in-YAML to stay valid).
    title = ""
    tags: List[str] = []
    entities: List[str] = []
    extracted_published_at = ""
    extracted_source_url = ""
    extracted_source_path = ""

    body_lines = list(lines)

    def _maybe_parse_json_list(value: str) -> List[str]:
        v = value.strip()
        if not v.startswith("["):
            return []
        try:
            data = json.loads(v)
        except Exception:
            return []
        if isinstance(data, list):
            out: List[str] = []
            for item in data:
                if isinstance(item, str) and item.strip():
                    out.append(item.strip())
            return out
        return []

    if body_lines and body_lines[0].strip() == "---":
        # YAML-like block until the next --- line.
        end = None
        for i in range(1, len(body_lines)):
            if body_lines[i].strip() == "---":
                end = i
                break
        if end is not None:
            fm = body_lines[1:end]
            body_lines = body_lines[end + 1 :]
            for ln in fm:
                if ":" not in ln:
                    continue
                k, v = ln.split(":", 1)
                k = k.strip()
                v = v.strip()
                if k == "title":
                    title = v.strip('"').strip()
                elif k == "published_at":
                    extracted_published_at = v.strip('"').strip()
                elif k == "tags":
                    tags = _maybe_parse_json_list(v) or tags
                elif k == "entities":
                    entities = _maybe_parse_json_list(v) or entities
                elif k == "source_url":
                    extracted_source_url = v.strip('"').strip()
                elif k == "source_path":
                    extracted_source_path = v.strip('"').strip()
        else:
            # Broken frontmatter; treat as plain body.
            body_lines = list(lines)
    elif body_lines and re.match(r"^[a-z_]+:\s*", body_lines[0].strip()):
        fm_keys = {"title", "source_type", "source_url", "source_path", "published_at", "topic_id", "tags", "entities"}
        fm: List[str] = []
        rest: List[str] = []
        in_fm = True
        for ln in body_lines:
            s = ln.strip()
            if in_fm and s and ":" in s:
                key = s.split(":", 1)[0].strip()
                if key in fm_keys:
                    fm.append(ln.rstrip())
                    continue
            in_fm = False
            rest.append(ln.rstrip())
        body_lines = rest
        for ln in fm:
            if ":" not in ln:
                continue
            k, v = ln.split(":", 1)
            k = k.strip()
            v = v.strip()
            if k == "title":
                title = v.strip('"').strip()
            elif k == "published_at":
                extracted_published_at = v.strip('"').strip()
            elif k == "tags":
                tags = _maybe_parse_json_list(v) or tags
            elif k == "entities":
                entities = _maybe_parse_json_list(v) or entities
            elif k == "source_url":
                extracted_source_url = v.strip('"').strip()
            elif k == "source_path":
                extracted_source_path = v.strip('"').strip()

    if not title:
        title = f"Video digest ({digest_stem})"

    final_source_url = (source_url or extracted_source_url or "").strip().replace("\n", "").replace("\r", "")
    final_source_path = (source_path or extracted_source_path or "").strip()
    final_published_at = (published_at or extracted_published_at or "").strip()

    # Clean obvious leaked frontmatter fragments at the start of body (broken outputs may spill keys after ---).
    fm_keys = {"title", "source_type", "source_url", "source_path", "published_at", "topic_id", "tags", "entities"}
    cleaned_body: List[str] = []
    skipping = True
    for ln in body_lines:
        s = ln.strip()
        if skipping:
            if not s or s in {'"', "“", "”"}:
                continue
            if re.match(r"^[a-z_]+:\s*", s):
                key = s.split(":", 1)[0].strip()
                if key in fm_keys:
                    continue
            skipping = False
        cleaned_body.append(ln)
    body_lines = cleaned_body

    frontmatter = "\n".join(
        [
            "---",
            f"title: {json.dumps(title, ensure_ascii=False)}",
            'source_type: "video"',
            f"source_url: {json.dumps(final_source_url, ensure_ascii=False)}",
            f"source_path: {json.dumps(final_source_path, ensure_ascii=False)}",
            f"published_at: {json.dumps(final_published_at, ensure_ascii=False)}",
            f"topic_id: {json.dumps(topic_id or '', ensure_ascii=False)}",
            f"tags: {json.dumps(tags, ensure_ascii=False)}",
            f"entities: {json.dumps(entities, ensure_ascii=False)}",
            "---",
            "",
        ]
    )

    raw = "\n".join(body_lines).strip("\n")
    lines = raw.splitlines()

    # 1) Normalize headings (add `## ` when the line matches known headings).
    heading_map = {
        "核心观点": "## 核心观点",
        "关键证据/数据点（可引用来源）": "## 关键证据/数据点（可引用来源）",
        "反驳点/局限性": "## 反驳点/局限性",
        "对主题框架的影响": "## 对主题框架的影响",
        "建议写入 mem0 的长期结论候选（可选）": "## 建议写入 mem0 的长期结论候选（可选）",
        "Claim Ledger（断言清单，建议用于投研/行业研究）": "## Claim Ledger（断言清单，建议用于投研/行业研究）",
        "Claim Ledger": "## Claim Ledger（断言清单，建议用于投研/行业研究）",
    }
    norm_lines: List[str] = []
    for ln in lines:
        s = ln.strip()
        if s in heading_map and not s.startswith("##"):
            norm_lines.append(heading_map[s])
        else:
            norm_lines.append(ln)
    raw = "\n".join(norm_lines).strip("\n")

    # 2) Normalize Claim Ledger table if present as tab-separated rows.
    lines = raw.splitlines()
    claim_start = None
    for i, ln in enumerate(lines):
        if "Claim Ledger" in ln or "断言清单" in ln:
            claim_start = i
            break
    if claim_start is not None:
        # Parse rows after the heading.
        rows: List[List[str]] = []
        for ln in lines[claim_start + 1 :]:
            if not ln.strip():
                continue
            if re.match(r"^\d+\t", ln):
                parts = [p.strip() for p in ln.split("\t")]
                if len(parts) >= 6:
                    while len(parts) < 8:
                        parts.append("")
                    rows.append(parts[:8])
                    continue
            # Continuation line: append to last cell of last row.
            if rows:
                if "\t" in ln:
                    cont = [p.strip() for p in ln.split("\t")]
                    if len(cont) >= 2:
                        evidence_add = " ".join(cont[:-1]).strip()
                        action_add = cont[-1].strip()
                        if evidence_add:
                            rows[-1][6] = (rows[-1][6] + " " + evidence_add).strip()
                        if action_add:
                            rows[-1][7] = (rows[-1][7] + " " + action_add).strip()
                    else:
                        rows[-1][6] = (rows[-1][6] + " " + ln.strip()).strip()
                else:
                    idx = 6 if not rows[-1][7].strip() else 7
                    rows[-1][idx] = (rows[-1][idx] + " " + ln.strip()).strip()

        if rows:
            header = [
                "| # | claim_id | claim | 影响范围 | 置信度 | 核验状态 | 来源/证据（URL/出处/时间戳/帧） | 建议核验动作 |",
                "|---|----------|-------|----------|--------|----------|----------------------------------|--------------|",
            ]
            md_rows: List[str] = []
            for r in rows:
                # Ensure claim_id prefix matches expectation when topic_id is empty.
                claim_id = r[1].strip()
                if not topic_id and claim_id.startswith("video_") is False:
                    claim_id = f"video_{digest_stem}_c{str(r[0]).zfill(2)}"
                cells = [r[0].strip(), claim_id, r[2].strip(), r[3].strip(), r[4].strip(), r[5].strip(), r[6].strip(), r[7].strip()]
                md_rows.append("| " + " | ".join(c.replace("\n", " ").replace("\r", " ").strip() for c in cells) + " |")

            # Replace everything after the claim heading with the normalized table.
            lines = lines[: claim_start + 1] + [""] + header + md_rows
            raw = "\n".join(lines).strip("\n")

    return (frontmatter + raw.strip() + "\n").rstrip() + "\n"


def _build_inputs(
    analysis_dir: Path,
    *,
    max_transcript_chars: int,
    include_ocr: bool,
    source_url_override: str,
) -> Tuple[Dict[str, Any], List[str], List[str]]:
    manifest_path = analysis_dir / "manifest.json"
    transcript_path = analysis_dir / "transcript.json"
    ocr_path = analysis_dir / "ocr.jsonl"
    evidence_compact_path = analysis_dir / "evidence_compact.md"

    manifest = _read_json(manifest_path) if manifest_path.exists() else {}
    video_path = _extract_video_path_from_manifest(manifest) if isinstance(manifest, dict) else None
    source_url = (source_url_override or "").strip() or _infer_source_url(video_path)

    segments: List[Dict[str, Any]] = []
    transcript: Any = _read_json(transcript_path) if transcript_path.exists() else []
    if isinstance(transcript, list):
        for seg in transcript:
            if not isinstance(seg, dict):
                continue
            start = float(seg.get("start") or 0.0)
            end = float(seg.get("end") or start)
            text = str(seg.get("text") or "").strip()
            if not text:
                continue
            segments.append({"start": start, "end": end, "text": text})

    def _score_text(text: str) -> int:
        t = text
        score = 0
        if re.search(r"\\d", t):
            score += 5
        if re.search(r"[一二三四五六七八九十百千万亿]", t):
            score += 1
        keywords = (
            "同比",
            "环比",
            "增长",
            "下降",
            "市场",
            "份额",
            "营收",
            "利润",
            "毛利",
            "指引",
            "订单",
            "渗透率",
            "成本",
            "价格",
            "空间",
            "航天",
            "卫星",
            "火箭",
            "算力",
            "液冷",
            "光模块",
        )
        markers = ("结论", "核心", "重点", "关键", "逻辑", "机会", "风险", "催化", "估值", "对比", "我们认为", "建议", "判断")
        for k in keywords:
            if k in t:
                score += 2
        for k in markers:
            if k in t:
                score += 2
        if len(t) >= 20:
            score += 1
        if len(t) >= 60:
            score += 1
        return score

    transcript_lines: List[str] = []
    if max_transcript_chars <= 0:
        for s in segments:
            transcript_lines.append(
                f"- [{_seconds_to_timecode(float(s['start']))} - {_seconds_to_timecode(float(s['end']))}] {s['text']}"
            )
    else:
        # Bucketed selection across the whole video to avoid only taking the beginning.
        duration = 0.0
        for s in segments[-50:]:
            try:
                duration = max(duration, float(s.get("end") or 0.0))
            except Exception:
                pass
        bucket_sec = 300.0  # 5 minutes
        bucket_count = int(duration // bucket_sec) + 1 if duration > 0 else 1
        bucket_count = max(1, min(bucket_count, 24))
        per_bucket_budget = max(400, int(max_transcript_chars // bucket_count))

        buckets: List[List[Dict[str, Any]]] = [[] for _ in range(bucket_count)]
        for s in segments:
            idx = int(float(s["start"]) // bucket_sec)
            if idx < 0:
                idx = 0
            if idx >= bucket_count:
                idx = bucket_count - 1
            buckets[idx].append(s)

        selected: List[Dict[str, Any]] = []
        for b in buckets:
            scored: List[Tuple[int, Dict[str, Any]]] = []
            for s in b:
                scored.append((_score_text(str(s["text"])), s))
            scored.sort(key=lambda x: (x[0], float(x[1]["start"])), reverse=True)
            used = 0
            for score, s in scored:
                if score <= 0:
                    continue
                line = f"- [{_seconds_to_timecode(float(s['start']))} - {_seconds_to_timecode(float(s['end']))}] {s['text']}"
                if used + len(line) + 1 > per_bucket_budget:
                    continue
                selected.append(s)
                used += len(line) + 1

        if not selected and segments:
            selected = segments[: min(300, len(segments))]

        selected.sort(key=lambda s: float(s["start"]))
        total = 0
        for s in selected:
            line = f"- [{_seconds_to_timecode(float(s['start']))} - {_seconds_to_timecode(float(s['end']))}] {s['text']}"
            if total + len(line) + 1 > max_transcript_chars:
                break
            transcript_lines.append(line)
            total += len(line) + 1

    ocr_lines: List[str] = []
    if include_ocr and ocr_path.exists():
        for raw in _read_text(ocr_path).splitlines():
            raw = raw.strip()
            if not raw:
                continue
            try:
                item = json.loads(raw)
            except Exception:
                continue
            if not isinstance(item, dict):
                continue
            # Support both formats:
            # - video_pipeline jsonl: approx_time_sec + numeric_lines[{text,score}]
            # - legacy jsonl: time_sec + texts[{text,conf}]
            t = float(item.get("approx_time_sec") or item.get("time_sec") or 0.0)
            frame = str(item.get("frame_file") or "")
            numeric_lines = item.get("numeric_lines")
            if isinstance(numeric_lines, list) and numeric_lines:
                for hit in numeric_lines[:5]:
                    if not isinstance(hit, dict):
                        continue
                    text = str(hit.get("text") or "").strip()
                    if not text:
                        continue
                    score = hit.get("score")
                    score_s = f"{score:.2f}" if isinstance(score, (int, float)) else ""
                    ocr_lines.append(f"- [{_seconds_to_timecode(t)}] {frame} ({score_s}) {text}".strip())
            else:
                texts = item.get("texts")
                if not isinstance(texts, list):
                    continue
                for hit in texts[:5]:
                    if not isinstance(hit, dict):
                        continue
                    text = str(hit.get("text") or "").strip()
                    if not text:
                        continue
                    conf = hit.get("conf")
                    conf_s = f"{conf:.2f}" if isinstance(conf, (int, float)) else ""
                    ocr_lines.append(f"- [{_seconds_to_timecode(t)}] {frame} ({conf_s}) {text}".strip())
            if len(ocr_lines) >= 40:
                break

    compact_md = _read_text(evidence_compact_path) if evidence_compact_path.exists() else ""

    meta: Dict[str, Any] = {
        "analysis_id": str(manifest.get("analysis_id") or analysis_dir.name) if isinstance(manifest, dict) else analysis_dir.name,
        "video_path": str(video_path) if video_path else "",
        "source_url": source_url,
        "generated_at": str(manifest.get("generated_at") or "") if isinstance(manifest, dict) else "",
        "duration_sec": "",
    }
    if isinstance(manifest, dict):
        params = manifest.get("params") if isinstance(manifest.get("params"), dict) else {}
        if isinstance(params, dict):
            meta["frame_every_sec"] = params.get("frame_every_sec")

    return meta, transcript_lines, (ocr_lines if include_ocr else [])[0:40] + (["", compact_md.strip()] if compact_md.strip() else [])


def _build_chatgpt_prompt(
    *,
    meta: Dict[str, Any],
    topic_id: str,
    digest_stem: str,
    published_at: str,
    transcript_lines: List[str],
    ocr_and_compact: List[str],
) -> str:
    source_url = str(meta.get("source_url") or "").strip()
    source_path = str(meta.get("video_path") or "").strip()
    analysis_id = str(meta.get("analysis_id") or "").strip()
    generated_at = str(meta.get("generated_at") or "").strip()
    published_at = (published_at or "").strip()

    topic_for_claim = topic_id if topic_id else "video"
    frontmatter_example = f"""---
title: \"(用一句话概括视频核心内容)\"
source_type: \"video\"
source_url: {json.dumps(source_url or "", ensure_ascii=False)}
source_path: {json.dumps(source_path or "", ensure_ascii=False)}
published_at: {json.dumps(published_at, ensure_ascii=False)}
topic_id: {json.dumps(topic_id or "", ensure_ascii=False)}
tags: []
entities: []
---"""

    claim_table_example = (
        "| # | claim_id | claim | 影响范围 | 置信度 | 核验状态 | 来源/证据（URL/出处/时间戳/帧） | 建议核验动作 |\n"
        "|---|----------|-------|----------|--------|----------|----------------------------------|--------------|\n"
        f"| 1 | {topic_for_claim}_{digest_stem}_c01 | (示例 claim) | high | medium | unverified | ASR [00:00:00.000-00:00:01.000] | (如何核验) |"
    )

    prompt = f"""你是投研助理。你将收到一个视频的 ASR 逐段转录（带时间戳）与 OCR 数字命中（带帧文件名）。请严格只基于给定证据总结，不要引入外部事实或补充未出现的数字。

输出要求（只输出 Markdown，不要解释；不要用 ``` 包裹）：

1) YAML frontmatter
- 必须从第一行开始，且用 `---` 包裹（见下方示例）。
- tags/entities 必须是 **JSON 数组**（例如 `tags: [\"A\",\"B\"]` 或空数组 `[]`），不要写成散落的多行文本。

2) 标题（必须以 `## ` 开头；顺序一致）
- ## 核心观点（用 `- ` 列表，3–7 条）
- ## 关键证据/数据点（可引用来源）（用 `- ` 列表）
- ## 反驳点/局限性（用 `- ` 列表）
- ## 对主题框架的影响（用 `- ` 列表）
- ## 建议写入 mem0 的长期结论候选（可选）（用 `- ` 列表）
- ## Claim Ledger（断言清单，建议用于投研/行业研究）

3) Claim Ledger 表格（必须是 `|` 分隔的 Markdown 表格；列名与示例一致）
- claim_id 使用稳定格式：{topic_for_claim}_{digest_stem}_c01, c02...（新增/重排时不变）
- 影响范围/置信度 只能用：high|medium|low
- 核验状态 只能用：unverified|partially_verified|verified|falsified
- 每条 claim 的“来源/证据”必须引用到本次证据中的时间戳/帧（例如：ASR [00:00:20.960-00:00:23.800]；OCR [00:00:12.000 frame 000007.jpg]）
- 对“外媒报道/考虑中/可能/传闻”等必须标注 unverified，并写明下一步核验动作（找原始报道/官方口径/一手披露）

你必须按以下骨架输出（可填充内容，但不要改变结构）：

{frontmatter_example}

## 核心观点

- ...

## 关键证据/数据点（可引用来源）

- ...

## 反驳点/局限性

- ...

## 对主题框架的影响

- ...

## 建议写入 mem0 的长期结论候选（可选）

- ...

## Claim Ledger（断言清单，建议用于投研/行业研究）

{claim_table_example}

上下文：
- analysis_id: {analysis_id}
- generated_at: {generated_at}

### ASR Transcript（逐段，带时间戳）
{chr(10).join(transcript_lines) if transcript_lines else '- (无转录)'}

### OCR / Compact Evidence（可选）
{chr(10).join([ln for ln in ocr_and_compact if ln.strip()]) if ocr_and_compact else '- (无)'}
"""
    return prompt.strip()


def _build_gemini_audit_prompt(*, digest_md: str) -> str:
    return (
        "你是审计员。请审阅下面这份视频 digest（它应当只基于 ASR/OCR 证据），输出：\n"
        "1) 你认为证据支撑不足/可能误读的 claim_id 列表（逐条说明原因）；\n"
        "2) 关键遗漏点（若有）；\n"
        "3) 建议新增的核验任务（可执行、可核验）；\n"
        "输出为 Markdown 列表，不要复述全文。\n\n"
        "=== DIGEST START ===\n"
        f"{digest_md.strip()}\n"
        "=== DIGEST END ===\n"
    )


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(description="Generate a video digest via chatgptMCP (ChatGPT Pro + Gemini web).")
    ap.add_argument("--analysis-id", default="", help="state/video-analyses/<analysis_id>")
    ap.add_argument("--analysis-dir", default="", help="Explicit analysis directory path.")
    ap.add_argument("--topic", default="", help="Optional topic_id to write into archives/topics/<topic>/digests/")
    ap.add_argument("--output", default="", help="Optional explicit output path for digest markdown.")
    ap.add_argument("--published-at", default="", help="Optional published_at (YYYY-MM-DD) to force into frontmatter.")
    ap.add_argument("--source-url", default="", help="Optional source_url override (e.g. bilibili video URL).")
    ap.add_argument("--max-transcript-chars", type=int, default=20000, help="Max chars of transcript to include in prompt (0=all).")
    ap.add_argument("--no-ocr", action="store_true", help="Do not include OCR hits in prompt.")
    ap.add_argument("--no-gemini", action="store_true", help="Skip Gemini audit step.")
    ap.add_argument("--timeout-seconds", type=int, default=1200, help="Timeout for each web ask (seconds).")
    ap.add_argument("--chatgpt-mcp-url", default="", help="MCP HTTP url (default from env CHATGPT_MCP_URL or http://127.0.0.1:18701/mcp).")
    ap.add_argument("--allow-invalid", action="store_true", help="Write model output even if it fails template validation (not recommended).")
    ap.add_argument("--json", action="store_true", help="Output a JSON object (paths + conversation urls) instead of plain path.")
    ap.add_argument("--dry-run", action="store_true", help="Build prompts and print planned paths without calling MCP.")
    args = ap.parse_args(argv)

    analysis_dir = Path(args.analysis_dir).expanduser() if args.analysis_dir else None
    if analysis_dir is None:
        analysis_id = str(args.analysis_id).strip()
        if not analysis_id:
            _die("require --analysis-id or --analysis-dir")
        analysis_dir = REPO_ROOT / "state" / "video-analyses" / analysis_id
    if not analysis_dir.exists():
        _die(f"analysis_dir not found: {analysis_dir}")

    topic_id = str(args.topic).strip()
    if args.output:
        out_path = Path(args.output).expanduser()
    else:
        date = datetime.now().strftime("%Y-%m-%d")
        stem = f"{date}_video_{analysis_dir.name}"
        if topic_id:
            out_path = REPO_ROOT / "archives" / "topics" / topic_id / "digests" / f"{stem}.md"
        else:
            out_path = REPO_ROOT / "exports" / "digests" / f"{stem}.md"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    digest_stem = out_path.stem

    published_at = str(args.published_at).strip()
    meta, transcript_lines, ocr_and_compact = _build_inputs(
        analysis_dir,
        max_transcript_chars=int(args.max_transcript_chars),
        include_ocr=(not bool(args.no_ocr)),
        source_url_override=str(args.source_url).strip(),
    )

    prompt = _build_chatgpt_prompt(
        meta=meta,
        topic_id=topic_id,
        digest_stem=digest_stem,
        published_at=published_at,
        transcript_lines=transcript_lines,
        ocr_and_compact=ocr_and_compact,
    )

    if args.dry_run:
        print(f"[dry-run] analysis_dir={analysis_dir}")
        print(f"[dry-run] output={out_path}")
        print(f"[dry-run] prompt_chars={len(prompt)} transcript_lines={len(transcript_lines)}")
        return 0

    url = str(args.chatgpt_mcp_url).strip() or os.environ.get("CHATGPT_MCP_URL") or "http://127.0.0.1:18701/mcp"
    try:
        session = _mcp_initialize_with_retry(url, attempts=3)
    except Exception as exc:
        _die(str(exc))

    # ChatGPT Pro (main digest)
    chatgpt_result: Dict[str, Any] = {}
    try:
        chatgpt_result = _call_tool_with_retry(
            session,
            url=url,
            tool_name="chatgpt_web_ask_pro_extended",
            tool_args={"question": prompt, "timeout_seconds": int(args.timeout_seconds)},
            timeout_seconds=int(args.timeout_seconds),
            attempts=3,
        )
    except Exception as exc:
        _die(str(exc))

    answer = str(chatgpt_result.get("answer") or "")
    digest_md = _sanitize_llm_markdown(answer)
    digest_md = _normalize_chatgpt_output(
        digest_md,
        topic_id=topic_id,
        digest_stem=digest_stem,
        source_url=str(meta.get("source_url") or ""),
        source_path=str(meta.get("video_path") or ""),
        published_at=published_at,
    )
    errs = _validate_digest(digest_md)
    if errs:
        # One repair attempt, reusing the conversation for context.
        conv = str(chatgpt_result.get("conversation_url") or "").strip() or None
        topic_for_claim = topic_id or "video"
        repair = f"""你刚才的输出未严格符合格式要求（可能缺少 `---` frontmatter、缺少 `## ` 标题、或 Claim Ledger 不是 `|` 分隔表格）。

请在同一会话中**重新输出完整最终版 Markdown**（不要解释、不要使用 ``` 包裹），并严格按下面骨架输出：

---
title: \"(一句话概括)\"
source_type: \"video\"
source_url: \"{(meta.get('source_url') or '').strip()}\"
source_path: \"{(meta.get('video_path') or '').strip()}\"
published_at: \"{published_at}\"
topic_id: \"{topic_id}\"
tags: []
entities: []
---

## 核心观点
- ...

## 关键证据/数据点（可引用来源）
- ...

## 反驳点/局限性
- ...

## 对主题框架的影响
- ...

## 建议写入 mem0 的长期结论候选（可选）
- ...

## Claim Ledger（断言清单，建议用于投研/行业研究）

| # | claim_id | claim | 影响范围 | 置信度 | 核验状态 | 来源/证据（URL/出处/时间戳/帧） | 建议核验动作 |
|---|----------|-------|----------|--------|----------|----------------------------------|--------------|
| 1 | {topic_for_claim}_{digest_stem}_c01 | ... | high | medium | unverified | ASR [00:00:00.000-00:00:01.000] | ... |
"""
        try:
            chatgpt_result2 = mcp_http_call_tool(
                session,
                tool_name="chatgpt_web_ask_pro_extended",
                tool_args={"question": repair, "conversation_url": conv, "timeout_seconds": int(args.timeout_seconds)},
                timeout_sec=float(args.timeout_seconds) + 30.0,
            )
            digest_md2 = _sanitize_llm_markdown(str(chatgpt_result2.get("answer") or ""))
            digest_md2 = _normalize_chatgpt_output(
                digest_md2,
                topic_id=topic_id,
                digest_stem=digest_stem,
                source_url=str(meta.get("source_url") or ""),
                source_path=str(meta.get("video_path") or ""),
                published_at=published_at,
            )
            if not _validate_digest(digest_md2):
                digest_md = digest_md2
                chatgpt_result = chatgpt_result2
        except Exception:
            pass

    final_errs = _validate_digest(digest_md)
    if final_errs and not bool(args.allow_invalid):
        debug_dir = REPO_ROOT / "state" / "tmp" / "video_digest_web_research"
        debug_dir.mkdir(parents=True, exist_ok=True)
        raw_path = debug_dir / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{analysis_dir.name}_raw.md"
        raw_path.write_text(digest_md, encoding="utf-8")
        _die(
            "chatgpt output failed validation; wrote raw output to "
            f"{raw_path}. errors={final_errs}. You can re-run with --allow-invalid to force write."
        )

    # Optional Gemini audit (append to digest as a handoff section).
    audit_md = ""
    gemini_result: Dict[str, Any] = {}
    if not bool(args.no_gemini):
        # Be conservative: wait a bit before asking Gemini to reduce "back-to-back" UI actions.
        time.sleep(10)
        try:
            gemini_result = _call_tool_with_retry(
                session,
                url=url,
                tool_name="gemini_web_ask_pro_thinking",
                tool_args={"question": _build_gemini_audit_prompt(digest_md=digest_md), "timeout_seconds": int(args.timeout_seconds)},
                timeout_seconds=int(args.timeout_seconds),
                attempts=2,
            )
            audit_text = str(gemini_result.get("answer") or "").strip()
            # Sometimes Gemini returns a very short early message; wait on the same conversation.
            if len(audit_text) < 200 and gemini_result.get("conversation_url"):
                waited = mcp_http_call_tool(
                    session,
                    tool_name="gemini_web_wait",
                    tool_args={"conversation_url": str(gemini_result.get("conversation_url")), "timeout_seconds": 900, "min_chars": 400},
                    timeout_sec=930.0,
                )
                audit_text2 = str(waited.get("answer") or "").strip()
                if len(audit_text2) > len(audit_text):
                    audit_text = audit_text2
            if audit_text:
                audit_md = "\n\n## 审计与下一步（Gemini）\n\n" + _sanitize_llm_markdown(audit_text).strip() + "\n"
        except Exception:
            audit_md = ""

    final_md = digest_md.rstrip() + "\n"
    if audit_md:
        final_md = final_md.rstrip() + audit_md

    out_path.write_text(final_md, encoding="utf-8")

    # Write a short run note (paths + conversation urls) for handoff.
    note_path: Optional[Path] = None
    if topic_id:
        notes_dir = REPO_ROOT / "archives" / "topics" / topic_id / "notes" / "runs"
        notes_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y-%m-%d_%H%M")
        note_path = notes_dir / f"{ts}_video_digest_{analysis_dir.name}.md"
        note_lines = [
            "# Video digest run (web-research)",
            "",
            f"- analysis_dir: `{analysis_dir}`",
            f"- digest_path: `{out_path.relative_to(REPO_ROOT) if out_path.is_relative_to(REPO_ROOT) else out_path}`",
            f"- source_url: {meta.get('source_url') or ''}",
            f"- video_path: `{meta.get('video_path') or ''}`",
            "",
        ]
        if chatgpt_result.get("conversation_url"):
            note_lines.append(f"- chatgpt_conversation_url: {chatgpt_result.get('conversation_url')}")
        if gemini_result.get("conversation_url"):
            note_lines.append(f"- gemini_conversation_url: {gemini_result.get('conversation_url')}")
        note_lines.append("")
        note_path.write_text("\n".join(note_lines), encoding="utf-8")

    if bool(args.json):
        payload = {
            "ok": True,
            "analysis_dir": str(analysis_dir),
            "digest_path": str(out_path),
            "topic_id": topic_id,
            "published_at": published_at,
            "chatgpt_conversation_url": str(chatgpt_result.get("conversation_url") or ""),
            "gemini_conversation_url": str(gemini_result.get("conversation_url") or ""),
        }
        if note_path is not None:
            payload["run_note_path"] = str(note_path)
        sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    else:
        sys.stdout.write(str(out_path) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
