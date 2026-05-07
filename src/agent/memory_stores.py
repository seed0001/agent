"""Typed stores layered on top of ``memory_db``.

Each store is a thin object that wraps the SQLite connection for a profile and
exposes a focused API. They're intentionally independent — the agent composes
them, so a future refactor can swap any of them out without touching the
others.

Lifecycle model is lifted from seed0001/Adam:

- ``ProfileStore.reinforce(key)`` is called every time a fact is injected
  into a prompt. Confidence climbs toward 1.0 and ``last_referenced_at`` is
  refreshed.
- ``ProfileStore.decay(half_life_days, min_confidence)`` is run periodically
  by the consolidator. Unprotected facts shrink exponentially based on time
  since last reference; anything below ``min_confidence`` is soft-deleted.
- User-source facts and explicitly ``protect()``-ed facts are immortal.
- Every write to ``profile_facts`` creates a new row with bumped ``version``
  while soft-deleting the previous row, so the full history is queryable.
"""
from __future__ import annotations

import json
import math
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from src.agent.memory_db import get_connection, transaction

# ---------- Shared helpers ----------


def _uuid() -> str:
    return uuid.uuid4().hex


def _utcnow() -> datetime:
    """Naive UTC datetime. SQLite stores timestamps as text, so we strip the
    tzinfo after computing UTC to match the schema's ``datetime('now')`` shape.
    Use this everywhere instead of ``datetime.utcnow()`` (deprecated in 3.12).
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _now_iso() -> str:
    """Microsecond-precision ISO string. Microseconds matter — ``datetime('now')``
    in SQLite is only second-precision, which makes "most recent" queries
    indeterminate when multiple rows are inserted in the same second.
    """
    return _utcnow().isoformat(sep=" ", timespec="microseconds")


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        s = value.replace("Z", "")
        if "T" not in s and " " in s:
            s = s.replace(" ", "T", 1)
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    except (ValueError, TypeError):
        return None


def _audit(conn: sqlite3.Connection, action: str, table: str | None = None,
           target_id: str | None = None, **details: Any) -> None:
    conn.execute(
        "INSERT INTO memory_audit (action, target_table, target_id, details) "
        "VALUES (?, ?, ?, ?)",
        (action, table, target_id, json.dumps(details, ensure_ascii=False) if details else None),
    )


# ---------- Sessions ----------


@dataclass
class Session:
    id: str
    source: str
    channel_id: str | None
    user_id: str | None
    title: str | None
    started_at: datetime
    last_activity_at: datetime
    ended_at: datetime | None
    metadata: dict[str, Any]


class SessionStore:
    """Tracks the lifecycle of conversation sessions.

    A session is created when the agent starts and tagged onto every episodic
    write. Sessions enable cross-session queries ("what did Travis and I talk
    about yesterday?") and let the consolidator scope its work.
    """

    def __init__(self, user_id: str = "default"):
        self.user_id = user_id

    def _conn(self) -> sqlite3.Connection:
        return get_connection(self.user_id)

    def create(self, source: str, channel_id: str | None = None,
               human_user_id: str | None = None, title: str | None = None,
               metadata: dict[str, Any] | None = None) -> Session:
        sid = _uuid()
        now = _now_iso()
        meta_str = json.dumps(metadata or {}, ensure_ascii=False)
        with transaction(self.user_id) as conn:
            conn.execute(
                "INSERT INTO sessions "
                "(id, source, channel_id, user_id, title, started_at, last_activity_at, metadata) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (sid, source, channel_id, human_user_id, title, now, now, meta_str),
            )
        return Session(
            id=sid, source=source, channel_id=channel_id, user_id=human_user_id,
            title=title, started_at=_parse_iso(now) or _utcnow(),
            last_activity_at=_parse_iso(now) or _utcnow(),
            ended_at=None, metadata=metadata or {},
        )

    def touch(self, session_id: str) -> None:
        """Mark the session as recently active. Cheap, called per turn."""
        self._conn().execute(
            "UPDATE sessions SET last_activity_at = ? WHERE id = ?",
            (_now_iso(), session_id),
        )

    def end(self, session_id: str) -> None:
        self._conn().execute(
            "UPDATE sessions SET ended_at = ? WHERE id = ? AND ended_at IS NULL",
            (_now_iso(), session_id),
        )

    def get(self, session_id: str) -> Session | None:
        row = self._conn().execute(
            "SELECT * FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        return _row_to_session(row) if row else None

    def list_recent(self, limit: int = 20, source: str | None = None) -> list[Session]:
        if source:
            rows = self._conn().execute(
                "SELECT * FROM sessions WHERE source = ? "
                "ORDER BY last_activity_at DESC LIMIT ?",
                (source, limit),
            ).fetchall()
        else:
            rows = self._conn().execute(
                "SELECT * FROM sessions ORDER BY last_activity_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [_row_to_session(r) for r in rows]


def _row_to_session(row: sqlite3.Row) -> Session:
    try:
        meta = json.loads(row["metadata"] or "{}")
    except (json.JSONDecodeError, TypeError):
        meta = {}
    return Session(
        id=row["id"],
        source=row["source"],
        channel_id=row["channel_id"],
        user_id=row["user_id"],
        title=row["title"],
        started_at=_parse_iso(row["started_at"]) or _utcnow(),
        last_activity_at=_parse_iso(row["last_activity_at"]) or _utcnow(),
        ended_at=_parse_iso(row["ended_at"]),
        metadata=meta,
    )


# ---------- Episodic ----------


@dataclass
class EpisodicEntry:
    id: str
    session_id: str
    role: str
    content: str
    source: str
    task_id: str | None
    importance: float
    strength: float
    embedding_id: str | None
    metadata: dict[str, Any]
    created_at: datetime


class EpisodicStore:
    """Every conversation turn lands here, tagged by session.

    The retention model is generous — by default we keep everything and let
    the consolidator distill old turns into profile facts. Hard deletes are
    rare; soft-delete via ``deleted_at`` keeps audit trails intact.
    """

    def __init__(self, user_id: str = "default"):
        self.user_id = user_id

    def _conn(self) -> sqlite3.Connection:
        return get_connection(self.user_id)

    def insert(self, session_id: str, role: str, content: str,
               source: str = "unknown", task_id: str | None = None,
               importance: float = 0.5, strength: float = 1.0,
               metadata: dict[str, Any] | None = None) -> str:
        eid = _uuid()
        meta_str = json.dumps(metadata or {}, ensure_ascii=False)
        self._conn().execute(
            "INSERT INTO episodic_memory "
            "(id, session_id, role, content, source, task_id, importance, strength, metadata, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (eid, session_id, role, content, source, task_id,
             max(0.0, min(1.0, importance)), max(0.0, strength), meta_str, _now_iso()),
        )
        return eid

    def get_by_session(self, session_id: str, limit: int = 100) -> list[EpisodicEntry]:
        rows = self._conn().execute(
            "SELECT * FROM episodic_memory "
            "WHERE session_id = ? AND deleted_at IS NULL "
            "ORDER BY rowid ASC LIMIT ?",
            (session_id, limit),
        ).fetchall()
        return [_row_to_episodic(r) for r in rows]

    def get_recent_in_session(self, session_id: str, limit: int = 20) -> list[EpisodicEntry]:
        """Most recent N turns within a single session, returned chronologically."""
        rows = self._conn().execute(
            "SELECT * FROM episodic_memory "
            "WHERE session_id = ? AND deleted_at IS NULL "
            "ORDER BY rowid DESC LIMIT ?",
            (session_id, limit),
        ).fetchall()
        entries = [_row_to_episodic(r) for r in rows]
        entries.reverse()
        return entries

    def get_recent_across_sessions(self, limit: int = 20,
                                   within_days: int = 30,
                                   exclude_session: str | None = None) -> list[EpisodicEntry]:
        """Last N turns across ALL sessions in the past ``within_days`` days.

        This is the cross-session continuity primitive — the thing that makes
        Andrew remember what we talked about yesterday even though that was a
        different session.
        """
        cutoff_iso = (_utcnow() - timedelta(days=within_days)).isoformat(sep=" ", timespec="microseconds")
        if exclude_session:
            rows = self._conn().execute(
                "SELECT * FROM episodic_memory "
                "WHERE deleted_at IS NULL AND created_at >= ? AND session_id != ? "
                "ORDER BY created_at DESC LIMIT ?",
                (cutoff_iso, exclude_session, limit),
            ).fetchall()
        else:
            rows = self._conn().execute(
                "SELECT * FROM episodic_memory "
                "WHERE deleted_at IS NULL AND created_at >= ? "
                "ORDER BY created_at DESC LIMIT ?",
                (cutoff_iso, limit),
            ).fetchall()
        entries = [_row_to_episodic(r) for r in rows]
        entries.reverse()
        return entries

    def get_older_than(self, days: int, limit: int = 200) -> list[EpisodicEntry]:
        """Used by the consolidator to find candidates for distillation."""
        cutoff_iso = (_utcnow() - timedelta(days=days)).isoformat(sep=" ", timespec="microseconds")
        rows = self._conn().execute(
            "SELECT * FROM episodic_memory "
            "WHERE deleted_at IS NULL AND created_at < ? "
            "ORDER BY created_at ASC LIMIT ?",
            (cutoff_iso, limit),
        ).fetchall()
        return [_row_to_episodic(r) for r in rows]

    def set_importance(self, entry_id: str, importance: float) -> None:
        self._conn().execute(
            "UPDATE episodic_memory SET importance = ? WHERE id = ?",
            (max(0.0, min(1.0, importance)), entry_id),
        )

    def get_top_importance_in_session(self, session_id: str, limit: int = 8,
                                      exclude_ids: Iterable[str] | None = None,
                                      min_importance: float = 0.55) -> list[EpisodicEntry]:
        """Highest-importance turns in a session, excluding rows already in
        the recent window. Returned chronologically so the prompt reads in
        order."""
        excl = list(exclude_ids or [])
        # Use parameterized IN clause; SQLite supports up to 999 placeholders
        if excl:
            placeholders = ",".join("?" * len(excl))
            sql = (
                "SELECT * FROM episodic_memory "
                "WHERE session_id = ? AND deleted_at IS NULL "
                f"AND importance >= ? AND id NOT IN ({placeholders}) "
                "ORDER BY importance DESC, rowid DESC LIMIT ?"
            )
            params: tuple = (session_id, min_importance, *excl, limit)
        else:
            sql = (
                "SELECT * FROM episodic_memory "
                "WHERE session_id = ? AND deleted_at IS NULL "
                "AND importance >= ? "
                "ORDER BY importance DESC, rowid DESC LIMIT ?"
            )
            params = (session_id, min_importance, limit)
        rows = self._conn().execute(sql, params).fetchall()
        entries = [_row_to_episodic(r) for r in rows]
        # Sort chronologically for readable prompt rendering
        entries.sort(key=lambda e: e.created_at)
        return entries

    def get_unscored_for_importance(self, limit: int = 25,
                                    older_than_minutes: int = 5) -> list[EpisodicEntry]:
        """Episodic rows still at the default importance (==0.5) that have
        cooled for at least ``older_than_minutes``. Used by the importance
        scorer in the consolidator — we let very fresh rows settle first to
        avoid scoring half a conversation."""
        cutoff = (_utcnow() - timedelta(minutes=older_than_minutes)).isoformat(
            sep=" ", timespec="microseconds"
        )
        rows = self._conn().execute(
            "SELECT * FROM episodic_memory "
            "WHERE deleted_at IS NULL AND importance = 0.5 AND created_at <= ? "
            "ORDER BY created_at ASC LIMIT ?",
            (cutoff, limit),
        ).fetchall()
        return [_row_to_episodic(r) for r in rows]

    def set_importance_many(self, scores: dict[str, float]) -> int:
        """Bulk update importance for multiple entries. Returns count updated."""
        if not scores:
            return 0
        with transaction(self.user_id) as conn:
            for eid, score in scores.items():
                conn.execute(
                    "UPDATE episodic_memory SET importance = ? WHERE id = ?",
                    (max(0.0, min(1.0, score)), eid),
                )
        return len(scores)

    def soft_delete(self, entry_id: str) -> None:
        self._conn().execute(
            "UPDATE episodic_memory SET deleted_at = ? WHERE id = ?",
            (_now_iso(), entry_id),
        )

    def count(self) -> int:
        row = self._conn().execute(
            "SELECT COUNT(*) AS n FROM episodic_memory WHERE deleted_at IS NULL"
        ).fetchone()
        return int(row["n"]) if row else 0


def _row_to_episodic(row: sqlite3.Row) -> EpisodicEntry:
    try:
        meta = json.loads(row["metadata"] or "{}")
    except (json.JSONDecodeError, TypeError):
        meta = {}
    return EpisodicEntry(
        id=row["id"],
        session_id=row["session_id"],
        role=row["role"],
        content=row["content"],
        source=row["source"],
        task_id=row["task_id"],
        importance=float(row["importance"]),
        strength=float(row["strength"]),
        embedding_id=row["embedding_id"],
        metadata=meta,
        created_at=_parse_iso(row["created_at"]) or _utcnow(),
    )


# ---------- Profile (the lifecycle layer) ----------


@dataclass
class ProfileFact:
    id: str
    key: str
    value: str
    category: str
    confidence: float
    source: str
    version: int
    protected: bool
    last_referenced_at: datetime | None
    created_at: datetime
    updated_at: datetime


@dataclass
class DecayStats:
    checked: int = 0
    reinforced: int = 0
    decayed: int = 0
    removed: int = 0


class ProfileStore:
    """The living-memory layer.

    Facts have lifecycles: confidence is reinforced when used, decays when
    ignored, and is pruned when it falls below the floor — unless protected.
    Every update is versioned so we can see how a fact evolved.
    """

    # Adam's defaults; tunable per call.
    DEFAULT_REINFORCE_AMOUNT = 0.08
    DEFAULT_HALF_LIFE_DAYS = 30.0
    DEFAULT_MIN_CONFIDENCE = 0.25

    VALID_CATEGORIES = {"background", "work", "preferences", "personal", "other", "general"}
    VALID_SOURCES = {"user", "extracted", "consolidated", "imported"}

    def __init__(self, user_id: str = "default"):
        self.user_id = user_id

    def _conn(self) -> sqlite3.Connection:
        return get_connection(self.user_id)

    def _normalize_category(self, category: str | None) -> str:
        c = (category or "general").lower().strip()
        return c if c in self.VALID_CATEGORIES else "other"

    def _normalize_source(self, source: str | None) -> str:
        s = (source or "extracted").lower().strip()
        return s if s in self.VALID_SOURCES else "extracted"

    def set(self, key: str, value: str, *, category: str | None = None,
            confidence: float = 1.0, source: str = "user",
            protected: bool | None = None) -> str:
        """Insert or update a fact. Versions the previous row via soft-delete.

        User-sourced facts are auto-protected unless ``protected=False`` is
        passed explicitly.
        """
        key = key.strip()
        value = value.strip()
        if not key or not value:
            raise ValueError("ProfileStore.set requires non-empty key and value")
        cat = self._normalize_category(category)
        src = self._normalize_source(source)
        is_protected = (src == "user") if protected is None else bool(protected)
        confidence = max(0.0, min(1.0, float(confidence)))
        now = _now_iso()
        new_id = _uuid()

        with transaction(self.user_id) as conn:
            existing = conn.execute(
                "SELECT id, version FROM profile_facts "
                "WHERE key = ? AND deleted_at IS NULL",
                (key,),
            ).fetchone()
            new_version = (existing["version"] + 1) if existing else 1
            if existing:
                conn.execute(
                    "UPDATE profile_facts SET deleted_at = ? WHERE id = ?",
                    (now, existing["id"]),
                )
            conn.execute(
                "INSERT INTO profile_facts "
                "(id, key, value, category, confidence, source, version, protected, "
                " created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (new_id, key, value, cat, confidence, src, new_version,
                 1 if is_protected else 0, now, now),
            )
            _audit(conn, "fact_set", "profile_facts", new_id,
                   key=key, version=new_version, source=src)
        return new_id

    def get(self, key: str) -> ProfileFact | None:
        row = self._conn().execute(
            "SELECT * FROM profile_facts WHERE key = ? AND deleted_at IS NULL",
            (key,),
        ).fetchone()
        return _row_to_fact(row) if row else None

    def get_all(self, category: str | None = None,
                min_confidence: float = 0.0) -> list[ProfileFact]:
        if category:
            rows = self._conn().execute(
                "SELECT * FROM profile_facts "
                "WHERE deleted_at IS NULL AND category = ? AND confidence >= ? "
                "ORDER BY confidence DESC, updated_at DESC",
                (self._normalize_category(category), min_confidence),
            ).fetchall()
        else:
            rows = self._conn().execute(
                "SELECT * FROM profile_facts "
                "WHERE deleted_at IS NULL AND confidence >= ? "
                "ORDER BY confidence DESC, updated_at DESC",
                (min_confidence,),
            ).fetchall()
        return [_row_to_fact(r) for r in rows]

    def get_top(self, limit: int = 30, min_confidence: float = 0.0) -> list[ProfileFact]:
        """Highest-confidence facts. Used to populate the system prompt."""
        rows = self._conn().execute(
            "SELECT * FROM profile_facts "
            "WHERE deleted_at IS NULL AND confidence >= ? "
            "ORDER BY confidence DESC, updated_at DESC LIMIT ?",
            (min_confidence, limit),
        ).fetchall()
        return [_row_to_fact(r) for r in rows]

    def version_history(self, key: str) -> list[ProfileFact]:
        """Every version of a fact, newest first. Includes soft-deleted rows."""
        rows = self._conn().execute(
            "SELECT * FROM profile_facts WHERE key = ? "
            "ORDER BY version DESC",
            (key,),
        ).fetchall()
        return [_row_to_fact(r) for r in rows]

    def reinforce(self, key: str, amount: float | None = None) -> bool:
        """Boost a fact's confidence toward 1.0 and refresh ``last_referenced_at``.

        Called whenever a fact is injected into a prompt. Returns True if a
        fact was found and reinforced. Cheap — O(1) update on indexed column.
        """
        amt = self.DEFAULT_REINFORCE_AMOUNT if amount is None else amount
        row = self._conn().execute(
            "SELECT id, confidence FROM profile_facts "
            "WHERE key = ? AND deleted_at IS NULL",
            (key,),
        ).fetchone()
        if not row:
            return False
        new_conf = min(1.0, float(row["confidence"]) + amt)
        now = _now_iso()
        self._conn().execute(
            "UPDATE profile_facts "
            "SET confidence = ?, last_referenced_at = ?, updated_at = ? "
            "WHERE id = ?",
            (new_conf, now, now, row["id"]),
        )
        return True

    def reinforce_many(self, keys: Iterable[str], amount: float | None = None) -> int:
        """Bulk reinforcement. Returns number of facts updated."""
        n = 0
        for k in keys:
            if self.reinforce(k, amount):
                n += 1
        return n

    def protect(self, key: str, protected: bool = True) -> bool:
        row = self._conn().execute(
            "UPDATE profile_facts SET protected = ?, updated_at = ? "
            "WHERE key = ? AND deleted_at IS NULL",
            (1 if protected else 0, _now_iso(), key),
        )
        if row.rowcount > 0:
            _audit(self._conn(), "fact_protect" if protected else "fact_unprotect",
                   "profile_facts", None, key=key)
            return True
        return False

    def delete(self, key: str) -> bool:
        cur = self._conn().execute(
            "UPDATE profile_facts SET deleted_at = ? "
            "WHERE key = ? AND deleted_at IS NULL",
            (_now_iso(), key),
        )
        if cur.rowcount > 0:
            _audit(self._conn(), "fact_delete", "profile_facts", None, key=key)
            return True
        return False

    def decay(self, half_life_days: float | None = None,
              min_confidence: float | None = None) -> DecayStats:
        """Apply exponential decay to all unprotected, non-user facts.

        Math (Adam's): ``new = old * exp(-ln(2)/halfLife * daysSince)``. Facts
        that fall below ``min_confidence`` are soft-deleted. Within the first
        24h of a reference, no decay is applied (grace period).
        """
        h = self.DEFAULT_HALF_LIFE_DAYS if half_life_days is None else half_life_days
        floor = self.DEFAULT_MIN_CONFIDENCE if min_confidence is None else min_confidence
        if h <= 0:
            raise ValueError("half_life_days must be positive")
        lam = math.log(2) / h
        stats = DecayStats()
        now_dt = _utcnow()

        rows = self._conn().execute(
            "SELECT id, key, confidence, last_referenced_at, updated_at, source, protected "
            "FROM profile_facts WHERE deleted_at IS NULL"
        ).fetchall()

        with transaction(self.user_id) as conn:
            for r in rows:
                stats.checked += 1
                if int(r["protected"]) == 1 or r["source"] == "user":
                    stats.reinforced += 1
                    continue
                ref = _parse_iso(r["last_referenced_at"]) or _parse_iso(r["updated_at"]) or now_dt
                days = max(0.0, (now_dt - ref).total_seconds() / 86400.0)
                if days < 1.0:
                    continue
                new_conf = float(r["confidence"]) * math.exp(-lam * days)
                if new_conf < floor:
                    conn.execute(
                        "UPDATE profile_facts SET deleted_at = ? WHERE id = ?",
                        (_now_iso(), r["id"]),
                    )
                    _audit(conn, "fact_decay_remove", "profile_facts", r["id"],
                           key=r["key"], confidence=new_conf, days=days)
                    stats.removed += 1
                else:
                    conn.execute(
                        "UPDATE profile_facts SET confidence = ?, updated_at = ? "
                        "WHERE id = ?",
                        (new_conf, _now_iso(), r["id"]),
                    )
                    stats.decayed += 1
            _audit(conn, "decay_pass", None, None,
                   half_life=h, floor=floor, **stats.__dict__)
        return stats


def _row_to_fact(row: sqlite3.Row) -> ProfileFact:
    return ProfileFact(
        id=row["id"],
        key=row["key"],
        value=row["value"],
        category=row["category"],
        confidence=float(row["confidence"]),
        source=row["source"],
        version=int(row["version"]),
        protected=bool(row["protected"]),
        last_referenced_at=_parse_iso(row["last_referenced_at"]),
        created_at=_parse_iso(row["created_at"]) or _utcnow(),
        updated_at=_parse_iso(row["updated_at"]) or _utcnow(),
    )


# ---------- Background thoughts ----------


@dataclass
class Thought:
    id: str
    session_id: str | None
    content: str
    delivered: bool
    reject_reason: str | None
    strength: float
    created_at: datetime


class ThoughtStore:
    """Andrew's stream-of-consciousness, separate from the conversation log.

    Mirrors the previous ``thoughts.jsonl`` design but adds delivered/rejected
    flags so we can analyze the proactive-message filter quality over time.
    """

    def __init__(self, user_id: str = "default"):
        self.user_id = user_id

    def _conn(self) -> sqlite3.Connection:
        return get_connection(self.user_id)

    def insert(self, content: str, *, session_id: str | None = None,
               delivered: bool = False, reject_reason: str | None = None,
               strength: float = 1.0) -> str:
        tid = _uuid()
        self._conn().execute(
            "INSERT INTO background_thoughts "
            "(id, session_id, content, delivered, reject_reason, strength, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (tid, session_id, content, 1 if delivered else 0, reject_reason, strength, _now_iso()),
        )
        return tid

    def mark_delivered(self, thought_id: str) -> None:
        self._conn().execute(
            "UPDATE background_thoughts SET delivered = 1, reject_reason = NULL "
            "WHERE id = ?",
            (thought_id,),
        )

    def get_recent(self, limit: int = 8, delivered_only: bool = False) -> list[Thought]:
        # Order by rowid (insertion order) instead of created_at — Windows
        # clock resolution can collapse multiple sub-second inserts.
        if delivered_only:
            rows = self._conn().execute(
                "SELECT * FROM background_thoughts "
                "WHERE deleted_at IS NULL AND delivered = 1 "
                "ORDER BY rowid DESC LIMIT ?",
                (limit,),
            ).fetchall()
        else:
            rows = self._conn().execute(
                "SELECT * FROM background_thoughts "
                "WHERE deleted_at IS NULL "
                "ORDER BY rowid DESC LIMIT ?",
                (limit,),
            ).fetchall()
        thoughts = [_row_to_thought(r) for r in rows]
        thoughts.reverse()
        return thoughts


def _row_to_thought(row: sqlite3.Row) -> Thought:
    return Thought(
        id=row["id"],
        session_id=row["session_id"],
        content=row["content"],
        delivered=bool(row["delivered"]),
        reject_reason=row["reject_reason"],
        strength=float(row["strength"]),
        created_at=_parse_iso(row["created_at"]) or _utcnow(),
    )


# ---------- Persistent KV state (replaces working.json) ----------


class WorkingState:
    """Persistent key/value store for session-spanning state.

    Examples: ``current_speaker_discord_id``, the active doctor-mode flag, etc.
    NOT the same as the in-process ``ContextWindow`` — this is durable.
    """

    def __init__(self, user_id: str = "default"):
        self.user_id = user_id

    def _conn(self) -> sqlite3.Connection:
        return get_connection(self.user_id)

    def get(self, key: str, default: Any = None) -> Any:
        row = self._conn().execute(
            "SELECT value FROM working_memory WHERE key = ?", (key,)
        ).fetchone()
        if not row:
            return default
        try:
            return json.loads(row["value"])
        except (json.JSONDecodeError, TypeError):
            return row["value"]

    def set(self, key: str, value: Any) -> None:
        if value is None:
            self.delete(key)
            return
        self._conn().execute(
            "INSERT INTO working_memory (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (key, json.dumps(value, ensure_ascii=False), _now_iso()),
        )

    def delete(self, key: str) -> None:
        self._conn().execute("DELETE FROM working_memory WHERE key = ?", (key,))

    def all(self) -> dict[str, Any]:
        rows = self._conn().execute(
            "SELECT key, value FROM working_memory"
        ).fetchall()
        out: dict[str, Any] = {}
        for r in rows:
            try:
                out[r["key"]] = json.loads(r["value"])
            except (json.JSONDecodeError, TypeError):
                out[r["key"]] = r["value"]
        return out


# ---------- Context window (in-process, token-budgeted) ----------


@dataclass
class ContextMessage:
    role: str
    content: str
    importance: float = 0.5
    token_estimate: int = 0


class ContextWindow:
    """In-process token-budgeted message buffer for the active turn.

    Adam-style: when the buffer would exceed the budget, evict the
    lowest-importance message instead of strict FIFO. The most recent message
    and the system prompt are never evicted.

    Token estimate is the cheap ``len // 4`` heuristic — fine for budget
    decisions; replace with a real tokenizer later if needed.
    """

    def __init__(self, max_tokens: int = 16_000):
        self.max_tokens = max_tokens
        self._system_prompt = ""
        self._messages: list[ContextMessage] = []

    def set_system_prompt(self, text: str) -> None:
        self._system_prompt = text

    def push(self, role: str, content: str, importance: float = 0.5) -> None:
        msg = ContextMessage(
            role=role, content=content,
            importance=max(0.0, min(1.0, importance)),
            token_estimate=_estimate_tokens(content),
        )
        self._messages.append(msg)
        self._trim()

    def push_episodic(self, entries: Iterable[EpisodicEntry]) -> None:
        for e in entries:
            self._messages.append(ContextMessage(
                role=e.role, content=e.content,
                importance=e.importance,
                token_estimate=_estimate_tokens(e.content),
            ))
        self._trim()

    def messages(self) -> list[ContextMessage]:
        return list(self._messages)

    def total_tokens(self) -> int:
        return _estimate_tokens(self._system_prompt) + sum(m.token_estimate for m in self._messages)

    def clear(self) -> None:
        self._messages.clear()

    def _trim(self) -> None:
        while self.total_tokens() > self.max_tokens and len(self._messages) > 1:
            # Don't evict the very last message (the active turn)
            candidates = self._messages[:-1]
            min_idx = min(range(len(candidates)), key=lambda i: candidates[i].importance)
            self._messages.pop(min_idx)


def _estimate_tokens(text: str) -> int:
    return max(1, (len(text) + 3) // 4)
