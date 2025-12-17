#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
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


def _slugify(value: str) -> str:
    s = (value or "").strip()
    s = re.sub(r"[^A-Za-z0-9_.-]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    if s:
        return s[:64].lower()
    raw = (value or "").strip()
    if not raw:
        return "unknown"
    digest = hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()[:10]
    return f"unknown_{digest}"


def _is_na_ticker(ticker: str) -> bool:
    t = (ticker or "").strip().lower()
    return t in ("", "n/a", "na", "none", "-")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Generate CFA-style company cards for top N companies from topic investing.md.")
    ap.add_argument("--topic-id", required=True)
    ap.add_argument("--topic-title", default="")
    ap.add_argument("--investing-path", default="")
    ap.add_argument("--cards-dir", default="")
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--allow-paid", action="store_true")
    ap.add_argument("--out", required=True, help="Write JSON results to this path.")
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

    cards_dir_raw = str(args.cards_dir).strip()
    if cards_dir_raw:
        cards_dir = Path(cards_dir_raw).expanduser()
        if not cards_dir.is_absolute():
            cards_dir = (REPO_ROOT / cards_dir).resolve(strict=False)
    else:
        cards_dir = (REPO_ROOT / "archives" / "topics" / topic_id / "companies").resolve(strict=False)
    cards_dir.mkdir(parents=True, exist_ok=True)

    companies, warnings = parse_company_pool(investing_path)
    pick = [c for c in companies if c.get("status") in {"candidate", "thesis_candidate"}]
    pick.sort(
        key=lambda r: (
            0 if r.get("status") == "thesis_candidate" else 1,
            -priority_rank(r.get("priority") or ""),
            (r.get("ticker") or r.get("company") or ""),
        )
    )
    pick = pick[: max(0, int(args.limit))]

    glm_script = (REPO_ROOT / "scripts" / "glm_write_file.py").resolve(strict=False)
    if not glm_script.exists():
        _die(f"missing glm_write_file.py: {glm_script}")

    results: list[dict[str, Any]] = []
    for row in pick:
        company = (row.get("company") or "").strip()
        if not company:
            continue
        ticker = (row.get("ticker") or "").strip()
        segment = (row.get("segment") or "").strip()
        market = (row.get("market") or "").strip()
        hypothesis = (row.get("hypothesis") or "").strip()
        evidence_level = (row.get("evidence_level") or "").strip()
        gaps = (row.get("gaps") or "").strip()
        slug_base = ticker if not _is_na_ticker(ticker) else company
        slug = _slugify(slug_base)
        out_path = cards_dir / f"{slug}.md"

        instructions = (
            "请为公司生成 CFA 风格 Company Card（中文），严格按模板输出。\n\n"
            f"公司：{company}\n"
            f"Ticker：{ticker or 'N/A'}\n"
            f"Topic：{topic_id}（{args.topic_title or ''}）\n\n"
            f"公司池信息（来自 investing.md）：\n"
            f"- 细分赛道：{segment or 'N/A'}\n"
            f"- 市场：{market or 'N/A'}\n"
            f"- 投资假设（可证伪）：{hypothesis or 'N/A'}\n"
            f"- 证据等级：{evidence_level or 'N/A'}\n"
            f"- 关键缺口：{gaps or 'N/A'}\n\n"
            "要求：\n"
            "1) 不要编造具体数字；拿不到就标 unverified，并在表格/结论里写出“要去哪里核验”。\n"
            "2) 结论必须可证伪，Bull/Base/Bear 各给 2-3 条。\n"
            "3) Monitoring KPIs 给出可执行的数据源建议（官网/监管披露/财报/行业标准）。\n"
        )

        cmd = [
            sys.executable,
            str(glm_script),
            "--output-path",
            str(out_path),
            "--overwrite",
            "--timeout-sec",
            "360",
            "--max-retries",
            "2",
            "--template-path",
            "templates/company_card.md",
            "--validate-must-have",
            "## 1) Business & Strategy",
            "--validate-must-have",
            "## 8) Conclusion",
            "--validate-must-have",
            "| 指标 |",
            "--validate-min-chars",
            "800",
            "--validate-max-chars",
            "20000",
            "--system",
            "你是严谨的卖方分析师。禁止编造关键数字；不确定就标 unverified 并转成核验任务。",
            "--instructions",
            instructions,
        ]
        if bool(args.allow_paid):
            cmd.insert(2, "--allow-paid")

        cp = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if cp.returncode != 0:
            results.append(
                {
                    "company": company,
                    "ticker": ticker,
                    "ok": False,
                    "error": (cp.stderr or "").strip()[:800],
                    "output_path": str(out_path),
                }
            )
            continue

        try:
            meta = json.loads((cp.stdout or "").strip())
        except Exception:
            meta = {"raw": (cp.stdout or "").strip()[:300]}
        results.append({"company": company, "ticker": ticker, "ok": True, "output_path": str(out_path), "glm": meta})

    ok = [r for r in results if r.get("ok")]
    bad = [r for r in results if not r.get("ok")]

    payload: dict[str, Any] = {
        "generated_at": _now_iso(),
        "topic_id": topic_id,
        "topic_title": str(args.topic_title).strip(),
        "investing_path": str(investing_path),
        "cards_dir": str(cards_dir),
        "limit": int(args.limit),
        "warnings": warnings,
        "stats": {"ok": len(ok), "failed": len(bad), "total": len(results)},
        "results": results,
    }

    out_path = Path(str(args.out)).expanduser()
    if not out_path.is_absolute():
        out_path = (REPO_ROOT / out_path).resolve(strict=False)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(str(out_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
