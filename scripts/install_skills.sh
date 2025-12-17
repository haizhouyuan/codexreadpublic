#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"
DEST="$CODEX_HOME/skills"
SRC="$REPO_ROOT/skills-src"

mkdir -p "$DEST"

if [[ ! -d "$SRC" ]]; then
  echo "skills-src not found: $SRC" >&2
  exit 1
fi

echo "Installing skills to: $DEST"

for skill_dir in "$SRC"/*; do
  [[ -d "$skill_dir" ]] || continue
  name="$(basename "$skill_dir")"
  target="$DEST/$name"

  if [[ -L "$target" ]]; then
    rm "$target"
  elif [[ -e "$target" ]]; then
    echo "Skip (exists, not symlink): $target" >&2
    continue
  fi

  ln -s "$skill_dir" "$target"
  echo "Linked: $name"
done

echo "Done."

