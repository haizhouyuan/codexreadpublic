#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import io
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path


def _decode_bytes(raw: bytes, content_type: str | None) -> str:
    charset = None
    if content_type:
        m = re.search(r"charset=([A-Za-z0-9._-]+)", content_type, re.IGNORECASE)
        if m:
            charset = m.group(1)
    for enc in [charset, "utf-8", "utf-8-sig", "gb18030", "latin-1"]:
        if not enc:
            continue
        try:
            return raw.decode(enc)
        except Exception:
            continue
    return raw.decode("utf-8", errors="ignore")


def _html_to_text(page_html: str) -> str:
    # Drop scripts/styles early to reduce noise.
    s = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", page_html)
    s = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", s)
    s = re.sub(r"(?is)<!--.*?-->", " ", s)
    s = re.sub(r"(?is)<noscript[^>]*>.*?</noscript>", " ", s)

    # Convert common block separators to newlines.
    s = re.sub(r"(?i)<br\\s*/?>", "\n", s)
    s = re.sub(r"(?i)</(p|div|h1|h2|h3|h4|h5|h6|li|tr)>", "\n", s)

    # Remove tags.
    s = re.sub(r"(?is)<[^>]+>", " ", s)
    s = html.unescape(s)

    # Normalize whitespace.
    lines = []
    for line in s.splitlines():
        line = line.replace("\u00a0", " ")
        line = re.sub(r"[ \t]+", " ", line).strip()
        if not line:
            continue
        lines.append(line)
    return "\n".join(lines)


def _pdf_to_text(raw: bytes) -> str:
    try:
        from pypdf import PdfReader  # type: ignore[import-not-found]
    except Exception as exc:  # pragma: no cover - optional dependency
        raise RuntimeError("PDF detected but pypdf is not available; install pypdf to enable PDF extraction.") from exc

    reader = PdfReader(io.BytesIO(raw))
    parts: list[str] = []
    for page in reader.pages:
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        text = text.replace("\u00a0", " ")
        text = re.sub(r"[ \t]+", " ", text)
        text = "\n".join([line.strip() for line in text.splitlines() if line.strip()])
        if text:
            parts.append(text)
    return "\n\n".join(parts).strip()


def _fetch_raw(url: str, *, user_agent: str, timeout: float, accept: str) -> tuple[bytes, str | None]:
    req = urllib.request.Request(url, headers={"User-Agent": user_agent, "Accept": accept})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read(), resp.headers.get("Content-Type")
    except Exception:
        curl = shutil.which("curl")
        if not curl:
            raise

    args = [
        curl,
        "-sS",
        "-L",
        "--max-time",
        str(int(max(1, timeout))),
        "-H",
        f"Accept: {accept}",
        "-A",
        user_agent,
        url,
    ]
    completed = subprocess.run(args, capture_output=True, check=False)
    if completed.returncode != 0:
        stderr = completed.stderr.decode("utf-8", errors="ignore").strip()
        raise RuntimeError(f"curl fetch failed (code={completed.returncode}): {stderr or 'unknown error'}")
    raw = completed.stdout

    # Some download endpoints behave differently for browser-like UA and may return an HTML interstitial.
    # If it looks like HTML but the URL strongly suggests a PDF, retry with curl's default UA/headers.
    looks_like_html = raw[:64].lstrip().lower().startswith(b"<!doctype html") or raw[:32].lstrip().lower().startswith(b"<html")
    if looks_like_html and url.lower().endswith(".pdf"):
        args2 = [
            curl,
            "-sS",
            "-L",
            "--max-time",
            str(int(max(1, timeout))),
            url,
        ]
        completed2 = subprocess.run(args2, capture_output=True, check=False)
        if completed2.returncode == 0 and completed2.stdout[:4] == b"%PDF":
            return completed2.stdout, None

    return raw, None


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Fetch a URL and write a plain-text extraction to a file.")
    parser.add_argument("url")
    parser.add_argument("--out", required=True, help="Output text file path.")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--user-agent", default="codexread/0.1 (+https://github.com/haizhouyuan/codexread)")
    args = parser.parse_args(argv)

    out_path = Path(args.out).expanduser()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    accept = "text/html,*/*"
    raw, content_type = _fetch_raw(args.url, user_agent=args.user_agent, timeout=args.timeout, accept=accept)
    content_type_norm = (content_type or "").lower()

    is_pdf = (
        "application/pdf" in content_type_norm
        or args.url.lower().endswith(".pdf")
        or raw[:4] == b"%PDF"
    )
    if is_pdf:
        text = _pdf_to_text(raw)
    else:
        page_html = _decode_bytes(raw, content_type)
        text = _html_to_text(page_html)

    if not text.strip():
        raise SystemExit("empty extraction (page may be non-HTML or blocked)")

    out_path.write_text(text + "\n", encoding="utf-8")
    sys.stdout.write(str(out_path) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
