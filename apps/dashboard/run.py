#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from pathlib import Path


def _has_auth_configured() -> bool:
    if (os.environ.get("CODEXREAD_DASH_TOKEN") or "").strip():
        return True
    user = (os.environ.get("CODEXREAD_DASH_BASIC_USER") or "").strip()
    password = (os.environ.get("CODEXREAD_DASH_BASIC_PASS") or "").strip()
    return bool(user and password)


def main() -> int:
    # Ensure repo root is importable when running as a script (e.g. `python apps/dashboard/run.py`).
    repo_root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(repo_root))

    host = (os.environ.get("CODEXREAD_DASH_HOST") or "127.0.0.1").strip()
    port_raw = (os.environ.get("CODEXREAD_DASH_PORT") or "8787").strip()
    try:
        port = int(port_raw)
    except ValueError:
        raise SystemExit(f"Invalid CODEXREAD_DASH_PORT: {port_raw!r}")

    if host not in {"127.0.0.1", "localhost"} and not _has_auth_configured():
        raise SystemExit(
            "Refusing to start dashboard on a non-loopback host without auth. "
            "Set CODEXREAD_DASH_BASIC_USER+CODEXREAD_DASH_BASIC_PASS or CODEXREAD_DASH_TOKEN."
        )

    import uvicorn

    reload = (os.environ.get("CODEXREAD_DASH_RELOAD") or "").strip().lower() in {"1", "true", "yes", "y", "on"}
    uvicorn.run(
        "apps.dashboard.app:app",
        host=host,
        port=port,
        reload=reload,
        log_level=(os.environ.get("CODEXREAD_DASH_LOG_LEVEL") or "info").strip().lower(),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
