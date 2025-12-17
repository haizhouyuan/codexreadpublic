#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlparse
from urllib.parse import urlencode
from urllib.request import Request, urlopen


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_IMPORTS_VIDEOS_DIR = REPO_ROOT / "imports" / "content" / "videos"
DEFAULT_STATE_DIR = REPO_ROOT / "state"
DEFAULT_VENV_PYTHON = REPO_ROOT / ".venv" / "bin" / "python"


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _die(msg: str, code: int = 2) -> None:
    print(msg, file=sys.stderr)
    raise SystemExit(code)


def _run(cmd: List[str], *, cwd: Optional[Path] = None, timeout_sec: Optional[int] = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        text=True,
        capture_output=True,
        timeout=timeout_sec,
        check=False,
    )


def _yt_dlp_path() -> str:
    candidates = [
        str(REPO_ROOT / ".venv" / "bin" / "yt-dlp"),
        "yt-dlp",
    ]
    for candidate in candidates:
        if os.path.isabs(candidate) and Path(candidate).exists():
            return candidate
        if not os.path.isabs(candidate):
            probe = _run(["bash", "-lc", f"command -v {candidate}"], timeout_sec=5)
            if probe.returncode == 0 and probe.stdout.strip():
                return candidate
    _die("yt-dlp not found. Install into venv: `.venv/bin/python -m pip install yt-dlp`")
    raise AssertionError("unreachable")


def _cuda_available() -> bool:
    force_cpu = os.getenv("VIDEO_PIPELINE_FORCE_CPU", "").strip().lower() in {"1", "true", "yes"}
    if force_cpu:
        return False
    return Path("/dev/nvidia0").exists() or Path("/dev/nvidiactl").exists()


def _resolve_b23(url: str) -> str:
    url = url.strip()
    if not url:
        _die("empty url")
    if "b23.tv" not in url:
        return url
    proc = _run(["curl", "-Ls", "-o", "/dev/null", "-w", "%{url_effective}\n", url], timeout_sec=30)
    if proc.returncode != 0 or not proc.stdout.strip():
        _die(f"failed to resolve b23 url: {url}\n{proc.stderr.strip()}")
    return proc.stdout.strip()


def _extract_mid(space_url: str) -> str:
    space_url = _resolve_b23(space_url)
    parsed = urlparse(space_url)
    if parsed.netloc.endswith("bilibili.com") and parsed.path.startswith("/"):
        m = re.match(r"^/(\d+)(/.*)?$", parsed.path)
        if m:
            return m.group(1)
    m = re.search(r"(?:space\.bilibili\.com/)(\d+)", space_url)
    if m:
        return m.group(1)
    _die(f"could not extract mid from url: {space_url}")
    raise AssertionError("unreachable")


@dataclass(frozen=True)
class Entry:
    bvid: str
    url: str
    playlist_index: int


def _http_get_json(url: str, *, headers: Dict[str, str], timeout_sec: int = 20) -> Dict[str, Any]:
    req = Request(url, headers=headers)
    with urlopen(req, timeout=timeout_sec) as resp:
        data = resp.read()
    try:
        return json.loads(data.decode("utf-8", errors="replace"))
    except Exception as e:
        raise RuntimeError(f"failed to parse json from {url}: {e}") from e


_WBI_MIXIN_KEY_ENC_TAB = [
    46,
    47,
    18,
    2,
    53,
    8,
    23,
    32,
    15,
    50,
    10,
    31,
    58,
    3,
    45,
    35,
    27,
    43,
    5,
    49,
    33,
    9,
    42,
    19,
    29,
    28,
    14,
    39,
    12,
    38,
    41,
    13,
]


def _wbi_mixin_key(img_key: str, sub_key: str) -> str:
    s = img_key + sub_key
    return "".join(s[i] for i in _WBI_MIXIN_KEY_ENC_TAB)[:32]


