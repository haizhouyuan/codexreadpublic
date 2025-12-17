#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable, List

DEFAULT_OUT_DIR = Path("state") / "public-repos" / "codexreadpublic"
EXPORT_MARKER_FILENAME = ".codexreadpublic_export_marker"

INCLUDE_PREFIXES = (
    "mcp-servers/",
    "scripts/",
    "apps/",
    "templates/",
    "skills-src/",
    "examples/",
)

EXCLUDE_PREFIXES = (
    # User explicitly requested no "codex/" folder in the public repo.
    # Keep Codex config examples in the private repo only.
    "examples/codex/",
)

INCLUDE_ROOT_EXACT = {
    "AGENTS.md",
    "spec.md",
}

INCLUDE_ROOT_SUFFIXES = (
    "-spec.md",
)

EXTRA_REL_PATHS = (
    # Keep the export workflow self-contained in the public repo even if these
    # files are not tracked in the private repo yet.
    "public-repo-spec.md",
    "scripts/export_public_repo.py",
)


def _repo_root() -> Path:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"],
            stderr=subprocess.STDOUT,
            text=True,
        ).strip()
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Not a git repo (or git unavailable): {e.output.strip()}") from e
    return Path(out).resolve(strict=False)


def _git_ls_files(repo_root: Path) -> List[str]:
    raw = subprocess.check_output(["git", "ls-files", "-z"], cwd=repo_root)
    decoded = raw.decode("utf-8", errors="surrogateescape")
    return [p for p in decoded.split("\0") if p]


def _is_included(path: str) -> bool:
    if any(path.startswith(prefix) for prefix in EXCLUDE_PREFIXES):
        return False
    if "/" not in path:
        if path in INCLUDE_ROOT_EXACT:
            return True
        return any(path.endswith(suffix) for suffix in INCLUDE_ROOT_SUFFIXES)
    return any(path.startswith(prefix) for prefix in INCLUDE_PREFIXES)


def _safe_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _public_readme() -> str:
    return """# codexreadpublic

本仓库是 `codexread` 的公开子集，用于代码审查/重构 review。

包含：
- `mcp-servers/`、`scripts/`、`apps/`、`templates/`、`skills-src/`、`examples/`
- 根目录的核心规格/契约：`spec.md` 与 `*-spec.md`

不包含（避免隐私/产物误入公开仓库）：
- `archives/`（主题档案/投研资产）
- `imports/`（原始输入，含敏感数据）
- `exports/`（导出产物）
- `state/`、`logs/`、`.specstory/`、`notes/`、`codex/`、`.env*`

说明：
- 所有凭证仅允许通过环境变量注入；不要把 token/key 写入仓库文件。
"""


def _public_gitignore() -> str:
    return """# Python
__pycache__/
*.pyc
*.pyo
*.pyd
.venv/
venv/
.python-version

# Local state / runtime / outputs (do not commit)
state/
logs/
imports/
exports/
archives/

# Session exports / AI history (do not commit)
.specstory/
codex-session-*.md

# Local tooling (do not commit)
codex/

# Env files (credentials must never be committed)
.env
.env.*

# Editors
.vscode/
.cursor/
.idea/
.DS_Store
"""


def _ensure_empty_or_overwrite(out_dir: Path, *, overwrite: bool) -> None:
    if not out_dir.exists():
        return
    if not out_dir.is_dir():
        raise RuntimeError(f"--out must be a directory path: {out_dir}")
    if not overwrite:
        raise RuntimeError(f"Output directory already exists (use --overwrite): {out_dir}")

    marker = out_dir / EXPORT_MARKER_FILENAME
    if not marker.exists():
        raise RuntimeError(
            f"Refusing to overwrite a directory without export marker {EXPORT_MARKER_FILENAME}: {out_dir}"
        )
    shutil.rmtree(out_dir)


def _copy_files(repo_root: Path, out_dir: Path, rel_paths: Iterable[str]) -> None:
    for rel in rel_paths:
        src = repo_root / rel
        dst = out_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def main(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="export_public_repo",
        description="Export a safe, public-review subset of this repo into a clean directory.",
    )
    parser.add_argument(
        "--out",
        default=str(DEFAULT_OUT_DIR),
        help=f"Output directory (default: {DEFAULT_OUT_DIR})",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing output directory (only if it contains export marker).",
    )
    args = parser.parse_args(argv)

    repo_root = _repo_root()
    out_dir_raw = Path(str(args.out).strip())
    out_dir = out_dir_raw if out_dir_raw.is_absolute() else (repo_root / out_dir_raw)
    out_dir = out_dir.resolve(strict=False)

    tracked_paths = _git_ls_files(repo_root)
    selected_set = {p for p in tracked_paths if _is_included(p)}
    for rel in EXTRA_REL_PATHS:
        rel_norm = str(rel).strip().lstrip("/")
        if not rel_norm:
            continue
        candidate = (repo_root / rel_norm).resolve(strict=False)
        if not candidate.is_file():
            continue
        try:
            candidate.relative_to(repo_root)
        except Exception:
            continue
        if _is_included(rel_norm) or rel_norm in EXTRA_REL_PATHS:
            selected_set.add(rel_norm)

    selected = sorted(selected_set)

    if not selected:
        raise RuntimeError("No files selected; check INCLUDE_PREFIXES / INCLUDE_ROOT_* rules.")

    _ensure_empty_or_overwrite(out_dir, overwrite=bool(args.overwrite))
    out_dir.mkdir(parents=True, exist_ok=True)

    _safe_write_text(out_dir / EXPORT_MARKER_FILENAME, "generated_by=scripts/export_public_repo.py\n")
    _copy_files(repo_root, out_dir, selected)

    _safe_write_text(out_dir / "README.md", _public_readme())
    _safe_write_text(out_dir / ".gitignore", _public_gitignore())

    sys.stdout.write(f"Exported {len(selected)} tracked file(s) to: {out_dir}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
