#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import tempfile
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Dict, List


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _atomic_write_text(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding=encoding,
            delete=False,
            dir=str(path.parent),
            prefix=path.name + ".tmp.",
        ) as f:
            tmp_path = Path(f.name)
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(str(tmp_path), str(path))
    finally:
        if tmp_path is not None:
            try:
                tmp_path.unlink()
            except FileNotFoundError:
                pass


def _state_dir(repo_root: Path, topic_id: str) -> Path:
    return repo_root / "state" / "topics" / topic_id


def _status_path(repo_root: Path, topic_id: str) -> Path:
    return _state_dir(repo_root, topic_id) / "status.json"


def _manifest_path(repo_root: Path, topic_id: str, run_id: str) -> Path:
    return _state_dir(repo_root, topic_id) / "runs" / run_id / "manifest.json"


def _load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _parse_artifacts(items: List[str]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for item in items:
        if "=" not in item:
            raise SystemExit(f"Bad --artifact value (expected label=path): {item}")
        label, raw_path = item.split("=", 1)
        label = label.strip()
        raw_path = raw_path.strip()
        if not label or not raw_path:
            raise SystemExit(f"Bad --artifact value (expected label=path): {item}")
        p = Path(raw_path)
        if not p.exists():
            raise SystemExit(f"Artifact not found: {p}")
        out[label] = _load_json(p)
    return out


def cmd_status(args: argparse.Namespace) -> int:
    repo_root = _repo_root()
    topic_id = args.topic_id.strip()
    if not topic_id:
        raise SystemExit("topic_id is required")

    payload: Dict[str, Any] = {
        "schema_version": "1",
        "topic_id": topic_id,
        "topic_title": (args.topic_title or "").strip() or None,
        "run_id": (args.run_id or "").strip() or None,
        "stage": (args.stage or "").strip() or None,
        "stage_state": args.state,
        "worker_id": args.worker_id,
        "record_path": (args.record_path or "").strip() or None,
        "error_path": (args.error_path or "").strip() or None,
        "ts": _now_iso(),
    }
    _atomic_write_text(_status_path(repo_root, topic_id), json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return 0


def cmd_manifest(args: argparse.Namespace) -> int:
    repo_root = _repo_root()
    topic_id = args.topic_id.strip()
    run_id = args.run_id.strip()
    if not topic_id:
        raise SystemExit("topic_id is required")
    if not run_id:
        raise SystemExit("run_id is required")

    stage = (args.stage or "").strip() or "unknown"
    stage_state = args.stage_state
    now = _now_iso()

    path = _manifest_path(repo_root, topic_id, run_id)
    if path.exists():
        manifest = _load_json(path)
    else:
        manifest = {
            "schema_version": "1",
            "topic_id": topic_id,
            "topic_title": (args.topic_title or "").strip() or None,
            "run_id": run_id,
            "created_at": now,
            "stages": {},
        }

    manifest["topic_title"] = (args.topic_title or "").strip() or manifest.get("topic_title")
    manifest["updated_at"] = now

    stages = manifest.get("stages")
    if not isinstance(stages, dict):
        stages = {}
        manifest["stages"] = stages

    artifacts = _parse_artifacts(args.artifact or [])
    stage_entry: Dict[str, Any] = {
        "state": stage_state,
        "updated_at": now,
        "worker_id": args.worker_id,
        "record_path": (args.record_path or "").strip() or None,
    }
    if artifacts:
        stage_entry["artifacts"] = artifacts
    stages[stage] = stage_entry

    _atomic_write_text(path, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    return 0


def main(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(description="Write topic/run status and run manifest JSON under state/topics/...")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_status = sub.add_parser("status", help="Update per-topic status.json")
    p_status.add_argument("--topic-id", required=True)
    p_status.add_argument("--topic-title", default=None)
    p_status.add_argument("--run-id", default=None)
    p_status.add_argument("--stage", default=None)
    p_status.add_argument("--state", required=True, choices=["running", "done", "failed", "partial"])
    p_status.add_argument("--worker-id", type=int, default=None)
    p_status.add_argument("--record-path", default=None)
    p_status.add_argument("--error-path", default=None)
    p_status.set_defaults(func=cmd_status)

    p_manifest = sub.add_parser("manifest", help="Update per-run manifest.json")
    p_manifest.add_argument("--topic-id", required=True)
    p_manifest.add_argument("--topic-title", default=None)
    p_manifest.add_argument("--run-id", required=True)
    p_manifest.add_argument("--stage", default="unknown")
    p_manifest.add_argument("--stage-state", required=True, choices=["running", "done", "failed", "partial"])
    p_manifest.add_argument("--worker-id", type=int, default=None)
    p_manifest.add_argument("--record-path", default=None)
    p_manifest.add_argument("--artifact", action="append", default=[], help="Repeatable: label=/path/to/json")
    p_manifest.set_defaults(func=cmd_manifest)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
