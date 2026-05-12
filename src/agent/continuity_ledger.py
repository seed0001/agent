"""Continuity ledger — the rolling document pinned to the top of the prompt.

The ledger is the agent's "self briefing." It captures, in a single readable
document:

- **Identity** — who the user is, key facts about them, what they call the
  agent, the agent's name and basic self-frame.
- **Active projects** — long-running work the agent is helping with.
- **Open threads** — explicit ``task_threads`` rows in ``open`` or ``blocked``.
- **Recent decisions** — high-importance assistant turns from the last ~7 days
  (a thin signal that "this happened and mattered").
- **Unresolved directives** — recent user requests that don't have a matching
  completion yet.
- **Active routines** — schedule_memory rows the user explicitly recorded.

Two surfaces:

- ``ContinuityLedger`` — DB persistence (versioned rows, latest is canonical).
- ``LedgerBuilder`` — composes a fresh ledger from the live memory state.

The ledger is rebuilt by:

- The consolidator (periodic, in the background loop).
- ``MemoryStore.__init__`` startup reconciliation (cheap heuristic build if no
  ledger exists yet — gives a useful pin even before the consolidator runs).
- The deterministic recall router on explicit requests (``do you remember``
  / ``recap`` / etc.).

Design principles:

- The ledger is never the *only* source — it's a summary. The underlying
  stores remain the source of truth.
- Cheap to render (no LLM call needed for the heuristic build). Optional
  LLM call to polish prose, but the structured sections work without it.
- Always small enough to fit at the top of the system prompt without
  pushing out conversation context (default cap ≈ 4 KB).
"""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Iterable

from src.agent.memory_db import get_connection
from src.agent.memory_stores import _now_iso, _parse_iso, _utcnow

DEFAULT_LEDGER_CHAR_BUDGET = 4000


# ---------- Storage ------------------------------------------------------


@dataclass
class LedgerRow:
    id: int
    content: str
    sections: dict[str, Any]
    version: int
    built_by: str
    created_at: datetime


def _row_to_ledger(row: sqlite3.Row) -> LedgerRow:
    try:
        sections = json.loads(row["sections"] or "{}")
        if not isinstance(sections, dict):
            sections = {}
    except (json.JSONDecodeError, TypeError):
        sections = {}
    return LedgerRow(
        id=int(row["id"]),
        content=row["content"],
        sections=sections,
        version=int(row["version"]),
        built_by=row["built_by"],
        created_at=_parse_iso(row["created_at"]) or _utcnow(),
    )


class ContinuityLedger:
    """Versioned persistence of the rolling continuity document."""

    def __init__(self, user_id: str = "default"):
        self.user_id = user_id

    def _conn(self) -> sqlite3.Connection:
        return get_connection(self.user_id)

    def get_latest(self) -> LedgerRow | None:
        row = self._conn().execute(
            "SELECT * FROM continuity_ledger ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return _row_to_ledger(row) if row else None

    def write(
        self,
        content: str,
        sections: dict[str, Any] | None = None,
        *,
        built_by: str = "consolidator",
    ) -> LedgerRow:
        latest = self.get_latest()
        next_version = (latest.version + 1) if latest else 1
        sections_json = json.dumps(sections or {}, ensure_ascii=False, default=str)
        self._conn().execute(
            "INSERT INTO continuity_ledger (content, sections, version, built_by, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (content, sections_json, next_version, built_by, _now_iso()),
        )
        return self.get_latest()  # type: ignore[return-value]

    def history(self, limit: int = 10) -> list[LedgerRow]:
        rows = self._conn().execute(
            "SELECT * FROM continuity_ledger ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [_row_to_ledger(r) for r in rows]

    def get_pinned_block(self, *, max_chars: int = DEFAULT_LEDGER_CHAR_BUDGET) -> str:
        """The thing that goes at the TOP of the system prompt.

        Returns "" if no ledger has been built yet — the caller should treat
        that as "render nothing" rather than "render an empty block".
        """
        row = self.get_latest()
        if not row or not (row.content or "").strip():
            return ""
        body = row.content.strip()
        if len(body) > max_chars:
            body = body[: max_chars - 32].rstrip() + "\n... [continuity ledger truncated]"
        ts = row.created_at.isoformat(timespec="minutes")
        return (
            "## Continuity ledger\n"
            f"_Last consolidated {ts} (v{row.version}, built by {row.built_by})._\n\n"
            f"{body}"
        )


# ---------- Builder ------------------------------------------------------


# Heuristic keywords the user typically uses for directives that should be
# tracked as unresolved if no completion appears.
_DIRECTIVE_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"\bremember (to|that)\b",
        r"\b(can you|please|could you) (build|fix|add|create|make|write|implement)\b",
        r"\b(don'?t forget|don'?t lose) (to|about)\b",
        r"\b(let'?s|we (need|should)) (build|fix|add|create|make|write|implement|do)\b",
        r"\bnext time\b",
        r"\b(do it|fucking wire it up|wire it up|hook it up)\b",
        r"^\s*(do|fix|build|add|create|make|write|implement)\b",
    ]
]