def _bilibili_wbi_keys() -> Tuple[str, str]:
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://www.bilibili.com/",
    }
    nav = _http_get_json("https://api.bilibili.com/x/web-interface/nav", headers=headers, timeout_sec=20)
    data = nav.get("data") if isinstance(nav, dict) else None
    wbi_img = data.get("wbi_img") if isinstance(data, dict) else None
    img_url = wbi_img.get("img_url") if isinstance(wbi_img, dict) else ""
    sub_url = wbi_img.get("sub_url") if isinstance(wbi_img, dict) else ""
    if not img_url or not sub_url:
        raise RuntimeError("nav missing wbi_img keys")
    img_key = Path(str(img_url)).stem
    sub_key = Path(str(sub_url)).stem
    if not img_key or not sub_key:
        raise RuntimeError("invalid wbi_img keys")
    return img_key, sub_key


def _space_arc_search_latest(mid: str, limit: int) -> List[Entry]:
    img_key, sub_key = _bilibili_wbi_keys()
    mixin = _wbi_mixin_key(img_key, sub_key)

    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": f"https://space.bilibili.com/{mid}/video",
    }

    pn = 1
    ps = limit
    params: Dict[str, Any] = {"mid": mid, "pn": pn, "ps": ps, "order": "pubdate", "platform": "web"}
    params["wts"] = int(time.time())
    # wbi signing: sort params and md5(query + mixin_key)
    query = urlencode(sorted((k, str(v)) for k, v in params.items()))
    w_rid = hashlib.md5((query + mixin).encode("utf-8")).hexdigest()
    url = f"https://api.bilibili.com/x/space/wbi/arc/search?{query}&w_rid={w_rid}"

    backoffs = [2, 5, 12, 25, 45]
    last_err: Optional[str] = None
    for wait_s in [0] + backoffs:
        if wait_s:
            time.sleep(wait_s + random.random())
        try:
            data = _http_get_json(url, headers=headers, timeout_sec=20)
        except Exception as e:
            last_err = str(e)
            continue
        code = data.get("code")
        if code == 0:
            vlist = (((data.get("data") or {}).get("list") or {}).get("vlist") or [])
            if not isinstance(vlist, list):
                raise RuntimeError("arc/search invalid vlist")
            entries: List[Entry] = []
            for idx, v in enumerate(vlist, start=1):
                if not isinstance(v, dict):
                    continue
                bvid = str(v.get("bvid") or "").strip()
                if not bvid:
                    continue
                entries.append(Entry(bvid=bvid, url=f"https://www.bilibili.com/video/{bvid}", playlist_index=idx))
            if entries:
                return entries
            last_err = "arc/search returned empty list"
            continue
        if code == -799:
            last_err = f"arc/search throttled: {data.get('message')}"
            continue
        last_err = f"arc/search error code={code} msg={data.get('message')}"
    raise RuntimeError(last_err or "arc/search failed")


def _entries_cache_path(mid: str, limit: int) -> Path:
    return DEFAULT_STATE_DIR / "tmp" / f"bilibili_up_{mid}_latest_{limit}.json"


def _read_entries_cache(path: Path) -> Optional[List[Entry]]:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, list):
        return None
    out: List[Entry] = []
    for obj in data:
        if not isinstance(obj, dict):
            continue
        bvid = str(obj.get("bvid") or "").strip()
        url = str(obj.get("url") or "").strip()
        try:
            playlist_index = int(obj.get("playlist_index") or 0)
        except Exception:
            playlist_index = 0
        if not bvid or not url or playlist_index <= 0:
            continue
        out.append(Entry(bvid=bvid, url=url, playlist_index=playlist_index))
    return out or None


