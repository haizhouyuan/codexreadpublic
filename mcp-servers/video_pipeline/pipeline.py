from __future__ import annotations

import csv
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


logger = logging.getLogger(__name__)


@contextmanager
def stdout_to_stderr() -> Iterable[None]:
    original_stdout = sys.stdout
    sys.stdout = sys.stderr
    try:
        yield
    finally:
        sys.stdout = original_stdout


@contextmanager
def fd_stdout_to_stderr() -> Iterable[None]:
    try:
        original_fd = os.dup(1)
    except Exception:
        yield
        return
    try:
        os.dup2(2, 1)
        yield
    finally:
        try:
            os.dup2(original_fd, 1)
        finally:
            os.close(original_fd)


@contextmanager
def quiet_stdout() -> Iterable[None]:
    with stdout_to_stderr(), fd_stdout_to_stderr():
        yield


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def ensure_ffmpeg() -> None:
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg not found in PATH")
    if shutil.which("ffprobe") is None:
        raise RuntimeError("ffprobe not found in PATH")


def run(cmd: List[str]) -> None:
    logger.info("run: %s", cmd)
    subprocess.run(cmd, check=True)


def ffprobe_duration_sec(video_path: Path) -> Optional[float]:
    try:
        out = subprocess.check_output(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(video_path),
            ],
            text=True,
        ).strip()
    except Exception:
        return None
    try:
        return float(out)
    except Exception:
        return None


def seconds_to_timecode(seconds: float) -> str:
    if seconds < 0:
        seconds = 0
    ms = int(round(seconds * 1000))
    s, ms = divmod(ms, 1000)
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def safe_slug(value: str, *, max_len: int = 64) -> str:
    s = value.strip()
    s = re.sub(r"\s+", "_", s)
    s = s.replace("/", "_").replace("\\", "_")
    s = re.sub(r"[^0-9A-Za-z_\u4e00-\u9fff.-]+", "", s)
    s = s.strip("._-")
    if not s:
        return "video"
    return s[:max_len]


def cuda_available() -> bool:
    force_cpu = os.getenv("VIDEO_PIPELINE_FORCE_CPU", "").strip().lower() in {"1", "true", "yes"}
    if force_cpu:
        return False

    # Common device nodes when running with NVIDIA runtime.
    if Path("/dev/nvidia0").exists() or Path("/dev/nvidiactl").exists():
        return True
    return False


def resolve_asr_device(device: str) -> str:
    device = (device or "").strip().lower()
    if device in {"", "auto"}:
        return "cuda" if cuda_available() else "cpu"
    return device


def resolve_asr_compute_type(compute_type: str, *, device: str) -> str:
    compute_type = (compute_type or "").strip()
    if compute_type in {"", "auto"}:
        return "float16" if device == "cuda" else "int8"
    return compute_type


def extract_audio(video_path: Path, audio_wav: Path) -> None:
    audio_wav.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(video_path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-loglevel",
            "error",
            str(audio_wav),
        ]
    )


def extract_frames(
    video_path: Path,
    frames_dir: Path,
    *,
    frame_every_sec: float,
    max_height: int,
) -> None:
    frames_dir.mkdir(parents=True, exist_ok=True)
    if frame_every_sec <= 0:
        raise ValueError("frame_every_sec must be > 0")

    vf_parts: List[str] = []
    if max_height and max_height > 0:
        # cap height (avoid upscaling). Need to escape the comma inside min().
        vf_parts.append(f"scale=-2:min(ih\\,{int(max_height)})")
    vf_parts.append(f"fps=1/{frame_every_sec}")
    vf = ",".join(vf_parts)

    run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(video_path),
            "-vf",
            vf,
            "-q:v",
            "2",
            "-loglevel",
            "error",
            str(frames_dir / "%06d.jpg"),
        ]
    )


def _has_digits(text: str) -> bool:
    return bool(re.search(r"\d", text))


