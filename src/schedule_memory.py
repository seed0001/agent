"""Durable schedule/task memory.

Schedules are structured memories, not just transcript fragments. They are
small JSON records Andrew can retrieve after restart and surface in context.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from config.settings import USER_PROFILES_DIR

SCHEDULES_PATH = USER_PROFILES_DIR / "default" / "schedules.json"


def _now() -> str:
    return datetime.now().isoformat()


def _slug(text: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9]+", "-", text.strip().lower()).strip("-")
    return value or "schedule"


def _today() -> str:
    return date.today().isoformat()


@dataclass
class ScheduleItem:
    text: str
    time: str = ""
    status: str = "pending"
    notes: str = ""

    @classmethod
    def from_any(cls, value: Any) -> "ScheduleItem":
        if isinstance(value, dict):
            return cls(
                text=str(value.get("text") or value.get("task") or "").strip(),
                time=str(value.get("time") or "").strip(),
                status=str(value.get("status") or "pending").strip() or "pending",
                notes=str(value.get("notes") or "").strip(),
            )
        return cls(text=str(value or "").strip())


@dataclass
class Schedule:
    id: str
    title: str
    date: str
    items: list[ScheduleItem]
    owner: str = "Travis"
    status: str = "active"
    source: str = "manual"
    notes: str = ""
    file_path: str = ""
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Schedule":
        return cls(
            id=str(data.get("id") or ""),
            title=str(data.get("title") or ""),
            date=str(data.get("date") or ""),
            owner=str(data.get("owner") or "Travis"),
            status=str(data.get("status") or "active"),
            source=str(data.get("source") or "manual"),
            notes=str(data.get("notes") or ""),
            file_path=str(data.get("file_path") or ""),
            created_at=str(data.get("created_at") or _now()),
            updated_at=str(data.get("updated_at") or _now()),
            items=[ScheduleItem.from_any(i) for i in data.get("items", [])],
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["items"] = [asdict(item) for item in self.items if item.text]
        return data


def _load() -> dict[str, Any]:
    if not SCHEDULES_PATH.exists():
        return {"schedules": {}}
    try:
        data = json.loads(SCHEDULES_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("schedules"), dict):
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return {"schedules": {}}


def _save(data: dict[str, Any]) -> None:
    SCHEDULES_PATH.parent.mkdir(parents=True, exist_ok=True)
    data["_updated"] = _now()
    SCHEDULES_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def remember_schedule(
    *,
    title: str,
    schedule_date: str = "",
    items: list[Any] | None = None,
    schedule_id: str = "",
    owner: str = "Travis",
    status: str = "active",
    source: str = "manual",
    notes: str = "",
    file_path: str = "",
) -> Schedule:
    """Create or replace a durable schedule memory."""
    title = (title or "").strip()
    if not title:
        raise ValueError("schedule title is required")
    schedule_date = (schedule_date or _today()).strip()
    sid = (schedule_id or f"{schedule_date}-{_slug(title)}").strip()
    now = _now()
    data = _load()
    existing = data["schedules"].get(sid, {})
    sched = Schedule(
        id=sid,
        title=title,
        date=schedule_date,
        owner=owner or "Travis",
        status=status or "active",
        source=source or "manual",
        notes=notes or "",
        file_path=file_path or "",
        created_at=existing.get("created_at") or now,
        updated_at=now,
        items=[ScheduleItem.from_any(i) for i in (items or []) if ScheduleItem.from_any(i).text],
    )
    data["schedules"][sid] = sched.to_dict()
    _save(data)
    return sched


def get_schedule(identifier: str = "") -> Schedule | None:
    """Get by id or date. Empty identifier returns today's active schedule."""
    ident = (identifier or _today()).strip()
    data = _load()
    schedules = data.get("schedules", {})
    if ident in schedules:
        return Schedule.from_dict(schedules[ident])
    matches = [
        Schedule.from_dict(s)
        for s in schedules.values()
        if str(s.get("date")) == ident and str(s.get("status", "active")) != "archived"
    ]
    if not matches:
        return None
    matches.sort(key=lambda s: s.updated_at, reverse=True)
    return matches[0]


def list_schedules(include_archived: bool = False) -> list[Schedule]:
    data = _load()
    rows = [Schedule.from_dict(s) for s in data.get("schedules", {}).values()]
    if not include_archived:
        rows = [s for s in rows if s.status != "archived"]
    rows.sort(key=lambda s: (s.date, s.updated_at), reverse=True)
    return rows


def format_schedule(schedule: Schedule) -> str:
    lines = [f"{schedule.title} ({schedule.date}) [{schedule.status}]"]
    if schedule.notes:
        lines.append(f"Notes: {schedule.notes}")
    if schedule.file_path:
        lines.append(f"File: {schedule.file_path}")
    for idx, item in enumerate(schedule.items, 1):
        prefix = f"{idx}."
        if item.time:
            prefix += f" {item.time}"
        line = f"{prefix} {item.text} [{item.status}]"
        if item.notes:
            line += f" - {item.notes}"
        lines.append(line)
    return "\n".join(lines)


def format_for_context(limit: int = 3) -> str:
    schedules = list_schedules(include_archived=False)[:limit]
    if not schedules:
        return ""
    parts = ["## Schedules / Plans (durable memory)"]
    for sched in schedules:
        parts.append(format_schedule(sched))
    return "\n\n".join(parts)
