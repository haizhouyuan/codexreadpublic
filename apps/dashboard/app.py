from __future__ import annotations

import base64
import json
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markdown_it import MarkdownIt


@dataclass(frozen=True)
class DashboardConfig:
    topics_root: Path
    tasks_db: Path
    investing_root: Path
    state_root: Path


def _load_config() -> DashboardConfig:
    topics_root = Path(os.environ.get("CODEXREAD_DASH_TOPICS_ROOT", "archives/topics")).expanduser().resolve()
    tasks_db = Path(os.environ.get("CODEXREAD_DASH_TASKS_DB", "state/tasks.sqlite")).expanduser().resolve()
    investing_root = Path(os.environ.get("CODEXREAD_DASH_INVESTING_ROOT", "archives/investing")).expanduser().resolve()
    state_root = Path(os.environ.get("CODEXREAD_DASH_STATE_ROOT", "state")).expanduser().resolve()
    return DashboardConfig(topics_root=topics_root, tasks_db=tasks_db, investing_root=investing_root, state_root=state_root)


def _has_auth_configured() -> bool:
    if (os.environ.get("CODEXREAD_DASH_TOKEN") or "").strip():
        return True
    user = (os.environ.get("CODEXREAD_DASH_BASIC_USER") or "").strip()
    password = (os.environ.get("CODEXREAD_DASH_BASIC_PASS") or "").strip()
    return bool(user and password)


def _check_auth(request: Request) -> None:
    token = (os.environ.get("CODEXREAD_DASH_TOKEN") or "").strip()
    user = (os.environ.get("CODEXREAD_DASH_BASIC_USER") or "").strip()
    password = (os.environ.get("CODEXREAD_DASH_BASIC_PASS") or "").strip()

    if not (token or (user and password)):
        return

    auth = request.headers.get("authorization") or ""

    # Accept either Bearer or Basic (if configured).
    if token and auth.lower().startswith("bearer "):
        got = auth.split(" ", 1)[1].strip()
        if got == token:
            return

    if user and password and auth.lower().startswith("basic "):
        try:
            raw = base64.b64decode(auth.split(" ", 1)[1].strip()).decode("utf-8", errors="replace")
        except Exception:
            raw = ""
        if ":" in raw:
            got_user, got_pass = raw.split(":", 1)
            if got_user == user and got_pass == password:
                return

    headers: dict[str, str] = {}
    if user and password:
        headers["WWW-Authenticate"] = "Basic"
    elif token:
        headers["WWW-Authenticate"] = "Bearer"
    raise HTTPException(status_code=401, detail="Unauthorized", headers=headers)


def _auth_guard(request: Request) -> None:
    return _check_auth(request)


_TOPIC_ID_RE = re.compile(r"^[a-z0-9_][a-z0-9_-]{0,63}$")


def _validate_topic_id(topic_id: str) -> str:
    tid = str(topic_id).strip()
    if not tid or not _TOPIC_ID_RE.match(tid):
        raise HTTPException(status_code=404, detail="topic not found")
    return tid


def _list_topics(cfg: DashboardConfig) -> list[str]:
    if not cfg.topics_root.exists():
        return []
    topics: list[str] = []
    for child in sorted(cfg.topics_root.iterdir()):
        if not child.is_dir():
            continue
        tid = child.name
        if _TOPIC_ID_RE.match(tid):
            topics.append(tid)
    return topics


@dataclass(frozen=True)
class TopicWorkflowStatus:
    topic_id: str
    stage: str
    stage_state: str
    run_id: str
    worker_id: int | None
    record_path: str
    record_filename: str
    error_path: str
    ts: str


def _load_topic_status(cfg: DashboardConfig, *, topic_id: str) -> TopicWorkflowStatus | None:
    path = cfg.state_root / "topics" / topic_id / "status.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None

    stage = str(data.get("stage") or "").strip()
    stage_state = str(data.get("stage_state") or "").strip()
    run_id = str(data.get("run_id") or "").strip()
    record_path = str(data.get("record_path") or "").strip()
    error_path = str(data.get("error_path") or "").strip()
    ts = str(data.get("ts") or "").strip()

    worker_id: int | None = None
    raw_wid = data.get("worker_id")
    if isinstance(raw_wid, int):
        worker_id = raw_wid

    record_filename = ""
    if record_path:
        try:
            record_filename = Path(record_path).name
        except Exception:
            record_filename = ""

    return TopicWorkflowStatus(
        topic_id=topic_id,
        stage=stage,
        stage_state=stage_state,
        run_id=run_id,
        worker_id=worker_id,
        record_path=record_path,
        record_filename=record_filename,
        error_path=error_path,
        ts=ts,
    )


