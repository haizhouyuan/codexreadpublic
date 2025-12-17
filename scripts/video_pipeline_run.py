#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List


REPO_ROOT = Path(__file__).resolve().parents[1]


def _die(msg: str, code: int = 2) -> None:
    print(msg, file=sys.stderr)
    raise SystemExit(code)


def _ffprobe_wh(video_path: Path) -> tuple[int, int] | None:
    try:
        out = subprocess.check_output(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height",
                "-of",
                "csv=s=x:p=0",
                str(video_path),
            ],
            text=True,
        ).strip()
    except Exception:
        return None
    if not out or "x" not in out:
        return None
    try:
        w_s, h_s = out.split("x", 1)
        return int(w_s), int(h_s)
    except Exception:
        return None


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(description="Run local video_pipeline analysis (runner for batch scripts).")
    ap.add_argument("--video", required=True, help="Local video path.")
    ap.add_argument("--analysis-id", required=True, help="Analysis id (used for output dir name).")
    ap.add_argument("--out-dir", required=True, help="Output directory (must be under state/video-analyses).")
    ap.add_argument("--asr-model", default="large-v3", help="faster-whisper model (default: large-v3).")
    ap.add_argument("--frame-every-sec", type=float, default=5.0, help="Extract one frame every N seconds.")
    ap.add_argument(
        "--max-height",
        type=int,
        default=-1,
        help="Max frame height: 0=no scale, -1=auto (default: vertical=1920, horizontal=1080).",
    )
    ap.add_argument("--enable-ocr", action="store_true", help="Enable OCR (numeric_only).")
    ap.add_argument("--no-asr-vad-filter", action="store_true", help="Disable VAD filter for ASR (may recover quiet speech).")
    ap.add_argument("--overwrite", action="store_true", help="Overwrite existing analysis.")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,
        format="%(levelname)s:%(name)s:%(message)s",
    )

    video_path = Path(args.video).expanduser()
    out_dir = Path(args.out_dir).expanduser()
    base_dir = (REPO_ROOT / "state" / "video-analyses").resolve()
    try:
        out_dir_r = out_dir.resolve()
    except Exception:
        _die(f"invalid out_dir: {out_dir}")
    if out_dir_r != base_dir and base_dir not in out_dir_r.parents:
        _die(f"out_dir must be under {base_dir} (got {out_dir})")

    sys.path.insert(0, str(REPO_ROOT / "mcp-servers" / "video_pipeline"))
    try:
        from pipeline import analyze_video  # type: ignore
    except Exception as e:
        _die(f"failed to import video_pipeline pipeline.py: {e}")

    max_height = int(args.max_height)
    if max_height < 0:
        wh = _ffprobe_wh(video_path)
        if wh is None:
            max_height = 1080
        else:
            w, h = wh
            max_height = 1920 if h > w else 1080

    evidence = analyze_video(
        video_path=video_path,
        out_dir=out_dir,
        analysis_id=str(args.analysis_id),
        lang="zh",
        frame_every_sec=float(args.frame_every_sec),
        max_height=max_height,
        enable_asr=True,
        enable_frames=True,
        enable_ocr=bool(args.enable_ocr),
        ocr_mode="numeric_only",
        dry_run=False,
        overwrite=bool(args.overwrite),
        asr_model=str(args.asr_model),
        asr_device="auto",
        asr_compute_type="auto",
        asr_vad_filter=(not bool(args.no_asr_vad_filter)),
    )

    structured: Dict[str, Any] = {
        "analysis_id": str(args.analysis_id),
        "out_dir": str(out_dir),
        "artifacts": evidence.get("artifacts", {}),
        "stats": evidence.get("stats", {}),
        "warnings": evidence.get("warnings", []),
    }
    sys.stdout.write(json.dumps(structured, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
