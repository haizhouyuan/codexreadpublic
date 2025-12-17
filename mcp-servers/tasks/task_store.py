from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Iterable, List, Optional
from uuid import uuid4


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _ensure_parent_dir(path: str) -> None:
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)


class TaskStoreError(RuntimeError):
    pass


ALLOWED_CATEGORIES = {"investing", "tech", "parenting", "personal", "other"}
ALLOWED_PRIORITIES = {"low", "medium", "high"}
ALLOWED_STATUSES = {"pending", "in_progress", "done", "canceled"}
ALLOWED_ORDER_BY = {"created_at_desc", "updated_at_desc", "priority_desc"}


@dataclass(frozen=True)
class Task:
    id: str
    title: str
    description: Optional[str]
    category: Optional[str]
    status: str
    priority: Optional[str]
    tags: List[str]
    topic_id: Optional[str]
    source: Optional[str]
    created_at: str
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "category": self.category,
            "status": self.status,
            "priority": self.priority,
            "tags": self.tags,
            "topic_id": self.topic_id,
            "source": self.source,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def _normalize_tags(tags: Any) -> List[str]:
    if tags is None:
        return []
    if isinstance(tags, list):
        out: List[str] = []
        for item in tags:
            if isinstance(item, str) and item.strip():
                out.append(item.strip())
        return out
    raise TaskStoreError("tags must be an array of strings")


def _priority_rank(priority: Optional[str]) -> int:
    if priority == "high":
        return 3
    if priority == "medium":
        return 2
    if priority == "low":
        return 1
    return 0


