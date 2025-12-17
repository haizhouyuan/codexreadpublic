#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path


def _read_template(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _parse_frontmatter(text: str) -> tuple[str, str]:
    """
    Return (frontmatter_text_including_markers, body_text).
    If no frontmatter, returns ("", original_text).
    """
    if not text.startswith("---\n"):
        return "", text
    lines = text.splitlines(keepends=False)
    if not lines or lines[0].strip() != "---":
        return "", text
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        return "", text
    fm = "\n".join(lines[: end + 1]) + "\n"
    body = "\n".join(lines[end + 1 :]).lstrip("\n")
    return fm, body


def _today() -> str:
    return date.today().isoformat()


def _default_output_path(*, ticker: str) -> Path:
    stamp = _today()
    out_dir = Path("archives/investing/decisions")
    return out_dir / f"{stamp}_{ticker.upper()}_decision.md"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create a new Decision Package markdown from template.")
    parser.add_argument("--ticker", required=True, help="Ticker or stable slug (recommended: ticker).")
    parser.add_argument("--name", default="", help="Company name (optional).")
    parser.add_argument(
        "--topic-id",
        action="append",
        default=[],
        help="Associated topic_id (repeatable). Example: --topic-id ai_compute --topic-id optical_modules",
    )
    parser.add_argument("--template", default="templates/decision_package.md")
    parser.add_argument("--out", default="", help="Output path (default: archives/investing/decisions/YYYY-MM-DD_TICKER_decision.md)")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)

    ticker = str(args.ticker).strip()
    if not ticker:
        raise SystemExit("ticker is required")
    name = str(args.name or "").strip()

    topic_ids = [str(t).strip() for t in (args.topic_id or []) if str(t).strip()]
    if not topic_ids:
        raise SystemExit("at least one --topic-id is required")

    template_path = Path(args.template).expanduser()
    if not template_path.exists():
        raise SystemExit(f"template not found: {template_path}")

    template_text = _read_template(template_path)
    _fm, body = _parse_frontmatter(template_text)

    stamp = _today()
    decision_id = f"{stamp}_{ticker.upper()}"
    out_path = Path(args.out).expanduser() if args.out else _default_output_path(ticker=ticker)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists() and not args.overwrite:
        raise SystemExit(f"output exists (use --overwrite): {out_path}")

    topic_ids_inline = ", ".join([f'\"{t}\"' for t in topic_ids])
    fm = (
        "---\n"
        f"ticker: \"{ticker.upper()}\"\n"
        f"name: \"{name}\"\n"
        f"topic_ids: [{topic_ids_inline}]\n"
        f"decision_id: \"{decision_id}\"\n"
        "status: \"draft\" # draft|reviewed|active|closed\n"
        f"created_at: \"{stamp}\"\n"
        f"updated_at: \"{stamp}\"\n"
        "---\n\n"
    )

    body_filled = body.replace("<ticker>", ticker.upper()).replace("<name>", name or "")
    out_path.write_text(fm + body_filled, encoding="utf-8")
    print(str(out_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