# Words that suggest an assistant turn is reporting a *completion* of work.
_COMPLETION_HINTS = re.compile(
    r"\b(done|fixed|added|created|built|implemented|wired|patched|"
    r"committed|pushed|deployed|merged|saved|finished|shipped)\b",
    re.IGNORECASE,
)

# Words that suggest a turn was a meaningful decision/commitment.
_DECISION_HINTS = re.compile(
    r"\b(decid|chose|going with|switching to|will (not |never )?(use|do|build|ship)|"
    r"won'?t|never (use|do|run|switch)|standardiz|commit(ted)? to|agreed)\b",
    re.IGNORECASE,
)


@dataclass
class LedgerSections:
    identity: list[str] = field(default_factory=list)
    projects: list[str] = field(default_factory=list)
    open_threads: list[dict[str, Any]] = field(default_factory=list)
    recent_decisions: list[dict[str, Any]] = field(default_factory=list)
    unresolved_directives: list[dict[str, Any]] = field(default_factory=list)
    active_routines: list[str] = field(default_factory=list)
    last_session_summary: str = ""
    stats: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "identity": self.identity,
            "projects": self.projects,
            "open_threads": self.open_threads,
            "recent_decisions": self.recent_decisions,
            "unresolved_directives": self.unresolved_directives,
            "active_routines": self.active_routines,
            "last_session_summary": self.last_session_summary,
            "stats": self.stats,
        }


def _strip_prefix(text: str) -> str:
    t = (text or "").strip()
    # Strip "User: ", "Andrew: ", "Reply: ", "[discord ...] " etc.
    t = re.sub(r"^(user|andrew|reply|jarvis|assistant|system)\s*:\s*", "", t, flags=re.IGNORECASE)
    t = re.sub(r"^\[[^\]]+\]\s*", "", t).strip()
    return t


def _truncate(text: str, n: int) -> str:
    t = (text or "").replace("\n", " ").strip()
    if len(t) <= n:
        return t
    return t[: max(0, n - 3)] + "..."


