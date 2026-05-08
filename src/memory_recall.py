"""Unified recall helpers across profile, schedules, artifacts, contacts, and episodic memory."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from src import contacts
from src.agent.memory_db import db_path
from src.artifact_memory import format_artifact, search_artifacts
from src.schedule_memory import format_schedule, list_schedules


@dataclass
class RecallHit:
    source: str
    title: str
    content: str
    score: int = 1


def _words(query: str) -> set[str]:
    import re

    return {w.lower() for w in re.findall(r"[a-zA-Z0-9']{3,}", query or "")}


def _score(query_words: set[str], text: str) -> int:
    blob = (text or "").lower()
    return sum(1 for w in query_words if w in blob)


def search_memory(query: str, *, user_id: str = "default", limit: int = 12) -> str:
    q_words = _words(query)
    hits: list[RecallHit] = []

    for sched in list_schedules(include_archived=False):
        content = format_schedule(sched)
        score = _score(q_words, content)
        if score:
            hits.append(RecallHit("schedule", sched.title, content, score))

    for art in search_artifacts(query, limit=limit):
        content = format_artifact(art)
        hits.append(RecallHit("artifact", art.title, content, _score(q_words, content) or 1))

    for contact in contacts.get_all_contacts():
        content = "\n".join(f"{k}: {v}" for k, v in contact.items() if not k.startswith("_"))
        score = _score(q_words, content)
        if score:
            hits.append(RecallHit("contact", contact.get("name") or contact.get("id", "contact"), content, score))

    path = db_path(user_id)
    if path.exists():
        con = sqlite3.connect(path)
        con.row_factory = sqlite3.Row
        try:
            like = f"%{query}%"
            rows = con.execute(
                "select created_at, role, content from episodic_memory "
                "where content like ? order by created_at desc limit ?",
                (like, limit),
            ).fetchall()
            for row in rows:
                content = f"{row['created_at']} {row['role']}: {row['content']}"
                hits.append(RecallHit("episodic", row["created_at"], content, 1))

            rows = con.execute(
                "select category, value, confidence from profile_facts "
                "where deleted_at is null and value like ? order by confidence desc limit ?",
                (like, limit),
            ).fetchall()
            for row in rows:
                content = f"{row['category']}: {row['value']} (confidence {row['confidence']:.2f})"
                hits.append(RecallHit("profile", row["category"], content, 2))
        finally:
            con.close()

    hits.sort(key=lambda h: h.score, reverse=True)
    if not hits:
        return f"No memory matches for: {query}"

    lines = [f"Memory search results for: {query}"]
    for hit in hits[:limit]:
        lines.append(f"\n[{hit.source}] {hit.title}\n{hit.content[:1200]}")
    return "\n".join(lines)