def _pill_class(state: str) -> str:
    s = (state or "").strip().lower()
    if s in {"done", "ok", "success"}:
        return "ok"
    if s in {"failed", "error"}:
        return "danger"
    if s in {"running", "partial", "warn", "warning"}:
        return "warn"
    return ""


def _safe_resolve_under(root: Path, rel: str) -> Path:
    candidate = (root / rel).resolve()
    root_resolved = root.resolve()
    if candidate == root_resolved or root_resolved in candidate.parents:
        return candidate
    raise HTTPException(status_code=404, detail="not found")


def _strip_quotes(value: str) -> str:
    v = value.strip()
    if len(v) >= 2 and ((v[0] == v[-1] == '"') or (v[0] == v[-1] == "'")):
        return v[1:-1]
    return v


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---\n"):
        return {}, text
    lines = text.splitlines(keepends=False)
    if not lines or lines[0].strip() != "---":
        return {}, text

    end = None
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            end = idx
            break
    if end is None:
        return {}, text

    fm_lines = lines[1:end]
    rest = "\n".join(lines[end + 1 :]).lstrip("\n")

    fm: dict[str, Any] = {}
    for raw in fm_lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = re.split(r"\s+#", value.strip(), 1)[0].strip()
        value = _strip_quotes(value)
        if value.lower() in {"null", "~"}:
            value = ""
        fm[key] = value
    return fm, rest


def _markdown_renderer() -> MarkdownIt:
    md = MarkdownIt(
        "default",
        {
            "html": False,  # block raw HTML for safer external exposure
            "linkify": True,
            "typographer": True,
        },
    )
    return md


def _render_markdown(text: str) -> str:
    fm, body = _parse_frontmatter(text)
    md = _markdown_renderer()
    html = md.render(body)
    return html, fm


@dataclass(frozen=True)
class DigestItem:
    filename: str
    title: str
    published_at: str
    source_type: str
    tags: list[str]


def _parse_tags(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    # naive YAML list parsing not supported in our simple parser; accept comma-separated.
    s = str(raw).strip()
    if not s:
        return []
    if s.startswith("[") and s.endswith("]"):
        s = s[1:-1]
    return [p.strip().strip("'\"") for p in s.split(",") if p.strip()]


def _list_digests(topic_dir: Path) -> list[DigestItem]:
    digests_dir = topic_dir / "digests"
    if not digests_dir.exists():
        return []
    items: list[DigestItem] = []
    for path in sorted(digests_dir.glob("*.md"), key=lambda p: p.name, reverse=True):
        try:
            raw = path.read_text(encoding="utf-8")
        except Exception:
            continue
        fm, _body = _parse_frontmatter(raw)
        items.append(
            DigestItem(
                filename=path.name,
                title=str(fm.get("title") or "").strip() or path.stem,
                published_at=str(fm.get("published_at") or "").strip(),
                source_type=str(fm.get("source_type") or "").strip(),
                tags=_parse_tags(fm.get("tags")),
            )
        )
    return items


@dataclass(frozen=True)
class TaskRow:
    id: str
    title: str
    status: str
    category: str
    priority: str
    source: str
    updated_at: str


def _list_tasks(cfg: DashboardConfig, *, topic_id: str) -> list[TaskRow]:
    if not cfg.tasks_db.exists():
        return []
    conn = sqlite3.connect(str(cfg.tasks_db))
    try:
        cur = conn.execute(
            """
            SELECT id, title, status, COALESCE(category,''), COALESCE(priority,''), COALESCE(source,''), COALESCE(updated_at,'')
            FROM tasks
            WHERE topic_id = ?
            ORDER BY
              CASE status
                WHEN 'in_progress' THEN 0
                WHEN 'pending' THEN 1
                WHEN 'done' THEN 2
                WHEN 'canceled' THEN 3
                ELSE 9
              END,
              updated_at DESC
            """,
            (topic_id,),
        )
        rows = cur.fetchall()
        return [
            TaskRow(
                id=str(r[0]),
                title=str(r[1]),
                status=str(r[2]),
                category=str(r[3]),
                priority=str(r[4]),
                source=str(r[5]),
                updated_at=str(r[6]),
            )
            for r in rows
        ]
    finally:
        conn.close()


def _group_tasks(tasks: list[TaskRow]) -> dict[str, list[TaskRow]]:
    out: dict[str, list[TaskRow]] = {"in_progress": [], "pending": [], "done": [], "canceled": [], "other": []}
    for t in tasks:
        out.get(t.status, out["other"]).append(t)
    return out


cfg = _load_config()

app = FastAPI(title="codexread research dashboard", docs_url=None, redoc_url=None)

static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=static_dir), name="static")

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    return {
        "ok": True,
        "time": datetime.utcnow().isoformat() + "Z",
        "topics_root": str(cfg.topics_root),
        "investing_root": str(cfg.investing_root),
        "tasks_db_exists": cfg.tasks_db.exists(),
        "auth_configured": _has_auth_configured(),
    }