class LedgerBuilder:
    """Composes a ledger from the live ``MemoryStore`` state.

    No LLM required. The output is a deterministic markdown document plus
    a structured ``sections`` dict for callers that want the parts.
    """

    def __init__(
        self,
        memory: Any,  # circular-import shy: we just need the duck-typed surface
        *,
        decision_lookback_days: int = 7,
        directive_lookback_hours: int = 48,
        max_open_threads: int = 12,
        max_decisions: int = 8,
        max_directives: int = 8,
    ):
        self.memory = memory
        self.decision_lookback_days = decision_lookback_days
        self.directive_lookback_hours = directive_lookback_hours
        self.max_open_threads = max_open_threads
        self.max_decisions = max_decisions
        self.max_directives = max_directives

    # ---- section builders ----

    def _build_identity(self) -> list[str]:
        out: list[str] = []
        try:
            from src import soul
            s = soul.load_soul()
        except Exception:
            s = None
        if s:
            owner = (s.get("owner_name") or "").strip()
            agent = (s.get("agent_name") or "").strip()
            tone = (s.get("agent_tone") or "").strip()
            if owner and agent:
                out.append(f"You are {agent}; you talk to {owner}.")
            elif agent:
                out.append(f"Your name is {agent}.")
            elif owner:
                out.append(f"You talk to {owner}.")
            if tone:
                out.append(f"Tone: {tone}")
        # Top profile facts in identity-bearing categories
        try:
            facts = self.memory.profile.get_top(limit=20, min_confidence=0.4)
        except Exception:
            facts = []
        identity_cats = {"background", "personal", "work", "preferences"}
        seen_values: set[str] = set()
        for f in facts:
            if f.category not in identity_cats:
                continue
            v = (f.value or "").strip()
            if not v or v.lower() in seen_values:
                continue
            seen_values.add(v.lower())
            out.append(v)
            if len(out) >= 10:
                break
        return out

    def _build_open_threads(self) -> list[dict[str, Any]]:
        try:
            threads = self.memory.threads.list_open(limit=self.max_open_threads)
        except Exception:
            return []
        out: list[dict[str, Any]] = []
        for t in threads:
            out.append({
                "id": t.id,
                "title": t.title,
                "status": t.status,
                "owner": t.owner,
                "description": _truncate(t.description or "", 220),
                "last_touched_at": t.last_touched_at.isoformat(timespec="minutes"),
            })
        return out

    def _build_recent_decisions(self) -> list[dict[str, Any]]:
        cutoff = _utcnow() - timedelta(days=self.decision_lookback_days)
        try:
            recent = self.memory.episodic.get_recent_across_sessions(
                limit=400,
                within_days=max(1, self.decision_lookback_days),
                exclude_session=None,
            )
        except Exception:
            recent = []
        out: list[dict[str, Any]] = []
        for r in recent:
            if r.created_at < cutoff:
                continue
            if r.role not in ("assistant", "user"):
                continue
            text = _strip_prefix(r.content)
            if not text:
                continue
            is_decision = (
                bool(_DECISION_HINTS.search(text))
                or float(r.importance) >= 0.8
            )
            if not is_decision:
                continue
            out.append({
                "id": r.id,
                "role": r.role,
                "ts": r.created_at.isoformat(timespec="minutes"),
                "text": _truncate(text, 220),
            })
        # Newest first; cap.
        out.sort(key=lambda d: d["ts"], reverse=True)
        return out[: self.max_decisions]

    def _build_unresolved_directives(self) -> list[dict[str, Any]]:
        cutoff = _utcnow() - timedelta(hours=self.directive_lookback_hours)
        try:
            recent = self.memory.episodic.get_recent_across_sessions(
                limit=400,
                within_days=max(1, (self.directive_lookback_hours + 23) // 24),
                exclude_session=None,
            )
        except Exception:
            recent = []
        # Walk chronologically to pair user directives with subsequent assistant completions.
        recent_sorted = sorted(recent, key=lambda r: r.created_at)
        directives: list[dict[str, Any]] = []
        for i, r in enumerate(recent_sorted):
            if r.created_at < cutoff or r.role != "user":
                continue
            text = _strip_prefix(r.content)
            if not text or len(text) < 4:
                continue
            if not any(p.search(text) for p in _DIRECTIVE_PATTERNS):
                continue
            # Look ahead for an assistant completion within ~10 turns.
            resolved = False
            for follower in recent_sorted[i + 1 : i + 12]:
                if follower.role != "assistant":
                    continue
                if _COMPLETION_HINTS.search(_strip_prefix(follower.content)):
                    resolved = True
                    break
            if resolved:
                continue
            directives.append({
                "id": r.id,
                "ts": r.created_at.isoformat(timespec="minutes"),
                "text": _truncate(text, 220),
            })
        # Newest first, capped.
        directives.sort(key=lambda d: d["ts"], reverse=True)
        return directives[: self.max_directives]

    def _build_active_routines(self) -> list[str]:
        try:
            from src.schedule_memory import list_schedules
            sched = list_schedules(include_archived=False)
        except Exception:
            return []
        out: list[str] = []
        for s in sched[:5]:
            title = getattr(s, "title", None) or getattr(s, "name", None) or "schedule"
            out.append(str(title))
        return out

    def _build_projects(self) -> list[str]:
        out: list[str] = []
        try:
            facts = self.memory.profile.get_all(category="work", min_confidence=0.4)
        except Exception:
            facts = []
        seen: set[str] = set()
        for f in facts:
            v = (f.value or "").strip()
            if not v or v.lower() in seen:
                continue
            seen.add(v.lower())
            out.append(v)
            if len(out) >= 8:
                break
        # Add titles of any "open" threads not already covered as a soft project list.
        try:
            threads = self.memory.threads.list_open(limit=8)
        except Exception:
            threads = []
        for t in threads:
            title = (t.title or "").strip()
            if title and title.lower() not in seen and len(out) < 12:
                seen.add(title.lower())
                out.append(f"thread: {title}")
        return out

    def _build_last_session_summary(self) -> str:
        try:
            sessions = self.memory.sessions.list_recent(limit=4)
        except Exception:
            sessions = []
        # Use the most recent session that ISN'T the current one.
        current_id = getattr(self.memory, "session_id", None)
        prior = next((s for s in sessions if s.id != current_id), None)
        if prior is None:
            return ""
        try:
            turns = self.memory.episodic.get_by_session(prior.id, limit=200)
        except Exception:
            turns = []
        if not turns:
            return ""
        # Take the last 6 meaningful turns (skip blanks / pure system).
        meaningful = [t for t in turns if t.role in ("user", "assistant") and (t.content or "").strip()]
        if not meaningful:
            return ""
        tail = meaningful[-6:]
        ts = prior.last_activity_at.isoformat(timespec="minutes") if prior.last_activity_at else "(unknown)"
        lines = [f"Prior session ended around {ts}:"]
        for t in tail:
            who = "you" if t.role == "assistant" else "they"
            lines.append(f"  {who}: {_truncate(_strip_prefix(t.content), 180)}")
        return "\n".join(lines)

    def _build_stats(self) -> dict[str, Any]:
        try:
            ep_count = self.memory.episodic.count()
        except Exception:
            ep_count = 0
        try:
            thread_counts = self.memory.threads.counts()
        except Exception:
            thread_counts = {}
        try:
            sessions = self.memory.sessions.list_recent(limit=200)
        except Exception:
            sessions = []
        return {
            "episodic_rows": ep_count,
            "session_count": len(sessions),
            "threads": thread_counts,
        }

    # ---- top-level build ----

    def build_sections(self) -> LedgerSections:
        return LedgerSections(
            identity=self._build_identity(),
            projects=self._build_projects(),
            open_threads=self._build_open_threads(),
            recent_decisions=self._build_recent_decisions(),
            unresolved_directives=self._build_unresolved_directives(),
            active_routines=self._build_active_routines(),
            last_session_summary=self._build_last_session_summary(),
            stats=self._build_stats(),
        )

    def render(self, sections: LedgerSections) -> str:
        """Render the structured sections as compact markdown.

        Output is deliberately deterministic — no LLM required. The
        consolidator may layer a polish-pass later.
        """
        parts: list[str] = []

        if sections.identity:
            parts.append("**Who you're with**")
            for line in sections.identity:
                parts.append(f"- {line}")

        if sections.projects:
            parts.append("\n**Active projects**")
            for line in sections.projects:
                parts.append(f"- {line}")

        if sections.open_threads:
            parts.append("\n**Open threads**")
            for t in sections.open_threads:
                marker = "[!]" if t["status"] == "blocked" else "[ ]"
                desc = f" — {t['description']}" if t.get("description") else ""
                parts.append(f"- {marker} {t['title']}{desc}  _(touched {t['last_touched_at']})_")

        if sections.unresolved_directives:
            parts.append("\n**Unresolved directives** (recent things they asked for that may not be done)")
            for d in sections.unresolved_directives:
                parts.append(f"- ({d['ts']}) {d['text']}")

        if sections.recent_decisions:
            parts.append("\n**Recent decisions / commitments**")
            for d in sections.recent_decisions:
                who = "you said" if d["role"] == "assistant" else "they said"
                parts.append(f"- ({d['ts']}, {who}) {d['text']}")

        if sections.active_routines:
            parts.append("\n**Active routines**")
            for line in sections.active_routines:
                parts.append(f"- {line}")

        if sections.last_session_summary:
            parts.append("\n**Where you left off**")
            parts.append(sections.last_session_summary)

        return "\n".join(parts).strip()

    def build_and_persist(
        self,
        ledger: ContinuityLedger,
        *,
        built_by: str = "builder",
    ) -> LedgerRow | None:
        """Build sections, render, and persist as a new ledger row.

        Returns the new row, or ``None`` if the build produced no content
        (e.g. brand-new profile with no facts, threads, or episodic memory).
        """
        sections = self.build_sections()
        body = self.render(sections)
        # If the body is empty AND there are no episodic rows, don't bother
        # persisting — keeps the ledger from accumulating empty placeholders.
        if not body.strip():
            stats = sections.stats or {}
            if not stats.get("episodic_rows"):
                return None
            body = "_(continuity ledger has no salient items yet — nothing learned to pin)_"
        return ledger.write(body, sections=sections.to_dict(), built_by=built_by)
