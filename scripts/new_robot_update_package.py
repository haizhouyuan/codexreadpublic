#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a pending robot update package JSON from template.")
    parser.add_argument(
        "--template",
        default="templates/robot-update-package.json",
        help="Template path (default: templates/robot-update-package.json)",
    )
    parser.add_argument(
        "--out-dir",
        default="exports/robot-update-packages",
        help="Output directory (default: exports/robot-update-packages)",
    )
    parser.add_argument("--child-user-id", default="CHILD_USER_ID", help="Child user id (placeholder allowed)")
    parser.add_argument("--u1-user-id", default="U1_USER_ID", help="U1 user id (placeholder allowed)")
    args = parser.parse_args()

    template_path = Path(args.template)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    payload = json.loads(template_path.read_text(encoding="utf-8"))
    payload["generated_at"] = now_iso()
    payload.setdefault("review", {})
    payload["review"]["required"] = True
    payload["review"]["status"] = "pending"
    payload["review"]["reviewed_by"] = None
    payload["review"]["reviewed_at"] = None

    payload.setdefault("target", {})
    payload["target"]["child_user_id"] = args.child_user_id
    payload["target"]["u1_user_id"] = args.u1_user_id

    short_id = uuid4().hex[:8]
    date = payload["generated_at"][:10]
    filename = f"{date}_{args.child_user_id}_{short_id}.json"
    out_path = out_dir / filename

    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(str(out_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

