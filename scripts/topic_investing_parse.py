#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from topic_investing_utils import parse_company_pool


REPO_ROOT = Path(__file__).resolve().parents[1]


def _die(msg: str, code: int = 2) -> None:
    print(msg, file=sys.stderr)
    raise SystemExit(code)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Parse topic investing.md company pool table into JSON.")
    ap.add_argument("--topic-id", default="", help="Topic id under archives/topics (optional if --investing-path is given).")
    ap.add_argument("--investing-path", default="", help="Explicit investing.md path.")
    ap.add_argument("--out", default="", help="Write JSON to file (optional).")
    args = ap.parse_args(argv)

    topic_id = str(args.topic_id).strip()
    investing_path_raw = str(args.investing_path).strip()
    if investing_path_raw:
        investing_path = Path(investing_path_raw).expanduser()
        if not investing_path.is_absolute():
            investing_path = (REPO_ROOT / investing_path).resolve(strict=False)
    else:
        if not topic_id:
            _die("require --topic-id or --investing-path")
        investing_path = (REPO_ROOT / "archives" / "topics" / topic_id / "investing.md").resolve(strict=False)

    if not investing_path.exists():
        _die(f"investing.md not found: {investing_path}")

    inferred_topic_id = topic_id or investing_path.parent.name

    companies, warnings = parse_company_pool(investing_path)
    thesis = [c for c in companies if c.get("status") == "thesis_candidate"]
    candidates = [c for c in companies if c.get("status") in {"candidate", "thesis_candidate"}]

    payload: dict[str, Any] = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "topic_id": inferred_topic_id,
        "investing_path": str(investing_path),
        "warnings": warnings,
        "stats": {
            "companies_total": len(companies),
            "companies_candidate_or_thesis": len(candidates),
            "thesis_candidates": len(thesis),
        },
        "companies": companies,
    }

    out_raw = str(args.out).strip()
    if out_raw:
        out_path = Path(out_raw).expanduser()
        if not out_path.is_absolute():
            out_path = (REPO_ROOT / out_path).resolve(strict=False)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(str(out_path))
        return 0

    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