def _has_compact_numeric_fact(text: str) -> bool:
    c = str(text).replace(" ", "")
    return bool(
        re.search(
            r"\d+(?:\.\d+)?(?:[%％]|GB|TB|MB|TOPS|bps|GHz|nm|倍|亿|万|千|百|元|美元|USD|CNY|T(?![A-Za-z]))",
            c,
            flags=re.IGNORECASE,
        )
    )


def _normalize_ocr_text(text: str) -> str:
    t = str(text or "").strip().lower()
    t = re.sub(r"\s+", "", t)
    t = t.replace("：", ":")
    return t


def _is_noise_numeric_line(text: str) -> bool:
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
    # Watermark-ish "update date" and "license id" commonly overlaid on videos.
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
    if re.match(r"^科技\d+", compact):
        return True
    return False


@dataclass
class TranscriptSegment:
    start: float
    end: float
    text: str

    def to_dict(self) -> Dict[str, Any]:
        return {"start": self.start, "end": self.end, "text": self.text}


def run_asr(
    audio_wav: Path,
    *,
    lang: str,
    model_name: str,
    device: str,
    compute_type: str,
    vad_filter: bool = True,
) -> Tuple[List[TranscriptSegment], List[str]]:
    warnings: List[str] = []

    try:
        os.environ.setdefault("DISABLE_MODEL_SOURCE_CHECK", "True")
        with quiet_stdout():
            from faster_whisper import WhisperModel  # type: ignore
    except Exception:
        warnings.append("faster-whisper not installed; skipped ASR")
        return [], warnings

    resolved_device = resolve_asr_device(device)
    resolved_compute = resolve_asr_compute_type(compute_type, device=resolved_device)
    try:
        with quiet_stdout():
            model = WhisperModel(model_name, device=resolved_device, compute_type=resolved_compute)
    except Exception as e:
        if resolved_device == "cuda":
            warnings.append(f"ASR cuda init failed; fallback to cpu: {e}")
            try:
                resolved_device = "cpu"
                resolved_compute = "int8"
                with quiet_stdout():
                    model = WhisperModel(model_name, device=resolved_device, compute_type=resolved_compute)
            except Exception as e2:
                warnings.append(f"ASR init failed on cpu: {e2}")
                return [], warnings
        else:
            warnings.append(f"ASR init failed: {e}")
            return [], warnings

    segments_out: List[TranscriptSegment] = []
    try:
        with quiet_stdout():
            segments, _info = model.transcribe(
                str(audio_wav),
                language=lang or None,
                vad_filter=bool(vad_filter),
            )
        for seg in segments:
            text = (seg.text or "").strip()
            if not text:
                continue
            segments_out.append(TranscriptSegment(start=float(seg.start), end=float(seg.end), text=text))
    except Exception as e:
        warnings.append(f"ASR failed: {e}")
        return [], warnings

    warnings.append(f"ASR device={resolved_device} compute_type={resolved_compute} model={model_name}")
    return segments_out, warnings