@app.get("/", response_class=HTMLResponse)
def home(request: Request, _auth: None = Depends(_auth_guard)):
    topics = _list_topics(cfg)
    statuses: list[TopicWorkflowStatus] = []
    for tid in topics:
        s = _load_topic_status(cfg, topic_id=tid)
        if s:
            statuses.append(s)
    return templates.TemplateResponse(
        "home.html",
        {
            "request": request,
            "topics": topics,
            "statuses": statuses,
            "pill_class": _pill_class,
            "topic_id": "",
        },
    )

@app.get("/workflow", response_class=HTMLResponse)
def workflow(request: Request, _auth: None = Depends(_auth_guard)):
    topics = _list_topics(cfg)
    statuses: list[TopicWorkflowStatus] = []
    missing: list[str] = []
    for tid in topics:
        s = _load_topic_status(cfg, topic_id=tid)
        if s:
            statuses.append(s)
        else:
            missing.append(tid)

    # Sort: running/failed first, then done.
    order = {"running": 0, "failed": 1, "partial": 2, "done": 3}
    statuses.sort(key=lambda x: (order.get(x.stage_state or "", 9), x.topic_id))
    return templates.TemplateResponse(
        "workflow.html",
        {
            "request": request,
            "topics": topics,
            "topic_id": "",
            "statuses": statuses,
            "missing_topics": missing,
            "pill_class": _pill_class,
        },
    )


@app.get("/investing/watchlist", response_class=HTMLResponse)
def investing_watchlist(request: Request, _auth: None = Depends(_auth_guard)):
    path = _safe_resolve_under(cfg.investing_root, "watchlist.md")
    if not path.exists():
        raise HTTPException(status_code=404, detail="watchlist not found")

    raw = path.read_text(encoding="utf-8")
    html, fm = _render_markdown(raw)
    return templates.TemplateResponse(
        "investing_watchlist.html",
        {
            "request": request,
            "topics": _list_topics(cfg),
            "topic_id": "",
            "title": fm.get("title") or "Investing / Watchlist",
            "content_html": html,
        },
    )

@dataclass(frozen=True)
class DecisionItem:
    filename: str
    ticker: str
    name: str
    status: str
    updated_at: str


def _list_decisions(cfg: DashboardConfig) -> list[DecisionItem]:
    decisions_dir = cfg.investing_root / "decisions"
    if not decisions_dir.exists():
        return []
    items: list[DecisionItem] = []
    for path in sorted(decisions_dir.glob("*.md"), key=lambda p: p.name, reverse=True):
        try:
            raw = path.read_text(encoding="utf-8")
        except Exception:
            continue
        fm, _body = _parse_frontmatter(raw)
        items.append(
            DecisionItem(
                filename=path.name,
                ticker=str(fm.get("ticker") or "").strip(),
                name=str(fm.get("name") or "").strip(),
                status=str(fm.get("status") or "").strip(),
                updated_at=str(fm.get("updated_at") or "").strip(),
            )
        )
    return items