class TaskStore:
    def __init__(self, *, db_path: str) -> None:
        self._db_path = db_path

    def ensure_schema(self) -> None:
        _ensure_parent_dir(self._db_path)
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                  id TEXT PRIMARY KEY,
                  title TEXT NOT NULL,
                  description TEXT,
                  category TEXT,
                  status TEXT NOT NULL,
                  priority TEXT,
                  tags_json TEXT NOT NULL,
                  topic_id TEXT,
                  source TEXT,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_topic_id ON tasks(topic_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_updated_at ON tasks(updated_at)")

    def create_task(
        self,
        *,
        title: Any,
        description: Any = None,
        category: Any = None,
        priority: Any = None,
        tags: Any = None,
        topic_id: Any = None,
        source: Any = None,
    ) -> Task:
        if not isinstance(title, str) or not title.strip():
            raise TaskStoreError("title is required")

        if description is not None and not isinstance(description, str):
            raise TaskStoreError("description must be a string")

        if category is not None and not isinstance(category, str):
            raise TaskStoreError("category must be a string")
        if isinstance(category, str):
            category = category.strip() or None
        if category is not None and category not in ALLOWED_CATEGORIES:
            raise TaskStoreError(f"invalid category (allowed: {sorted(ALLOWED_CATEGORIES)})")

        if priority is not None and not isinstance(priority, str):
            raise TaskStoreError("priority must be a string")
        if isinstance(priority, str):
            priority = priority.strip() or None
        if priority is not None and priority not in ALLOWED_PRIORITIES:
            raise TaskStoreError(f"invalid priority (allowed: {sorted(ALLOWED_PRIORITIES)})")

        if topic_id is not None and not isinstance(topic_id, str):
            raise TaskStoreError("topic_id must be a string")
        if isinstance(topic_id, str):
            topic_id = topic_id.strip() or None

        if source is not None and not isinstance(source, str):
            raise TaskStoreError("source must be a string")
        if isinstance(source, str):
            source = source.strip() or None

        normalized_tags = _normalize_tags(tags)

        task_id = f"task_{uuid4().hex}"
        created_at = _now_iso()
        updated_at = created_at

        status = "pending"
        normalized_priority = priority or "medium"

        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """
                INSERT INTO tasks (
                  id, title, description, category, status, priority, tags_json, topic_id, source, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    title.strip(),
                    description,
                    category,
                    status,
                    normalized_priority,
                    json.dumps(normalized_tags, ensure_ascii=False),
                    topic_id,
                    source,
                    created_at,
                    updated_at,
                ),
            )

        return Task(
            id=task_id,
            title=title.strip(),
            description=description,
            category=category,
            status=status,
            priority=normalized_priority,
            tags=normalized_tags,
            topic_id=topic_id,
            source=source,
            created_at=created_at,
            updated_at=updated_at,
        )

    def list_tasks(
        self,
        *,
        status: Any = None,
        category: Any = None,
        topic_id: Any = None,
        tags_any: Any = None,
        order_by: Any = None,
        limit: Any = None,
    ) -> List[Task]:
        if status is not None and not isinstance(status, str):
            raise TaskStoreError("status must be a string")
        if isinstance(status, str):
            status = status.strip() or None
        if status is not None and status not in ALLOWED_STATUSES:
            raise TaskStoreError(f"invalid status (allowed: {sorted(ALLOWED_STATUSES)})")
        if category is not None and not isinstance(category, str):
            raise TaskStoreError("category must be a string")
        if isinstance(category, str):
            category = category.strip() or None
        if category is not None and category not in ALLOWED_CATEGORIES:
            raise TaskStoreError(f"invalid category (allowed: {sorted(ALLOWED_CATEGORIES)})")
        if topic_id is not None and not isinstance(topic_id, str):
            raise TaskStoreError("topic_id must be a string")
        if isinstance(topic_id, str):
            topic_id = topic_id.strip() or None
        if order_by is not None and not isinstance(order_by, str):
            raise TaskStoreError("order_by must be a string")
        if isinstance(order_by, str):
            order_by = order_by.strip() or None
        if order_by is not None and order_by not in ALLOWED_ORDER_BY:
            raise TaskStoreError(f"invalid order_by (allowed: {sorted(ALLOWED_ORDER_BY)})")

        normalized_tags_any = _normalize_tags(tags_any)

        normalized_limit = 50
        if limit is not None:
            if not isinstance(limit, int):
                raise TaskStoreError("limit must be an integer")
            if limit < 1 or limit > 200:
                raise TaskStoreError("limit must be between 1 and 200")
            normalized_limit = limit

        where: List[str] = []
        args: List[Any] = []
        if status:
            where.append("status = ?")
            args.append(status)
        if category:
            where.append("category = ?")
            args.append(category)
        if topic_id:
            where.append("topic_id = ?")
            args.append(topic_id)

        where_sql = ("WHERE " + " AND ".join(where)) if where else ""

        if order_by == "updated_at_desc":
            order_sql = "updated_at DESC"
        elif order_by == "priority_desc":
            order_sql = (
                "CASE priority WHEN 'high' THEN 3 WHEN 'medium' THEN 2 WHEN 'low' THEN 1 ELSE 0 END DESC, "
                "updated_at DESC"
            )
        else:
            order_sql = "created_at DESC"

        query = f"""
            SELECT
              id, title, description, category, status, priority, tags_json, topic_id, source, created_at, updated_at
            FROM tasks
            {where_sql}
            ORDER BY {order_sql}
            LIMIT ? OFFSET ?
        """

        tasks: List[Task] = []
        tags_any_set = set(normalized_tags_any)

        page_size = normalized_limit if not normalized_tags_any else min(normalized_limit * 5, 1000)
        offset = 0
        max_pages = 50 if normalized_tags_any else 1

        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            for _ in range(max_pages):
                rows = conn.execute(query, args + [page_size, offset]).fetchall()
                if not rows:
                    break

                for row in rows:
                    tags = json.loads(row["tags_json"]) if row["tags_json"] else []
                    if tags_any_set and not (set(tags) & tags_any_set):
                        continue

                    tasks.append(
                        Task(
                            id=row["id"],
                            title=row["title"],
                            description=row["description"],
                            category=row["category"],
                            status=row["status"],
                            priority=row["priority"],
                            tags=tags,
                            topic_id=row["topic_id"],
                            source=row["source"],
                            created_at=row["created_at"],
                            updated_at=row["updated_at"],
                        )
                    )
                    if len(tasks) >= normalized_limit:
                        break

                if len(tasks) >= normalized_limit:
                    break

                offset += page_size

        return tasks

    def update_task_status(self, *, task_id: Any, status: Any) -> Task:
        if not isinstance(task_id, str) or not task_id:
            raise TaskStoreError("id is required")
        if not isinstance(status, str) or not status:
            raise TaskStoreError("status is required")
        status = status.strip()
        if status not in ALLOWED_STATUSES:
            raise TaskStoreError("invalid status")

        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """
                SELECT
                  id, title, description, category, status, priority, tags_json, topic_id, source, created_at, updated_at
                FROM tasks
                WHERE id = ?
                """,
                (task_id,),
            ).fetchone()
            if row is None:
                raise TaskStoreError("task not found")

            updated_at = _now_iso()
            conn.execute(
                "UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?",
                (status, updated_at, task_id),
            )

        tags = json.loads(row["tags_json"]) if row["tags_json"] else []
        return Task(
            id=row["id"],
            title=row["title"],
            description=row["description"],
            category=row["category"],
            status=status,
            priority=row["priority"],
            tags=tags,
            topic_id=row["topic_id"],
            source=row["source"],
            created_at=row["created_at"],
            updated_at=updated_at,
        )
