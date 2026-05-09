"""SQLite-backed memory facade for the agent.

Public surface stays the same as the previous JSON-file implementation so
``core.py``, ``discord_bot.py``, ``web/app.py`` and friends keep working.
Internally everything routes through the Adam-inspired stores in
``memory_stores.py`` (sessions, episodic, profile facts with reinforce/decay,
background thoughts, persistent KV state).

What changed under the hood:
- One ``memory.db`` per user profile instead of a pile of JSON files.
- Profile facts have per-fact metadata (confidence, source, protected,
  last_referenced_at, version). Old ``{"facts": {category: [strings]}}`` shape
  is reconstructed only at the view layer for backward compatibility.
- Every system-prompt injection of a profile fact triggers ``reinforce(key)``
  so used facts stay healthy and forgotten ones decay (Phase 3 will run the
  decay sweeps).
- Every turn is tagged with a session id; cross-session continuity is now
  a single SQL query.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from config.settings import USER_PROFILES_DIR
from src.agent.memory_stores import (
    ContextWindow,
    EpisodicEntry,
    EpisodicStore,
    ProfileFact,
    ProfileStore,
    SessionStore,
    ThoughtStore,
    WorkingState,
    _utcnow,
)

PROFILE_CATEGORIES = ("background", "work", "preferences", "personal", "other")
_STOP_TERMS = {
    "this", "that", "with", "from", "have", "your", "they", "them", "their",
    "what", "when", "where", "would", "could", "should", "about", "like",
    "just", "want", "really", "thing", "stuff", "some", "more", "very",
    "into", "over", "then", "than", "also", "back", "still", "been", "were",
    "good", "look", "kind", "sort", "here", "there", "doing", "make", "made",
    "take", "tell", "told", "said", "says", "user", "andrew", "reply", "message",
}


@dataclass
class MemoryEntry:
    """Backward-compat shim. Kept so any caller that imports it still works.

    The new system writes to SQLite directly; this dataclass is no longer
    persisted as JSON.
    """
    content: str
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    metadata: dict[str, Any] = field(default_factory=dict)
    strength: float = 1.0

    def to_dict(self) -> dict:
        return {
            "content": self.content,
            "timestamp": self.timestamp,
            "metadata": self.metadata,
            "strength": self.strength,
        }


# ---------- Helpers ------------------------------------------------------


def _infer_role(content: str) -> str:
    """Pick an episodic role from the legacy prefix in ``content``.

    The previous code stored prefixes like ``"User: ..."`` or ``"Andrew: ..."``
    inside the content string. We preserve the content as-is for the prompt
    formatter but tag the row with a real role for queryability.
    """
    c = content.lstrip()
    low = c.lower()
    if low.startswith("user:") or low.startswith("[discord") or low.startswith("[travis"):
        return "user"
    if low.startswith("reply:") or low.startswith("[proactive") or low.startswith("[status alert"):
        return "assistant" if low.startswith("reply:") else "system"
    # "Andrew: ...", "Jarvis: ...", or any "Name: ..." → assistant
    if ":" in c[:32]:
        return "assistant"
    return "user"


def _fact_key(category: str, fact: str) -> str:
    """Stable, deterministic key derived from category + fact text.

    Same fact in the same category yields the same key, so a duplicate
    insertion versions instead of duplicating. Different wording produces a
    different key — which is correct: "Travis is a developer" and
    "Travis works in AI" are distinct facts even if related.
    """
    cat = (category or "other").lower().strip()
    body = " ".join(fact.lower().split())  # collapse whitespace
    return f"{cat}::{body[:120]}"


def _is_blank_user_input(content: str) -> bool:
    c = (content or "").strip()
    return not c or c in ("User:", "User") or (c.startswith("User:") and not c[5:].strip())


# ---------- The facade --------------------------------------------------


class MemoryStore:
    """5-layer memory: immediate, short-term, working state, episodic, profile.

    The public methods match the previous (JSON-backed) version 1:1 so
    existing call sites need no changes. New capabilities live alongside as
    optional extras (``profile``, ``episodic``, ``sessions``, ``thoughts``,
    ``state`` properties expose the underlying stores for callers that want
    the rich API).
    """

    DEFAULT_SHORT_TERM_LIMIT = 30

    def __init__(self, user_id: str = "default"):
        self.user_id = user_id
        self.user_dir = USER_PROFILES_DIR / user_id
        self.user_dir.mkdir(parents=True, exist_ok=True)

        # In-process only.
        self.immediate: list[MemoryEntry] = []

        # Underlying SQLite stores.
        self.sessions = SessionStore(user_id)
        self.episodic = EpisodicStore(user_id)
        self.profile = ProfileStore(user_id)
        self.thoughts = ThoughtStore(user_id)
        self.state = WorkingState(user_id)
        self._semantic = None  # lazy: built on first context render

        # New per-process session. Tagged onto every short_term/episodic write
        # so cross-session queries actually have something to query against.
        self._session = self.sessions.create(
            source="agent", title=f"agent boot {datetime.now().isoformat(timespec='seconds')}"
        )
        self.session_id = self._session.id
        self.episodic_cache_path = self.user_dir / "episodic_cache.jsonl"
        # Warm-load recent episodic context so restarts don't feel amnesic.
        try:
            self.load_recent_episodic_context(hours_back=72, limit=5)
        except Exception:
            pass

    def _semantic_index(self):
        """Lazy-load the semantic index. Returns None if embeddings aren't
        available (e.g. the model failed to load)."""
        if self._semantic is None:
            try:
                from src.agent.memory_embeddings import SemanticIndex
                self._semantic = SemanticIndex(self.user_id)
            except Exception:
                self._semantic = False
        return self._semantic if self._semantic is not False else None

    def find_similar(self, query: str, limit: int = 5):
        """Search past episodic content semantically. Returns ``[]`` if the
        embedding service or model isn't available."""
        idx = self._semantic_index()
        if idx is None:
            return []
        return idx.find_similar_text(query, limit=limit, source_table="episodic_memory")

    def auto_retrieve_episodic(
        self,
        user_message: str,
        days_back: int = 7,
        *,
        limit: int = 5,
    ) -> dict[str, Any]:
        """Auto-retrieve relevant episodic context for the current message."""
        text = (user_message or "").strip()
        if not text:
            return {"summary": "", "hits": [], "query_terms": []}

        query_terms = self._extract_query_terms(text)
        recent = self.episodic.get_recent_across_sessions(
            limit=250,
            within_days=max(1, int(days_back)),
            exclude_session=None,
        )
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        ranked: list[tuple[float, str, str]] = []  # score, content, source
        for row in recent:
            blob = (row.content or "").lower()
            lexical = sum(1 for t in query_terms if t in blob)
            if lexical == 0:
                continue
            age_days = max(0.0, (now - row.created_at).total_seconds() / 86400.0)
            recency_bonus = 1.0 / (1.0 + age_days)
            score = float(lexical) + float(row.importance) * 1.5 + recency_bonus
            ranked.append((score, row.content, "lexical"))

        semantic_hits = self.find_similar(text, limit=max(2, limit * 2))
        for h in semantic_hits:
            ranked.append((float(h.score) + 0.5, h.content, "semantic"))

        ranked.sort(key=lambda x: x[0], reverse=True)
        seen: set[str] = set()
        hits: list[dict[str, Any]] = []
        for score, content, source in ranked:
            c = (content or "").strip()
            if not c:
                continue
            key = c.lower()
            if key in seen:
                continue
            seen.add(key)
            hits.append(
                {
                    "score": round(score, 3),
                    "source": source,
                    "content": c,
                }
            )
            if len(hits) >= max(1, int(limit)):
                break

        if not hits:
            return {"summary": "", "hits": [], "query_terms": query_terms}

        lines = []
        for i, h in enumerate(hits[: max(1, int(limit))], start=1):
            snippet = h["content"].replace("\n", " ")
            if len(snippet) > 180:
                snippet = snippet[:177] + "..."
            lines.append(f"{i}. ({h['source']}, {h['score']:.2f}) {snippet}")
        summary = "Auto-retrieved episodic context:\n" + "\n".join(lines)

        self.state.set(
            "auto_retrieved_episodic",
            {
                "ts": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
                "query_terms": query_terms,
                "summary": summary,
                "hits": hits,
            },
        )
        self._append_episodic_cache(
            key_topics_entities=query_terms,
            condensed_summary=summary,
            importance_score=min(1.0, max(h["score"] for h in hits) / 5.0),
        )
        return {"summary": summary, "hits": hits, "query_terms": query_terms}

    def load_recent_episodic_context(self, *, hours_back: int = 72, limit: int = 5) -> str:
        """Warm-load high-importance recent episodic context into working state."""
        hours = max(1, int(hours_back))
        within_days = max(1, int((hours + 23) // 24))
        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=hours)
        recent = self.episodic.get_recent_across_sessions(
            limit=250,
            within_days=within_days,
            exclude_session=None,
        )
        candidates = [r for r in recent if r.created_at >= cutoff and float(r.importance) >= 0.55]
        if not candidates:
            fallback = [r for r in recent if r.created_at >= cutoff]
            candidates = fallback[-max(1, int(limit)) :]
        else:
            candidates.sort(key=lambda r: (r.importance, r.created_at), reverse=True)
            candidates = candidates[: max(1, int(limit))]
            candidates.sort(key=lambda r: r.created_at)

        if not candidates:
            self.state.delete("episodic_warm_start")
            return ""

        lines = []
        for r in candidates:
            ts = r.created_at.isoformat(timespec="seconds")
            snippet = (r.content or "").replace("\n", " ")
            if len(snippet) > 200:
                snippet = snippet[:197] + "..."
            lines.append(f"- [{ts}] (importance {r.importance:.2f}) {snippet}")
        block = "Warm-loaded episodic context (recent continuity):\n" + "\n".join(lines)
        self.state.set("episodic_warm_start", block)
        return block

    def _extract_query_terms(self, text: str) -> list[str]:
        terms: list[str] = []
        for token in re.findall(r"[A-Za-z][A-Za-z0-9_'-]{2,}", text):
            norm = token.lower().strip()
            if norm in _STOP_TERMS:
                continue
            if norm not in terms:
                terms.append(norm)
        # Keep the first N to avoid over-broad matching.
        return terms[:12]

    def _append_episodic_cache(
        self,
        *,
        key_topics_entities: list[str],
        condensed_summary: str,
        importance_score: float,
    ) -> None:
        entry = {
            "conversation_date": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
            "key_topics_entities": key_topics_entities,
            "condensed_summary": condensed_summary,
            "importance_score": round(max(0.0, min(1.0, float(importance_score))), 3),
        }
        self.episodic_cache_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.episodic_cache_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    # -- compatibility properties --------------------------------------

    @property
    def short_term(self) -> list[MemoryEntry]:
        """Most recent N turns from this session, materialized as ``MemoryEntry``.

        Provided so legacy code that iterates ``memory.short_term`` keeps working.
        """
        rows = self.episodic.get_recent_in_session(self.session_id, limit=self.DEFAULT_SHORT_TERM_LIMIT)
        return [
            MemoryEntry(
                content=r.content,
                timestamp=r.created_at.isoformat(),
                metadata=r.metadata,
                strength=r.strength,
            )
            for r in rows
        ]

    @property
    def working(self) -> dict[str, Any]:
        return self.state.all()

    # -- writes --------------------------------------------------------

    def add_immediate(self, content: str, **metadata: Any) -> None:
        """In-process scratchpad for the current turn. Not persisted."""
        self.immediate.append(MemoryEntry(content=content, metadata=metadata))

    def clear_immediate(self) -> None:
        self.immediate.clear()

    def add_short_term(self, content: str, **metadata: Any) -> None:
        """Record a turn in the current session. Persists to SQLite immediately."""
        if _is_blank_user_input(content):
            return
        self.sessions.touch(self.session_id)
        self.episodic.insert(
            session_id=self.session_id,
            role=_infer_role(content),
            content=content,
            source=metadata.get("source", "agent"),
            metadata=metadata or None,
        )

    def append_thought(self, content: str, strength: float = 1.0) -> None:
        """Andrew's stream-of-consciousness. Stored separately from turns."""
        if not content or not content.strip():
            return
        self.thoughts.insert(
            content.strip(),
            session_id=self.session_id,
            strength=strength,
        )

    def add_profile_fact(self, category: str, fact: str) -> str:
        """Add or version a profile fact. Returns a human-readable confirmation.

        Facts entered via this path are treated as user-confirmed (source="user",
        protected by default). The consolidator's auto-extracted facts use a
        different code path with ``source="extracted"`` so the lifecycle can
        decay them without touching what the human said.
        """
        cat = (category or "other").lower()
        if cat not in PROFILE_CATEGORIES and cat != "general":
            cat = "other"
        fact = (fact or "").strip()
        if not fact:
            return "Empty fact, not stored."
        key = _fact_key(cat, fact)
        existing = self.profile.get(key)
        try:
            self.profile.set(key, fact, category=cat, source="user", protected=True)
        except ValueError as e:
            return f"Could not store fact: {e}"
        verb = "Updated" if existing else "Stored"
        return f"{verb} in profile ({cat})."

    # -- working-state KV (replaces working.json) ---------------------

    def set_working(self, key: str, value: Any) -> None:
        self.state.set(key, value)

    def get_working(self, key: str, default: Any = None) -> Any:
        return self.state.get(key, default)

    # -- prompt assembly ----------------------------------------------

    def get_context_for_agent(self, max_immediate: int = 5, max_short: int = 20) -> str:
        """Build the memory context block injected into the agent's system prompt.

        Side effect: every profile fact that ends up in the block triggers
        ``ProfileStore.reinforce(key)`` — the lifecycle's reinforcement signal.
        Facts that are actively used stay healthy; facts ignored for weeks
        eventually decay below the floor and get pruned.
        """
        parts: list[str] = []

        if self.immediate:
            recent = self.immediate[-max_immediate:]
            parts.append("## Immediate context (current turn)")
            for e in recent:
                parts.append(f"- {e.content}")

        recent_turns = self.episodic.get_recent_in_session(self.session_id, limit=max_short)
        recent_ids = {r.id for r in recent_turns}
        if recent_turns:
            parts.append("\n## Recent conversation")
            for r in recent_turns:
                ts = r.created_at.isoformat(timespec="seconds")
                parts.append(f"- [{ts}] {r.content}")

        # High-importance older turns from this same session that didn't make
        # the recent window. These are the "I should remember this" moments —
        # scored by the consolidator. Skipped if importance scoring hasn't run
        # yet (everything still at 0.5).
        important = self.episodic.get_top_importance_in_session(
            self.session_id, limit=8, exclude_ids=recent_ids
        )
        if important:
            parts.append("\n## Important earlier in this session")
            for r in important:
                ts = r.created_at.isoformat(timespec="seconds")
                parts.append(f"- [{ts}] (importance {r.importance:.2f}) {r.content}")

        # Cross-session continuity: a few turns from prior sessions so Andrew
        # remembers what we talked about yesterday.
        cross = self.episodic.get_recent_across_sessions(
            limit=10, within_days=7, exclude_session=self.session_id
        )
        cross_ids = {r.id for r in cross}
        if cross:
            parts.append("\n## Earlier sessions (recent)")
            for r in cross:
                ts = r.created_at.isoformat(timespec="seconds")
                parts.append(f"- [{ts}] {r.content}")

        # Retrieval-augmented memory: semantically similar prior turns to the
        # most recent user message. Falls back silently if embeddings aren't
        # available. Skip anything already shown above to avoid duplication.
        if self.immediate:
            query = self.immediate[-1].content or ""
            if query.strip():
                idx = self._semantic_index()
                if idx is not None:
                    exclude = {r.id for r in recent_turns} | cross_ids
                    hits = idx.find_similar_text(
                        query, limit=4, source_table="episodic_memory",
                        exclude_source_ids=exclude,
                    )
                    if hits:
                        parts.append("\n## Semantically related past turns")
                        for h in hits:
                            parts.append(f"- (similarity {h.score:.2f}) {h.content}")

        kv = self.state.all()
        if kv:
            parts.append("\n## Working memory (active task)")
            for k, v in kv.items():
                parts.append(f"- {k}: {v}")

        try:
            from src.schedule_memory import format_for_context as _schedule_context
            schedule_block = _schedule_context(limit=3)
        except Exception:
            schedule_block = ""
        if schedule_block:
            parts.append("\n" + schedule_block)

        try:
            from src.artifact_memory import format_for_context as _artifact_context
            artifact_block = _artifact_context(limit=8)
        except Exception:
            artifact_block = ""
        if artifact_block:
            parts.append("\n" + artifact_block)

        recent_thoughts = self.thoughts.get_recent(limit=8, delivered_only=False)
        if recent_thoughts:
            parts.append("\n## Your recent background thoughts")
            for t in recent_thoughts:
                parts.append(f"- {t.content}")

        # Profile facts — and reinforce every key that gets injected.
        top_facts = self.profile.get_top(limit=30, min_confidence=0.0)
        if top_facts:
            grouped = self._group_by_category(top_facts)
            parts.append("\n## User profile (remember this person)")
            keys_used: list[str] = []
            for cat in (*PROFILE_CATEGORIES, "general"):
                items = grouped.get(cat, [])
                if not items:
                    continue
                label = cat.replace("_", " ").title()
                values = "; ".join(f.value for f in items)
                parts.append(f"{label}: {values}")
                keys_used.extend(f.key for f in items)
            if keys_used:
                self.profile.reinforce_many(keys_used)

        return "\n".join(parts) if parts else ""

    @staticmethod
    def _group_by_category(facts: list[ProfileFact]) -> dict[str, list[ProfileFact]]:
        out: dict[str, list[ProfileFact]] = {}
        for f in facts:
            out.setdefault(f.category, []).append(f)
        return out

    # -- views (web UI) ------------------------------------------------

    def get_profile_view(self) -> dict:
        """Legacy shape ``{"facts": {cat: [str, ...]}, "summary": "", "updated": ts}``.

        Reconstructed from the new schema so existing UI code keeps working.
        Adds an ``entries`` field with the rich per-fact metadata so the new
        UI panel (Phase 5) can show health bars without a separate endpoint.
        """
        facts = self.profile.get_all()
        legacy: dict[str, list[str]] = {}
        rich: list[dict] = []
        latest_update = ""
        for f in facts:
            legacy.setdefault(f.category, []).append(f.value)
            rich.append({
                "key": f.key,
                "value": f.value,
                "category": f.category,
                "confidence": round(f.confidence, 4),
                "source": f.source,
                "protected": f.protected,
                "version": f.version,
                "last_referenced_at": (
                    f.last_referenced_at.isoformat() if f.last_referenced_at else None
                ),
                "updated_at": f.updated_at.isoformat(),
            })
            ts = f.updated_at.isoformat()
            if ts > latest_update:
                latest_update = ts
        return {
            "facts": legacy,
            "summary": "",
            "updated": latest_update,
            "entries": rich,
        }

    def get_episodic_view(self, max_items: int = 100) -> list[dict]:
        """Newest-first list, matching the previous shape."""
        rows = self.episodic.get_recent_across_sessions(limit=max_items, within_days=365)
        rows = list(reversed(rows))  # newest first for display
        return [
            {
                "content": r.content,
                "ts": r.created_at.isoformat(),
                "session_id": r.session_id,
                "role": r.role,
                "importance": round(r.importance, 3),
            }
            for r in rows
        ]

    def get_working_view(self) -> dict:
        return self.state.all()

    def get_thoughts_view(self, max_items: int = 50) -> list[dict]:
        rows = self.thoughts.get_recent(limit=max_items, delivered_only=False)
        rows = list(reversed(rows))
        return [
            {
                "content": t.content,
                "ts": t.created_at.isoformat(),
                "delivered": t.delivered,
                "reject_reason": t.reject_reason,
            }
            for t in rows
        ]

    # -- session lifecycle (new) --------------------------------------

    def end_session(self) -> None:
        """Mark the current session ended. Optional — sessions don't need to
        be explicitly closed for queries to work."""
        self.sessions.end(self.session_id)