@app.get("/investing/decisions", response_class=HTMLResponse)
def decisions_list(request: Request, _auth: None = Depends(_auth_guard)):
    items = _list_decisions(cfg)
    return templates.TemplateResponse(
        "decisions_list.html",
        {
            "request": request,
            "topics": _list_topics(cfg),
            "topic_id": "",
            "decisions": items,
            "pill_class": _pill_class,
        },
    )


@app.get("/investing/decisions/{decision_filename}", response_class=HTMLResponse)
def decision_view(decision_filename: str, request: Request, _auth: None = Depends(_auth_guard)):
    if "/" in decision_filename or "\\" in decision_filename or ".." in decision_filename:
        raise HTTPException(status_code=404, detail="not found")
    if not decision_filename.endswith(".md"):
        raise HTTPException(status_code=404, detail="not found")

    decisions_dir = cfg.investing_root / "decisions"
    path = _safe_resolve_under(decisions_dir, decision_filename)
    if not path.exists():
        raise HTTPException(status_code=404, detail="decision not found")

    raw = path.read_text(encoding="utf-8")
    html, fm = _render_markdown(raw)

    return templates.TemplateResponse(
        "decision_view.html",
        {
            "request": request,
            "topics": _list_topics(cfg),
            "topic_id": "",
            "decision_filename": decision_filename,
            "meta": fm,
            "content_html": html,
            "pill_class": _pill_class,
        },
    )


@app.get("/topics/{topic_id}", response_class=HTMLResponse)
def topic_root(topic_id: str, request: Request, _auth: None = Depends(_auth_guard)):
    tid = _validate_topic_id(topic_id)
    topic_dir = _safe_resolve_under(cfg.topics_root, tid)
    if not (topic_dir / "overview.md").exists():
        raise HTTPException(status_code=404, detail="topic not found")
    return topic_file(topic_id=tid, page="overview", request=request)


@dataclass(frozen=True)
class RunRecordItem:
    filename: str
    title: str
    ts: str


def _parse_run_record(path: Path) -> RunRecordItem:
    title = path.stem
    ts = ""
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
        lines = raw.splitlines()
        if lines:
            first = lines[0].strip()
            if first.startswith("#"):
                title = first.lstrip("#").strip() or title
        for ln in lines[0:60]:
            if ln.strip().startswith("- ts:"):
                ts = ln.split(":", 1)[1].strip()
                break
    except Exception:
        pass
    return RunRecordItem(filename=path.name, title=title, ts=ts)


def _list_run_records(topic_dir: Path) -> list[RunRecordItem]:
    runs_dir = topic_dir / "notes" / "runs"
    if not runs_dir.exists():
        return []
    items: list[RunRecordItem] = []
    for path in sorted(runs_dir.glob("*.md"), key=lambda p: p.name, reverse=True):
        items.append(_parse_run_record(path))
    return items


@app.get("/topics/{topic_id}/runs", response_class=HTMLResponse)
def runs_list(topic_id: str, request: Request, _auth: None = Depends(_auth_guard)):
    tid = _validate_topic_id(topic_id)
    topic_dir = _safe_resolve_under(cfg.topics_root, tid)
    items = _list_run_records(topic_dir)
    return templates.TemplateResponse(
        "runs_list.html",
        {
            "request": request,
            "topics": _list_topics(cfg),
            "topic_id": tid,
            "runs": items,
        },
    )


@app.get("/topics/{topic_id}/runs/{run_filename}", response_class=HTMLResponse)
def run_view(topic_id: str, run_filename: str, request: Request, _auth: None = Depends(_auth_guard)):
    tid = _validate_topic_id(topic_id)
    topic_dir = _safe_resolve_under(cfg.topics_root, tid)

    if "/" in run_filename or "\\" in run_filename or ".." in run_filename:
        raise HTTPException(status_code=404, detail="not found")
    if not run_filename.endswith(".md"):
        raise HTTPException(status_code=404, detail="not found")

    path = _safe_resolve_under(topic_dir / "notes" / "runs", run_filename)
    if not path.exists():
        raise HTTPException(status_code=404, detail="run record not found")

    raw = path.read_text(encoding="utf-8", errors="replace")
    html, _fm = _render_markdown(raw)
    return templates.TemplateResponse(
        "run_view.html",
        {
            "request": request,
            "topics": _list_topics(cfg),
            "topic_id": tid,
            "run_filename": run_filename,
            "content_html": html,
        },
    )