def write_srt(segments: List[TranscriptSegment], out_path: Path) -> None:
    def fmt_srt_time(t: float) -> str:
        if t < 0:
            t = 0
        ms = int(round(t * 1000))
        s, ms = divmod(ms, 1000)
        m, s = divmod(s, 60)
        h, m = divmod(m, 60)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    lines: List[str] = []
    for idx, seg in enumerate(segments, start=1):
        lines.append(str(idx))
        lines.append(f"{fmt_srt_time(seg.start)} --> {fmt_srt_time(seg.end)}")
        lines.append(seg.text)
        lines.append("")
    out_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def run_ocr_numeric_only(
    frames: List[Tuple[Path, float]],
    *,
    lang: str,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    warnings: List[str] = []
    try:
        os.environ.setdefault("DISABLE_MODEL_SOURCE_CHECK", "True")
        with quiet_stdout():
            from paddleocr import PaddleOCR  # type: ignore
    except Exception:
        warnings.append("paddleocr not installed; skipped OCR")
        return [], warnings

    ocr_lang = "ch" if lang.lower().startswith("zh") else "en"
    try:
        with quiet_stdout():
            ocr = PaddleOCR(lang=ocr_lang)
    except Exception as e:
        warnings.append(f"paddleocr init failed: {e}")
        return [], warnings

    ocr_results: List[Dict[str, Any]] = []
    for frame_path, approx_time_sec in frames:
        try:
            with quiet_stdout():
                if hasattr(ocr, "predict"):
                    raw = ocr.predict(str(frame_path))
                else:
                    raw = ocr.ocr(str(frame_path))
        except Exception as e:
            warnings.append(f"ocr failed for {frame_path.name}: {type(e).__name__}: {e!r}")
            continue

        numeric_lines: List[Dict[str, Any]] = []
        if isinstance(raw, list) and raw:
            first = raw[0]
            if hasattr(first, "get"):
                rec_texts = first.get("rec_texts") or []
                rec_scores = first.get("rec_scores") or []
                for idx, text in enumerate(rec_texts):
                    text_s = str(text).strip()
                    if not text_s or not _has_digits(text_s):
                        continue
                    score = rec_scores[idx] if idx < len(rec_scores) else None
                    try:
                        score_f = float(score) if score is not None else None
                    except Exception:
                        score_f = None
                    numeric_lines.append({"text": text_s, "score": score_f})
            elif isinstance(first, list):
                for item in first:
                    try:
                        text, score = item[1]
                    except Exception:
                        continue
                    text_s = str(text).strip()
                    if not text_s or not _has_digits(text_s):
                        continue
                    try:
                        score_f = float(score) if score is not None else None
                    except Exception:
                        score_f = None
                    numeric_lines.append({"text": text_s, "score": score_f})

        if not numeric_lines:
            continue

        ocr_results.append(
            {
                "frame_file": frame_path.name,
                "frame_path": str(frame_path),
                "approx_time_sec": approx_time_sec,
                "approx_timecode": seconds_to_timecode(approx_time_sec),
                "numeric_lines": numeric_lines,
            }
        )

    return ocr_results, warnings


def run_ocr_numeric_only_subprocess(
    frames_dir: Path,
    *,
    frame_every_sec: float,
    lang: str,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    warnings: List[str] = []

    worker = Path(__file__).with_name("ocr_worker.py")
    if not worker.exists():
        warnings.append("ocr worker missing; skipped OCR")
        return [], warnings

    if not frames_dir.exists():
        warnings.append("frames dir missing; skipped OCR")
        return [], warnings

    try:
        tmp_dir = Path(tempfile.mkdtemp(prefix="ocr_", dir=str(frames_dir.parent)))
    except Exception:
        tmp_dir = Path(tempfile.mkdtemp(prefix="ocr_"))

    out_json = tmp_dir / "ocr_results.json"
    try:
        cmd = [
            sys.executable,
            str(worker),
            "--frames-dir",
            str(frames_dir),
            "--frame-every-sec",
            str(frame_every_sec),
            "--lang",
            str(lang),
            "--out-json",
            str(out_json),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()
            if detail:
                detail = "\n".join(detail.splitlines()[-30:])
            warnings.append("ocr subprocess failed")
            if detail:
                warnings.append(detail)
            return [], warnings

        if not out_json.exists():
            warnings.append("ocr subprocess produced no output; skipped OCR")
            return [], warnings

        data = json.loads(out_json.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            warnings.append("ocr subprocess output invalid; skipped OCR")
            return [], warnings
        return data, warnings
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def build_evidence_compact_md(
    *,
    video_path: Path,
    video_sha256: str,
    duration_sec: Optional[float],
    transcript: List[TranscriptSegment],
    ocr_hits: List[Dict[str, Any]],
    key_metrics_csv: Path,
) -> str:
    lines: List[str] = []
    lines.append("# Video Evidence (Compact)")
    lines.append("")
    lines.append(f"- video_path: `{video_path}`")
    lines.append(f"- video_sha256: `{video_sha256}`")
    if duration_sec is not None:
        lines.append(f"- duration_sec: `{duration_sec:.2f}`")
    lines.append(f"- generated_at: `{now_iso()}`")
    lines.append(f"- key_metrics_csv: `{key_metrics_csv}`")
    lines.append("")

    # Transcript highlights: keep segments that contain digits or common finance keywords.
    highlight_keywords = ("同比", "环比", "增长", "下降", "市场", "份额", "营收", "利润", "毛利", "指引", "订单", "渗透率")
    highlights: List[TranscriptSegment] = []
    for seg in transcript:
        t = seg.text
        if _has_digits(t) or any(k in t for k in highlight_keywords):
            highlights.append(seg)
    highlights = highlights[:200]  # hard cap

    lines.append("## Transcript Highlights")
    lines.append("")
    if not highlights:
        lines.append("> (no transcript highlights; ASR may be disabled or missing)")
        lines.append("")
    else:
        for seg in highlights:
            lines.append(f"- [{seconds_to_timecode(seg.start)} - {seconds_to_timecode(seg.end)}] {seg.text}")
        lines.append("")

    lines.append("## OCR Numeric Hits (Frames)")
    lines.append("")
    if not ocr_hits:
        lines.append("> (no OCR hits; OCR may be disabled or missing)")
        lines.append("")
    else:
        for item in ocr_hits[:200]:
            lines.append(f"- [{item['approx_timecode']}] `{item['frame_file']}`")
            for ln in item.get("numeric_lines", [])[:20]:
                score = ln.get("score")
                score_s = f"{score:.2f}" if isinstance(score, (int, float)) else "?"
                lines.append(f"  - ({score_s}) {ln.get('text','')}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def write_key_metrics_csv(
    out_path: Path,
    *,
    transcript: List[TranscriptSegment],
    ocr_hits: List[Dict[str, Any]],
) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["source", "start_sec", "end_sec", "timecode", "frame_file", "text", "score"])

        for seg in transcript:
            if not _has_digits(seg.text):
                continue
            w.writerow(
                [
                    "asr",
                    f"{seg.start:.2f}",
                    f"{seg.end:.2f}",
                    f"{seconds_to_timecode(seg.start)}-{seconds_to_timecode(seg.end)}",
                    "",
                    seg.text,
                    "",
                ]
            )

        for item in ocr_hits:
            for ln in item.get("numeric_lines", []):
                w.writerow(
                    [
                        "frame_ocr",
                        f"{float(item['approx_time_sec']):.2f}",
                        "",
                        item.get("approx_timecode", ""),
                        item.get("frame_file", ""),
                        ln.get("text", ""),
                        ln.get("score", ""),
                    ]
                )


def analyze_video(
    *,
    video_path: Path,
    out_dir: Path,
    analysis_id: str,
    lang: str,
    frame_every_sec: float,
    max_height: int,
    enable_asr: bool,
    enable_frames: bool,
    enable_ocr: bool,
    ocr_mode: str,
    dry_run: bool,
    overwrite: bool,
    asr_model: str = "large-v3",
    asr_device: str = "auto",
    asr_compute_type: str = "auto",
    asr_vad_filter: bool = True,
) -> Dict[str, Any]:
    ensure_ffmpeg()

    if not video_path.exists():
        raise FileNotFoundError(video_path)

    out_dir.mkdir(parents=True, exist_ok=True)

    manifest_path = out_dir / "manifest.json"
    evidence_json_path = out_dir / "evidence.json"
    evidence_md_path = out_dir / "evidence.md"
    evidence_compact_md_path = out_dir / "evidence_compact.md"
    audio_wav = out_dir / "audio.wav"
    transcript_json = out_dir / "transcript.json"
    transcript_srt = out_dir / "transcript.srt"
    frames_dir = out_dir / "frames"
    ocr_jsonl = out_dir / "ocr.jsonl"
    key_metrics_csv = out_dir / "key_metrics.csv"

    warnings: List[str] = []
    duration_sec = ffprobe_duration_sec(video_path)
    video_sha = sha256_file(video_path)

    if dry_run:
        artifacts = {
            "manifest_json": str(manifest_path),
            "evidence_json": str(evidence_json_path),
            "evidence_md": str(evidence_md_path),
            "evidence_compact_md": str(evidence_compact_md_path),
            "audio_wav": str(audio_wav),
            "transcript_json": str(transcript_json),
            "transcript_srt": str(transcript_srt),
            "frames_dir": str(frames_dir),
            "ocr_jsonl": str(ocr_jsonl),
            "key_metrics_csv": str(key_metrics_csv),
        }
        return {
            "analysis_id": analysis_id,
            "out_dir": str(out_dir),
            "artifacts": artifacts,
            "stats": {},
            "warnings": ["dry_run=true; nothing executed"],
        }

    if evidence_json_path.exists() and not overwrite:
        return json.loads(evidence_json_path.read_text(encoding="utf-8"))

    transcript: List[TranscriptSegment] = []
    if enable_asr:
        if not audio_wav.exists() or overwrite:
            extract_audio(video_path, audio_wav)
        transcript, w = run_asr(
            audio_wav,
            lang=lang,
            model_name=asr_model,
            device=asr_device,
            compute_type=asr_compute_type,
            vad_filter=asr_vad_filter,
        )
        warnings.extend(w)
        transcript_json.write_text(
            json.dumps([seg.to_dict() for seg in transcript], ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        if transcript:
            write_srt(transcript, transcript_srt)
    else:
        warnings.append("ASR disabled")

    extracted_frames: List[Tuple[Path, float]] = []
    if enable_frames:
        if not frames_dir.exists() or overwrite:
            if frames_dir.exists() and overwrite:
                shutil.rmtree(frames_dir)
            extract_frames(
                video_path,
                frames_dir,
                frame_every_sec=frame_every_sec,
                max_height=max_height,
            )
        frame_files = sorted(frames_dir.glob("*.jpg"))
        for idx, fp in enumerate(frame_files, start=1):
            extracted_frames.append((fp, (idx - 1) * frame_every_sec))
    else:
        warnings.append("frames disabled")

    ocr_hits: List[Dict[str, Any]] = []
    if enable_ocr:
        if ocr_mode != "numeric_only":
            warnings.append(f"unsupported ocr_mode={ocr_mode!r}; fallback to numeric_only")
        if enable_asr:
            ocr_hits, w = run_ocr_numeric_only_subprocess(
                frames_dir,
                frame_every_sec=frame_every_sec,
                lang=lang,
            )
        else:
            ocr_hits, w = run_ocr_numeric_only(extracted_frames, lang=lang)
        warnings.extend(w)

        filtered_hits: List[Dict[str, Any]] = []
        for item in ocr_hits:
            numeric_lines = []
            for ln in item.get("numeric_lines", []) or []:
                if _is_noise_numeric_line(ln.get("text", "")):
                    continue
                numeric_lines.append(ln)
            if not numeric_lines:
                continue
            filtered = dict(item)
            filtered["numeric_lines"] = numeric_lines
            filtered_hits.append(filtered)
        ocr_hits = filtered_hits

        # Cross-frame filtering + de-dup:
        # - Drop "persistent overlay" lines that appear in most frames and don't look like compact numeric facts.
        # - Keep each remaining OCR line at most once across the whole video to prevent key_metrics pollution.
        if ocr_hits:
            key_frame_counts: Dict[str, int] = {}
            key_example: Dict[str, str] = {}
            for item in ocr_hits:
                keys_in_frame: set[str] = set()
                for ln in item.get("numeric_lines", []) or []:
                    txt = str(ln.get("text", "")).strip()
                    if not txt:
                        continue
                    k = _normalize_ocr_text(txt)
                    if not k:
                        continue
                    key_example.setdefault(k, txt)
                    keys_in_frame.add(k)
                for k in keys_in_frame:
                    key_frame_counts[k] = key_frame_counts.get(k, 0) + 1

            total = max(1, len(ocr_hits))
            persistent_keys = {
                k
                for k, c in key_frame_counts.items()
                if (c / total) >= 0.80 and not _has_compact_numeric_fact(key_example.get(k, ""))
            }

            seen_keys: set[str] = set()
            deduped_hits: List[Dict[str, Any]] = []
            for item in ocr_hits:
                kept: List[Dict[str, Any]] = []
                for ln in item.get("numeric_lines", []) or []:
                    txt = str(ln.get("text", "")).strip()
                    if not txt:
                        continue
                    k = _normalize_ocr_text(txt)
                    if not k or k in persistent_keys or k in seen_keys:
                        continue
                    seen_keys.add(k)
                    kept.append(ln)
                if not kept:
                    continue
                filtered = dict(item)
                filtered["numeric_lines"] = kept
                deduped_hits.append(filtered)

            ocr_hits = deduped_hits

        # Write jsonl (numeric-only)
        with ocr_jsonl.open("w", encoding="utf-8") as f:
            for item in ocr_hits:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")
    else:
        warnings.append("OCR disabled")

    write_key_metrics_csv(key_metrics_csv, transcript=transcript, ocr_hits=ocr_hits)

    evidence_compact_md_path.write_text(
        build_evidence_compact_md(
            video_path=video_path,
            video_sha256=video_sha,
            duration_sec=duration_sec,
            transcript=transcript,
            ocr_hits=ocr_hits,
            key_metrics_csv=key_metrics_csv,
        ),
        encoding="utf-8",
    )

    evidence = {
        "schema_version": "1.0",
        "generated_at": now_iso(),
        "analysis_id": analysis_id,
        "video": {
            "path": str(video_path),
            "sha256": video_sha,
            "duration_sec": duration_sec,
        },
        "transcript": [seg.to_dict() for seg in transcript],
        "frames": [
            {
                "frame_path": str(frames_dir / item["frame_file"]),
                "frame_index": int(Path(item["frame_file"]).stem),
                "approx_time_sec": float(item["approx_time_sec"]),
                "approx_timecode": item["approx_timecode"],
                "ocr_numeric_lines": item.get("numeric_lines", []),
                "tables": [],
            }
            for item in ocr_hits
        ],
        "artifacts": {
            "manifest_json": str(manifest_path),
            "audio_wav": str(audio_wav) if enable_asr else None,
            "transcript_json": str(transcript_json) if enable_asr else None,
            "transcript_srt": str(transcript_srt) if enable_asr and transcript else None,
            "frames_dir": str(frames_dir) if enable_frames else None,
            "ocr_jsonl": str(ocr_jsonl) if enable_ocr else None,
            "key_metrics_csv": str(key_metrics_csv),
            "evidence_md": str(evidence_md_path),
            "evidence_compact_md": str(evidence_compact_md_path),
        },
        "stats": {
            "frames_extracted": len(extracted_frames),
            "transcript_segments": len(transcript),
            "ocr_frames_with_numeric_hits": len(ocr_hits),
        },
        "warnings": warnings,
    }

    evidence_json_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    evidence_md_path.write_text(
        f"# Video Evidence\n\n- out_dir: `{out_dir}`\n- analysis_id: `{analysis_id}`\n\n"
        f"- transcript_segments: `{len(transcript)}`\n- frames_extracted: `{len(extracted_frames)}`\n"
        f"- ocr_frames_with_numeric_hits: `{len(ocr_hits)}`\n\n"
        f"See `evidence_compact.md` for Codex input.\n",
        encoding="utf-8",
    )

    manifest = {
        "analysis_id": analysis_id,
        "generated_at": evidence["generated_at"],
        "video_path": str(video_path),
        "params": {
            "lang": lang,
            "frame_every_sec": frame_every_sec,
            "max_height": max_height,
            "enable_asr": enable_asr,
            "enable_frames": enable_frames,
            "enable_ocr": enable_ocr,
            "ocr_mode": ocr_mode,
            "overwrite": overwrite,
            "asr_model": asr_model,
            "asr_device": asr_device,
            "asr_compute_type": asr_compute_type,
            "asr_vad_filter": bool(asr_vad_filter),
        },
        "warnings": warnings,
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    return evidence