def _write_entries_cache(path: Path, entries: List[Entry]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps([{"bvid": e.bvid, "url": e.url, "playlist_index": e.playlist_index} for e in entries], ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


def list_latest_videos(space_url_or_mid: str, limit: int) -> List[Entry]:
    mid = space_url_or_mid if space_url_or_mid.isdigit() else _extract_mid(space_url_or_mid)
    space_video_url = f"https://space.bilibili.com/{mid}/video"
    cache = _entries_cache_path(mid, limit)
    cached = _read_entries_cache(cache)
    if cached is not None:
        return cached

    yt_dlp = _yt_dlp_path()

    cmd = [
        yt_dlp,
        "--ignore-errors",
        "--flat-playlist",
        "--dump-json",
        "--sleep-requests",
        "1",
        "--playlist-end",
        str(limit),
        space_video_url,
    ]
    # Retry for Bilibili anti-scraping throttles (e.g. Request rejected 352).
    backoffs = [3, 8, 20, 45]
    last_err = ""
    for attempt, wait_s in enumerate([0] + backoffs, start=1):
        cached = _read_entries_cache(cache)
        if cached is not None:
            return cached
        if wait_s:
            time.sleep(wait_s)
        proc = _run(cmd, cwd=REPO_ROOT, timeout_sec=120)
        if proc.returncode == 0:
            last_err = ""
            break
        last_err = (proc.stderr or proc.stdout or "").strip()
        # If still failing, keep retrying; another worker might have written cache meanwhile.
    if last_err:
        # Fallback to bilibili wbi api (more stable under extractor throttles).
        try:
            entries = _space_arc_search_latest(mid, limit)
            _write_entries_cache(cache, entries)
            return entries
        except Exception as e:
            _die(f"yt-dlp list failed and wbi fallback failed:\ncmd={' '.join(cmd)}\n{last_err}\n\nwbi_error={e}")

    entries: List[Entry] = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        bvid = str(obj.get("id") or "").strip()
        url = str(obj.get("url") or obj.get("webpage_url") or "").strip()
        playlist_index = int(obj.get("playlist_index") or len(entries) + 1)
        if not bvid or not url:
            continue
        entries.append(Entry(bvid=bvid, url=url, playlist_index=playlist_index))

    if not entries:
        _die(f"no entries found from: {space_video_url}")
    _write_entries_cache(cache, entries)
    return entries


def _ensure_under(path: Path, base: Path, *, label: str) -> None:
    try:
        path_r = path.resolve()
        base_r = base.resolve()
    except Exception:
        _die(f"{label} invalid path: {path}")
    if path_r != base_r and base_r not in path_r.parents:
        _die(f"{label} must be under {base} (got {path})")


def download_one(*, url: str, out_dir: Path, archive_file: Path, sleep_interval_sec: float) -> Tuple[Optional[Path], Dict[str, Any]]:
    yt_dlp = _yt_dlp_path()
    out_dir.mkdir(parents=True, exist_ok=True)
    archive_file.parent.mkdir(parents=True, exist_ok=True)

    out_tmpl = str(out_dir / "%(upload_date)s_%(id)s.%(ext)s")
    cmd = [
        yt_dlp,
        "--no-playlist",
        "--ignore-errors",
        "--merge-output-format",
        "mp4",
        "--download-archive",
        str(archive_file),
        "--write-info-json",
        "--write-thumbnail",
        "--sleep-interval",
        str(sleep_interval_sec),
        "--max-sleep-interval",
        str(max(2.0, sleep_interval_sec * 3)),
        "--print",
        "after_move:filepath",
        "-o",
        out_tmpl,
        url,
    ]
    proc = _run(cmd, cwd=REPO_ROOT, timeout_sec=None)
    meta: Dict[str, Any] = {
        "cmd": cmd,
        "returncode": proc.returncode,
        "stdout_tail": "\n".join(proc.stdout.splitlines()[-20:]),
        "stderr_tail": "\n".join(proc.stderr.splitlines()[-50:]),
    }

    # When already archived, yt-dlp prints a message and may not emit filepath.
    candidates = [Path(p) for p in proc.stdout.splitlines() if p.strip() and Path(p.strip()).suffix]
    for p in reversed(candidates):
        if p.exists() and p.is_file():
            return p, meta
    return None, meta


def _analysis_id_from_video_path(video_path: Path, *, mid: str) -> str:
    # filename is like: YYYYMMDD_BVxxxx.mp4
    stem = video_path.stem
    m = re.match(r"^(?P<date>\d{8})_(?P<bvid>BV[0-9A-Za-z]+)$", stem)
    if m:
        return f"bili_{mid}_{m.group('date')}_{m.group('bvid')}"
    safe = re.sub(r"[^0-9A-Za-z_\\-]+", "_", stem)[:80].strip("_")
    return f"bili_{mid}_{safe}"


def _analysis_python() -> Path:
    if DEFAULT_VENV_PYTHON.exists():
        return DEFAULT_VENV_PYTHON
    return Path(sys.executable)


def analyze_with_pipeline_subprocess(
    video_path: Path,
    *,
    analysis_id: str,
    asr_model: str,
    frame_every_sec: float,
    enable_ocr: bool,
    overwrite: bool,
) -> Dict[str, Any]:
    out_dir = DEFAULT_STATE_DIR / "video-analyses" / analysis_id
    py = _analysis_python()
    runner = REPO_ROOT / "scripts" / "video_pipeline_run.py"
    if not runner.exists():
        _die(f"missing runner: {runner}")

    cmd = [
        str(py),
        str(runner),
        "--video",
        str(video_path),
        "--analysis-id",
        str(analysis_id),
        "--out-dir",
        str(out_dir),
        "--asr-model",
        str(asr_model),
        "--frame-every-sec",
        str(frame_every_sec),
    ]
    if enable_ocr:
        cmd.append("--enable-ocr")
    if overwrite:
        cmd.append("--overwrite")

    proc = _run(cmd, cwd=REPO_ROOT, timeout_sec=None)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "").strip() or f"video_pipeline_run failed: {cmd}")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"video_pipeline_run returned non-json output: {e}\n{proc.stdout[:500]}") from e


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(description="Download and batch-analyze latest videos from a Bilibili UP space.")
    ap.add_argument("--up", required=True, help="UP space url (space.bilibili.com/<mid>) or b23 short url or mid.")
    ap.add_argument("--limit", type=int, default=30, help="How many latest videos to process (default: 30).")
    ap.add_argument("--start-index", type=int, default=1, help="1-based playlist index start (default: 1).")
    ap.add_argument("--end-index", type=int, default=0, help="1-based playlist index end (default: 0 meaning limit).")
    ap.add_argument("--out", default="", help="Download directory (default: imports/content/videos/bilibili/<mid>/).")
    ap.add_argument("--download", action="store_true", help="Perform download step.")
    ap.add_argument("--analyze", action="store_true", help="Perform video_pipeline analysis step.")
    ap.add_argument("--asr-model", default="", help="faster-whisper model name (GPU: large-v3; CPU default: small).")
    ap.add_argument("--frame-every-sec", type=float, default=5.0, help="Extract one frame every N seconds (default: 5.0).")
    ap.add_argument("--enable-ocr", action="store_true", help="Enable OCR (default: off unless set).")
    ap.add_argument("--overwrite-analysis", action="store_true", help="Overwrite existing analysis artifacts.")
    ap.add_argument("--sleep-interval-sec", type=float, default=1.2, help="yt-dlp sleep interval between requests.")
    args = ap.parse_args(argv)

    if not args.download and not args.analyze:
        args.download = True
        args.analyze = True

    mid = args.up if str(args.up).isdigit() else _extract_mid(args.up)
    out_dir = Path(args.out).expanduser() if args.out else (DEFAULT_IMPORTS_VIDEOS_DIR / "bilibili" / mid)
    _ensure_under(out_dir, DEFAULT_IMPORTS_VIDEOS_DIR, label="--out")

    if args.asr_model.strip():
        asr_model = args.asr_model.strip()
    else:
        asr_model = "large-v3" if _cuda_available() else "small"

    run_id = _now_iso().replace(":", "").replace("-", "").replace("Z", "Z")
    run_dir = DEFAULT_STATE_DIR / "runs" / "bilibili_up_batch"
    run_dir.mkdir(parents=True, exist_ok=True)
    run_json = run_dir / f"{run_id}_{mid}.json"

    archive_file = DEFAULT_STATE_DIR / "video-download-archives" / f"bilibili_{mid}.txt"
    entries = list_latest_videos(mid, args.limit)
    start_i = max(1, int(args.start_index))
    end_i = int(args.end_index) if int(args.end_index) > 0 else int(args.limit)
    if end_i < start_i:
        _die(f"--end-index must be >= --start-index (got {start_i}..{end_i})")
    entries = [e for e in entries if start_i <= int(e.playlist_index) <= end_i]

    run: Dict[str, Any] = {
        "schema_version": "1.0",
        "generated_at": _now_iso(),
        "up": {"mid": mid, "space_url": f"https://space.bilibili.com/{mid}/video"},
        "params": {
            "limit": args.limit,
            "start_index": start_i,
            "end_index": end_i,
            "download": bool(args.download),
            "analyze": bool(args.analyze),
            "out_dir": str(out_dir),
            "archive_file": str(archive_file),
            "analysis_python": str(_analysis_python()),
            "asr_model": asr_model,
            "frame_every_sec": args.frame_every_sec,
            "enable_ocr": bool(args.enable_ocr),
            "overwrite_analysis": bool(args.overwrite_analysis),
        },
        "items": [],
    }

    for entry in entries:
        item: Dict[str, Any] = {"bvid": entry.bvid, "url": entry.url, "playlist_index": entry.playlist_index}
        video_path: Optional[Path] = None
        if args.download:
            video_path, dl_meta = download_one(
                url=entry.url,
                out_dir=out_dir,
                archive_file=archive_file,
                sleep_interval_sec=args.sleep_interval_sec,
            )
            if video_path is None:
                # If the item was already archived, yt-dlp may not print the filepath.
                candidates = sorted(out_dir.glob(f"*_{entry.bvid}.*"))
                candidates = [
                    p for p in candidates if p.is_file() and p.suffix.lower() in {".mp4", ".mkv", ".flv", ".webm"}
                ]
                if candidates:
                    video_path = candidates[0]
                    dl_meta = dict(dl_meta)
                    dl_meta["resolved_existing_video_path"] = str(video_path)
            item["download"] = {"video_path": str(video_path) if video_path else None, **dl_meta}
        else:
            item["download"] = {"skipped": True}

        if args.analyze:
            # If we didn't download in this run, try to locate an existing file by BV id.
            if video_path is None:
                candidates = sorted(out_dir.glob(f"*_{entry.bvid}.*"))
                candidates = [p for p in candidates if p.is_file() and p.suffix.lower() in {".mp4", ".mkv", ".flv", ".webm"}]
                video_path = candidates[0] if candidates else None
            if video_path is None:
                item["analysis"] = {"skipped": True, "reason": "video file not found"}
            else:
                analysis_id = _analysis_id_from_video_path(video_path, mid=mid)
                try:
                    item["analysis"] = analyze_with_pipeline_subprocess(
                        video_path,
                        analysis_id=analysis_id,
                        asr_model=asr_model,
                        frame_every_sec=args.frame_every_sec,
                        enable_ocr=bool(args.enable_ocr),
                        overwrite=bool(args.overwrite_analysis),
                    )
                except Exception as e:
                    item["analysis"] = {"is_error": True, "error": str(e), "analysis_id": analysis_id}
        else:
            item["analysis"] = {"skipped": True}

        run["items"].append(item)
        run_json.write_text(json.dumps(run, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[ok] wrote run record: {run_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