@app.get("/topics/{topic_id}/digests", response_class=HTMLResponse)
def digests_list(topic_id: str, request: Request, q: str | None = None, _auth: None = Depends(_auth_guard)):
    tid = _validate_topic_id(topic_id)
    topic_dir = _safe_resolve_under(cfg.topics_root, tid)
    items = _list_digests(topic_dir)
    query = (q or "").strip().lower()
    if query:
        items = [it for it in items if query in it.title.lower() or query in " ".join(it.tags).lower() or query in it.filename.lower()]

    return templates.TemplateResponse(
        "digests_list.html",
        {
            "request": request,
            "topics": _list_topics(cfg),
            "topic_id": tid,
            "digests": items,
            "q": q or "",
        },
    )


@app.get("/topics/{topic_id}/digests/{digest_filename}", response_class=HTMLResponse)
def digest_view(topic_id: str, digest_filename: str, request: Request, _auth: None = Depends(_auth_guard)):
    tid = _validate_topic_id(topic_id)
    topic_dir = _safe_resolve_under(cfg.topics_root, tid)

    if "/" in digest_filename or "\\" in digest_filename or ".." in digest_filename:
        raise HTTPException(status_code=404, detail="not found")
    if not digest_filename.endswith(".md"):
        raise HTTPException(status_code=404, detail="not found")

    path = _safe_resolve_under(topic_dir / "digests", digest_filename)
    if not path.exists():
        raise HTTPException(status_code=404, detail="digest not found")

    raw = path.read_text(encoding="utf-8")
    html, fm = _render_markdown(raw)

    return templates.TemplateResponse(
        "digest_view.html",
        {
            "request": request,
            "topics": _list_topics(cfg),
            "topic_id": tid,
            "digest_filename": digest_filename,
            "meta": fm,
            "meta_tags": _parse_tags(fm.get("tags")),
            "meta_entities": _parse_tags(fm.get("entities")),
            "content_html": html,
        },
    )


@app.get("/topics/{topic_id}/tasks", response_class=HTMLResponse)
def tasks_view(topic_id: str, request: Request, _auth: None = Depends(_auth_guard)):
    tid = _validate_topic_id(topic_id)
    topic_dir = _safe_resolve_under(cfg.topics_root, tid)
    _ = topic_dir  # existence check

    tasks = _list_tasks(cfg, topic_id=tid)
    grouped = _group_tasks(tasks)
    return templates.TemplateResponse(
        "tasks.html",
        {
            "request": request,
            "topics": _list_topics(cfg),
            "topic_id": tid,
            "tasks_grouped": grouped,
            "tasks_db_exists": cfg.tasks_db.exists(),
            "tasks_total": len(tasks),
        },
    )


@app.get("/topics/{topic_id}/{page}", response_class=HTMLResponse)
def topic_file(topic_id: str, page: str, request: Request, _auth: None = Depends(_auth_guard)):
    """
    Render one of the topic's canonical pages.

    NOTE: This dynamic route must be defined after the more specific routes like
    `/topics/{topic_id}/digests` and `/topics/{topic_id}/tasks` to avoid routing conflicts.
    """
    tid = _validate_topic_id(topic_id)
    topic_dir = _safe_resolve_under(cfg.topics_root, tid)

    allowed = {
        "overview": "overview.md",
        "framework": "framework.md",
        "investing": "investing.md",
        "sources": "sources.md",
        "timeline": "timeline.md",
        "open_questions": "open_questions.md",
    }
    filename = allowed.get(page)
    if not filename:
        raise HTTPException(status_code=404, detail="page not found")

    path = _safe_resolve_under(topic_dir, filename)
    if not path.exists():
        raise HTTPException(status_code=404, detail="file not found")

    raw = path.read_text(encoding="utf-8")
    html, fm = _render_markdown(raw)
    digests = _list_digests(topic_dir)

    return templates.TemplateResponse(
        "topic_file.html",
        {
            "request": request,
            "topics": _list_topics(cfg),
            "topic_id": tid,
            "page": page,
            "title": fm.get("title") or f"{tid} / {page}",
            "content_html": html,
            "digests": digests[:40],
        },
    )
