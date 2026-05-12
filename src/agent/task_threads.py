"""Task-thread store — explicit threads of work / discussion with status.

A thread is the agent's first-class memory of "something we're doing together":
a project, a question, a problem, a directive. Threads have a status
(``open / blocked / done / abandoned``), an owner (``user / andrew / shared``),
related artifacts, and a last-touched timestamp.

Why this exists separately from ``profile_facts`` and ``episodic_memory``:

- Episodic memory is a transcript: lossy by design, hard to query by topic.
- Profile facts are durable identity / preference statements.
- Threads are *active state* — what we're working on, where we left off,
  what's blocked on whom. They're what makes "what were we doing yesterday?"
  a SQL query instead of a prayer.

Threads are surfaced into the continuity ledger and into the prompt context.
The agent gets a tool (``manage_thread``) to open / close / update / list them.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable

from src.agent.memory_db import get_connection, transaction
from src.agent.memory_stores import _now_iso, _parse_iso, _utcnow, _uuid

VALID_STATUS = ("open", "blocked", "done", "abandoned")
VALID_OWNERS = ("user", "andrew", "shared", None)


@dataclass
class TaskThread:
    id: str
    title: str
    description: str | None
    status: str
    owner: str | None
    related_artifacts: list[str]
    tags: list[str]
    session_id: str | None
    last_touched_at: datetime
    created_at: datetime
    closed_at: datetime | None


def _row_to_thread(row: sqlite3.Row) -> TaskThread:
    try:
        artifacts = json.loads(row["related_artifacts"] or "[]")
        if not isinstance(artifacts, list):
            artifacts = []
    except (json.JSONDecodeError, TypeError):
        artifacts = []
    try:
        tags = json.loads(row["tags"] or "[]")
        if not isinstance(tags, list):
            tags = []
    except (json.JSONDecodeError, TypeError):
        tags = []
    return TaskThread(
        id=row["id"],
        title=row["title"],
        description=row["description"],
        status=row["status"],
        owner=row["owner"],
        related_artifacts=[str(a) for a in artifacts],
        tags=[str(t) for t in tags],
        session_id=row["session_id"],
        last_touched_at=_parse_iso(row["last_touched_at"]) or _utcnow(),
        created_at=_parse_iso(row["created_at"]) or _utcnow(),
        closed_at=_parse_iso(row["closed_at"]),
    )


class TaskThreadStore:
    """Persistence layer for ``task_threads`` rows."""

    def __init__(self, user_id: str = "default"):
        self.user_id = user_id

    def _conn(self) -> sqlite3.Connection:
        return get_connection(self.user_id)

    # ---- writes ----

    def open(
        self,
        title: str,
        *,
        description: str | None = None,
        owner: str | None = "shared",
        tags: Iterable[str] | None = None,
        related_artifacts: Iterable[str] | None = None,
        session_id: str | None = None,
    ) -> TaskThread:
        title = (title or "").strip()
        if not title:
            raise ValueError("thread title is required")
        if owner not in VALID_OWNERS:
            owner = "shared"
        tid = _uuid()
        now = _now_iso()
        artifacts_json = json.dumps([str(a) for a in (related_artifacts or [])], ensure_ascii=False)
        tags_json = json.dumps([str(t) for t in (tags or [])], ensure_ascii=False)
        with transaction(self.user_id) as conn:
            conn.execute(
                "INSERT INTO task_threads "
                "(id, title, description, status, owner, related_artifacts, tags, "
                " session_id, last_touched_at, created_at) "
                "VALUES (?, ?, ?, 'open', ?, ?, ?, ?, ?, ?)",
                (tid, title, description, owner, artifacts_json, tags_json,
                 session_id, now, now),
            )
        return self.get(tid)  # type: ignore[return-value]

    def update(
        self,
        thread_id: str,
        *,
        title: str | None = None,
        description: str | None = None,
        status: str | None = None,
        owner: str | None = None,
        related_artifacts: Iterable[str] | None = None,
        tags: Iterable[str] | None = None,
    ) -> TaskThread | None:
        existing = self.get(thread_id)
        if existing is None:
            return None
        fields: list[str] = []
        params: list[Any] = []
        if title is not None:
            fields.append("title = ?")
            params.append(title.strip())
        if description is not None:
            fields.append("description = ?")
            params.append(description.strip() if description else None)
        if status is not None:
            if status not in VALID_STATUS:
                raise ValueError(f"invalid status: {status}")
            fields.append("status = ?")
            params.append(status)
            if status in ("done", "abandoned"):
                fields.append("closed_at = ?")
                params.append(_now_iso())
        if owner is not None:
            if owner not in VALID_OWNERS:
                raise ValueError(f"invalid owner: {owner}")
            fields.append("owner = ?")
            params.append(owner)
        if related_artifacts is not None:
            fields.append("related_artifacts = ?")
            params.append(json.dumps([str(a) for a in related_artifacts], ensure_ascii=False))
        if tags is not None:
            fields.append("tags = ?")
            params.append(json.dumps([str(t) for t in tags], ensure_ascii=False))
        fields.append("last_touched_at = ?")
        params.append(_now_iso())
        params.append(thread_id)
        self._conn().execute(
            f"UPDATE task_threads SET {', '.join(fields)} "
            f"WHERE id = ? AND deleted_at IS NULL",
            tuple(params),
        )
        return self.get(thread_id)

    def touch(self, thread_id: str) -> None:
        """Refresh last-touched without changing status — call when a thread
        is referenced in conversation."""
        self._conn().execute(
            "UPDATE task_threads SET last_touched_at = ? "
            "WHERE id = ? AND deleted_at IS NULL",
            (_now_iso(), thread_id),
        )

    def close(self, thread_id: str, status: str = "done") -> bool:
        if status not in ("done", "abandoned"):
            raise ValueError("close status must be 'done' or 'abandoned'")
        cur = self._conn().execute(
            "UPDATE task_threads SET status = ?, closed_at = ?, last_touched_at = ? "
            "WHERE id = ? AND deleted_at IS NULL AND status NOT IN ('done','abandoned')",
            (status, _now_iso(), _now_iso(), thread_id),
        )
        return cur.rowcount > 0

    def delete(self, thread_id: str) -> bool:
        cur = self._conn().execute(
            "UPDATE task_threads SET deleted_at = ? "
            "WHERE id = ? AND deleted_at IS NULL",
            (_now_iso(), thread_id),
        )
        return cur.rowcount > 0

    # ---- reads ----

    def get(self, thread_id: str) -> TaskThread | None:
        row = self._conn().execute(
            "SELECT * FROM task_threads WHERE id = ? AND deleted_at IS NULL",
            (thread_id,),
        ).fetchone()
        return _row_to_thread(row) if row else None

    def list_open(self, limit: int = 25) -> list[TaskThread]:
        rows = self._conn().execute(
            "SELECT * FROM task_threads "
            "WHERE deleted_at IS NULL AND status IN ('open','blocked') "
            "ORDER BY last_touched_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [_row_to_thread(r) for r in rows]

    def list_recent(self, limit: int = 25, status: str | None = None) -> list[TaskThread]:
        if status:
            if status not in VALID_STATUS:
                raise ValueError(f"invalid status: {status}")
            rows = self._conn().execute(
                "SELECT * FROM task_threads "
                "WHERE deleted_at IS NULL AND status = ? "
                "ORDER BY last_touched_at DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = self._conn().execute(
                "SELECT * FROM task_threads "
                "WHERE deleted_at IS NULL "
                "ORDER BY last_touched_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [_row_to_thread(r) for r in rows]

    def search(self, query: str, limit: int = 10) -> list[TaskThread]:
        q = (query or "").strip()
        if not q:
            return []
        like = f"%{q}%"
        rows = self._conn().execute(
            "SELECT * FROM task_threads "
            "WHERE deleted_at IS NULL "
            "AND (title LIKE ? OR description LIKE ? OR tags LIKE ?) "
            "ORDER BY last_touched_at DESC LIMIT ?",
            (like, like, like, limit),
        ).fetchall()
        return [_row_to_thread(r) for r in rows]

    def find_by_title(self, title: str) -> TaskThread | None:
        """Case-insensitive exact title lookup. Used for de-duping when the
        agent re-opens a thread that already exists."""
        norm = (title or "").strip().lower()
        if not norm:
            return None
        rows = self._conn().execute(
            "SELECT * FROM task_threads WHERE deleted_at IS NULL "
            "AND LOWER(title) = ? "
            "ORDER BY last_touched_at DESC LIMIT 1",
            (norm,),
        ).fetchall()
        return _row_to_thread(rows[0]) if rows else None

    def counts(self) -> dict[str, int]:
        rows = self._conn().execute(
            "SELECT status, COUNT(*) AS n FROM task_threads "
            "WHERE deleted_at IS NULL GROUP BY status"
        ).fetchall()
        out = {s: 0 for s in VALID_STATUS}
        for r in rows:
            out[r["status"]] = int(r["n"])
        return out


# ---------- prompt rendering ---------------------------------------------


def render_threads_for_prompt(threads: list[TaskThread], *, max_chars: int = 1800) -> str:
    """Compact human-readable block of threads, oldest-touched first.

    Returns "" if empty so callers can do a falsy check.
    """
    if not threads:
        return ""
    lines: list[str] = []
    used = 0
    # Sort: open first, then blocked, then done/abandoned. Within each, most recent.
    order = {"open": 0, "blocked": 1, "done": 2, "abandoned": 3}
    threads_sorted = sorted(
        threads,
        key=lambda t: (order.get(t.status, 9), -t.last_touched_at.timestamp()),
    )
    for t in threads_sorted:
        ts = t.last_touched_at.isoformat(timespec="minutes")
        marker = {
            "open": "[ ]",
            "blocked": "[!]",
            "done": "[x]",
            "abandoned": "[~]",
        }.get(t.status, "[?]")
        owner = f" ({t.owner})" if t.owner and t.owner != "shared" else ""
        line = f"{marker} {t.title}{owner}  — touched {ts}"
        if t.description:
            d = t.description.strip().replace("\n", " ")
            if len(d) > 220:
                d = d[:217] + "..."
            line += f"\n    {d}"
        if used + len(line) > max_chars:
            lines.append(f"... and {len(threads_sorted) - len(lines)} more thread(s) (truncated)")
            break
        lines.append(line)
        used += len(line) + 1
    return "\n".join(lines)
