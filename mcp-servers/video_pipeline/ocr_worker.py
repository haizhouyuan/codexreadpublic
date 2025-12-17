#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def seconds_to_timecode(seconds: float) -> str:
    if seconds < 0:
        seconds = 0
    ms = int(round(seconds * 1000))
    s, ms = divmod(ms, 1000)
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def _has_digits(text: str) -> bool:
    return bool(re.search(r"\d", text))


def _is_noise_numeric_line(text: str) -> bool:
    t = str(text).strip().replace(" ", "")
    return t.startswith("录制时间") or t.startswith("录制日期")


def _coerce_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except Exception:
        return None


def run_ocr(frames_dir: Path, *, frame_every_sec: float, lang: str) -> List[Dict[str, Any]]:
    os.environ.setdefault("DISABLE_MODEL_SOURCE_CHECK", "True")
    from paddleocr import PaddleOCR  # type: ignore

    ocr_lang = "ch" if lang.lower().startswith("zh") else "en"
    ocr = PaddleOCR(lang=ocr_lang)

    frame_files = sorted(frames_dir.glob("*.jpg"))
    results: List[Dict[str, Any]] = []

    for idx, frame_path in enumerate(frame_files, start=1):
        approx_time_sec = (idx - 1) * frame_every_sec
        if hasattr(ocr, "predict"):
            raw = ocr.predict(str(frame_path))
        else:
            raw = ocr.ocr(str(frame_path))

        numeric_lines: List[Dict[str, Any]] = []
        if isinstance(raw, list) and raw:
            first = raw[0]
            if hasattr(first, "get"):
                rec_texts = first.get("rec_texts") or []
                rec_scores = first.get("rec_scores") or []
                for j, text in enumerate(rec_texts):
                    text_s = str(text).strip()
                    if not text_s or not _has_digits(text_s) or _is_noise_numeric_line(text_s):
                        continue
                    score = rec_scores[j] if j < len(rec_scores) else None
                    numeric_lines.append({"text": text_s, "score": _coerce_float(score)})
            elif isinstance(first, list):
                for item in first:
                    try:
                        text, score = item[1]
                    except Exception:
                        continue
                    text_s = str(text).strip()
                    if not text_s or not _has_digits(text_s) or _is_noise_numeric_line(text_s):
                        continue
                    numeric_lines.append({"text": text_s, "score": _coerce_float(score)})

        if not numeric_lines:
            continue

        results.append(
            {
                "frame_file": frame_path.name,
                "frame_path": str(frame_path),
                "approx_time_sec": approx_time_sec,
                "approx_timecode": seconds_to_timecode(approx_time_sec),
                "numeric_lines": numeric_lines,
            }
        )

    return results


def main(argv: List[str]) -> int:
    try:
        os.dup2(2, 1)
    except Exception:
        pass
    sys.stdout = sys.stderr

    parser = argparse.ArgumentParser(prog="video_pipeline_ocr_worker")
    parser.add_argument("--frames-dir", required=True)
    parser.add_argument("--frame-every-sec", type=float, required=True)
    parser.add_argument("--lang", default="zh")
    parser.add_argument("--out-json", required=True)
    args = parser.parse_args(argv)

    frames_dir = Path(str(args.frames_dir))
    out_json = Path(str(args.out_json))

    if not frames_dir.exists():
        raise FileNotFoundError(frames_dir)

    results = run_ocr(frames_dir, frame_every_sec=float(args.frame_every_sec), lang=str(args.lang))
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"ok frames={len(results)} generated_at={now_iso()}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

