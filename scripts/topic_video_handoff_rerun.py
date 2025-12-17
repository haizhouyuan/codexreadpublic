#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _die(msg: str, code: int = 2) -> None:
    print(msg, file=sys.stderr)
    raise SystemExit(code)


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _append_md(path: Path, lines: List[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for ln in lines:
            f.write(ln.rstrip() + "\n")


def _date_to_yyyymmdd(date_s: str) -> str:
    s = (date_s or "").strip()
    m = re.match(r"^(\\d{4})-(\\d{2})-(\\d{2})$", s)
    if not m:
        return ""
    return f"{m.group(1)}{m.group(2)}{m.group(3)}"


@dataclass(frozen=True)
class SourceRow:
    published_at: str
    title: str
    url: str
    digest_rel: str
    bvid: str


def _parse_sources_table(path: Path) -> tuple[Optional[str], List[SourceRow]]:
    text = _read_text(path)
    mid: Optional[str] = None
    rows: List[SourceRow] = []

    in_table = False
    for ln in text.splitlines():
        if ln.strip().startswith("| 日期 |"):
            in_table = True
            continue
        if not in_table:
            continue
        if ln.strip().startswith("|---"):
            continue
        if not ln.strip().startswith("|"):
            # End of table.
            break
        parts = [p.strip() for p in ln.strip().strip("|").split("|")]
        if len(parts) < 5:
            continue
        date, typ, title, link, digest = parts[:5]
        if typ.lower() == "up":
            m = re.search(r"space\\.bilibili\\.com/(\\d+)", link)
            if m:
                mid = m.group(1)
            continue
        if typ.lower() != "video":
            continue
        bvm = re.search(r"(BV[0-9A-Za-z]+)", link) or re.search(r"(BV[0-9A-Za-z]+)", digest)
        if not bvm:
            continue
        bvid = bvm.group(1)
        rows.append(
            SourceRow(
                published_at=str(date).strip(),
                title=str(title).strip(),
                url=str(link).strip(),
                digest_rel=str(digest).strip(),
                bvid=bvid,
            )
        )
    return mid, rows


def _parse_failed_bvids_from_record(path: Path) -> set[str]:
    text = _read_text(path)
    failed: set[str] = set()
    in_table = False
    for ln in text.splitlines():
        if ln.strip().startswith("| # | bvid |"):
            in_table = True
            continue
        if not in_table:
            continue
        if ln.strip().startswith("|---"):
            continue
        if not ln.strip().startswith("|"):
            # End of table.
            break
        parts = [p.strip() for p in ln.strip().strip("|").split("|")]
        if len(parts) < 4:
            continue
        bvid = parts[1]
        status = parts[3].lower()
        if not bvid.startswith("BV"):
            continue
        if status.startswith("ok"):
            continue
        failed.add(bvid)
    return failed


def _find_analysis_dir(*, mid: Optional[str], published_at: str, bvid: str) -> Optional[Path]:
    yyyymmdd = _date_to_yyyymmdd(published_at)
    if mid and yyyymmdd:
        cand = REPO_ROOT / "state" / "video-analyses" / f"bili_{mid}_{yyyymmdd}_{bvid}"
        if cand.exists():
            return cand

    # Fallback: search by BV id.
    base = REPO_ROOT / "state" / "video-analyses"
    if not base.exists():
        return None
    hits = sorted([p for p in base.iterdir() if p.is_dir() and bvid in p.name])
    for p in hits:
        if p.name.endswith("_refined_small"):
            continue
        return p
    return hits[0] if hits else None


def _safe_repo_path(path_raw: str) -> Path:
    p = Path(path_raw)
    if not p.is_absolute():
        p = (REPO_ROOT / p).resolve(strict=False)
    try:
        rp = p.resolve(strict=False)
    except Exception:
        rp = p
    if rp != REPO_ROOT and REPO_ROOT not in rp.parents:
        raise ValueError(f"path escapes repo: {path_raw!r}")
    return rp


def _run_digest(
    *,
    analysis_dir: Path,
    topic_id: str,
    out_path: Path,
    published_at: str,
    source_url: str,
    timeout_seconds: int,
    no_gemini: bool,
    max_transcript_chars: int,
    chatgpt_mcp_url: str,
) -> Dict[str, Any]:
    cmd: List[str] = [
        sys.executable,
        str(SCRIPTS_DIR / "generate_video_digest_via_web_research.py"),
        "--analysis-dir",
        str(analysis_dir),
        "--topic",
        topic_id,
        "--output",
        str(out_path),
        "--published-at",
        published_at,
        "--source-url",
        source_url,
        "--timeout-seconds",
        str(timeout_seconds),
        "--max-transcript-chars",
        str(max_transcript_chars),
        "--json",
    ]
    if no_gemini:
        cmd.append("--no-gemini")
    if chatgpt_mcp_url:
        cmd.extend(["--chatgpt-mcp-url", chatgpt_mcp_url])

    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "").strip() or f"digest failed (exit={proc.returncode})")
    out = (proc.stdout or "").strip()
    try:
        return json.loads(out)
    except Exception:
        raise RuntimeError(f"unexpected digest output (expected JSON): {out[:2000]}")


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(description="Re-run a topic's bilibili video digests via chatgptMCP (handoff v2).")
    ap.add_argument("--topic-id", required=True)
    ap.add_argument("--sources-path", default="", help="Default: archives/topics/<topic_id>/sources.md")
    ap.add_argument("--record-path", default="", help="Append progress to this markdown file.")
    ap.add_argument("--only-failed-from", default="", help="Optional: only rerun bvids that failed in an existing record markdown.")
    ap.add_argument("--sleep-between", type=float, default=25.0, help="Extra sleep between videos (seconds).")
    ap.add_argument("--timeout-seconds", type=int, default=1800)
    ap.add_argument("--max-transcript-chars", type=int, default=20000)
    ap.add_argument("--no-gemini", action="store_true")
    ap.add_argument("--chatgpt-mcp-url", default="")
    ap.add_argument("--limit", type=int, default=0, help="Process first N videos (0=all).")
    args = ap.parse_args(argv)

    topic_id = str(args.topic_id).strip()
    if not topic_id:
        _die("topic_id is required")

    sources_path = str(args.sources_path).strip()
    if sources_path:
        sources = _safe_repo_path(sources_path)
    else:
        sources = REPO_ROOT / "archives" / "topics" / topic_id / "sources.md"
    if not sources.exists():
        _die(f"sources not found: {sources}")

    record_path_raw = str(args.record_path).strip()
    record_path = _safe_repo_path(record_path_raw) if record_path_raw else None

    mid, rows = _parse_sources_table(sources)
    if not rows:
        _die(f"no video rows found in sources table: {sources}")

    only_failed_from_raw = str(args.only_failed_from).strip()
    if only_failed_from_raw:
        only_failed_path = _safe_repo_path(only_failed_from_raw)
        if not only_failed_path.exists():
            _die(f"only_failed_from not found: {only_failed_path}")
        failed_bvids = _parse_failed_bvids_from_record(only_failed_path)
        if not failed_bvids:
            _die(f"no failed bvids found in record: {only_failed_path}")
        rows = [r for r in rows if r.bvid in failed_bvids]
        if not rows:
            _die("after filtering by --only-failed-from, no matching video rows remain")

    limit = int(args.limit)
    if limit > 0:
        rows = rows[:limit]

    if record_path is not None:
        _append_md(
            record_path,
            [
                "# 视频 digest 重跑（handoff v2）",
                "",
                f"- ts_start: {_now_iso()}",
                f"- topic_id: {topic_id}",
                f"- sources: `{sources.relative_to(REPO_ROOT) if sources.is_relative_to(REPO_ROOT) else sources}`",
                f"- mid: {mid or ''}",
                f"- count: {len(rows)}",
                f"- max_transcript_chars: {int(args.max_transcript_chars)}",
                f"- no_gemini: {bool(args.no_gemini)}",
                (f"- only_failed_from: `{only_failed_from_raw}`" if only_failed_from_raw else "- only_failed_from:"),
                f"- timeout_seconds: {int(args.timeout_seconds)}",
                f"- sleep_between: {float(args.sleep_between)}",
                "",
                "## Progress",
                "",
                "| # | bvid | published_at | status | digest | chatgpt | gemini |",
                "|---:|------|-------------|--------|--------|--------|--------|",
            ],
        )

    ok = 0
    failed = 0
    for idx, row in enumerate(rows, start=1):
        analysis_dir = _find_analysis_dir(mid=mid, published_at=row.published_at, bvid=row.bvid)
        digest_path = (REPO_ROOT / "archives" / "topics" / topic_id / row.digest_rel).resolve(strict=False)
        if analysis_dir is None or not analysis_dir.exists():
            failed += 1
            if record_path is not None:
                _append_md(
                    record_path,
                    [f"| {idx} | {row.bvid} | {row.published_at} | missing_analysis | `{row.digest_rel}` |  |  |"],
                )
            continue

        try:
            result = _run_digest(
                analysis_dir=analysis_dir,
                topic_id=topic_id,
                out_path=digest_path,
                published_at=row.published_at,
                source_url=row.url,
                timeout_seconds=int(args.timeout_seconds),
                no_gemini=bool(args.no_gemini),
                max_transcript_chars=int(args.max_transcript_chars),
                chatgpt_mcp_url=str(args.chatgpt_mcp_url).strip(),
            )
            ok += 1
            if record_path is not None:
                _append_md(
                    record_path,
                    [
                        "| "
                        + " | ".join(
                            [
                                str(idx),
                                row.bvid,
                                row.published_at,
                                "ok",
                                f"`{row.digest_rel}`",
                                (result.get("chatgpt_conversation_url") or "").strip(),
                                (result.get("gemini_conversation_url") or "").strip(),
                            ]
                        )
                        + " |"
                    ],
                )
        except Exception as exc:
            failed += 1
            if record_path is not None:
                msg = str(exc).replace("\n", " ").strip()
                msg = (msg[:180] + "…") if len(msg) > 180 else msg
                _append_md(
                    record_path,
                    [f"| {idx} | {row.bvid} | {row.published_at} | failed ({msg}) | `{row.digest_rel}` |  |  |"],
                )

        time.sleep(float(args.sleep_between))

    if record_path is not None:
        _append_md(
            record_path,
            [
                "",
                "## Summary",
                "",
                f"- ts_end: {_now_iso()}",
                f"- ok: {ok}",
                f"- failed: {failed}",
            ],
        )

    if failed:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
