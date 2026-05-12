"""SQLite memory backend.

One database per user profile lives at ``data/profiles/{user_id}/memory.db``.
Schema is owned by this module and applied via a tiny version-tracked migration
runner so we can ship schema changes without losing data.

Design notes:
- Stdlib ``sqlite3`` only — no ORM. Hand-written SQL keeps the surface small,
  debuggable, and free of new dependencies.
- WAL journaling so background readers (consolidator, web UI) don't block
  writers (chat loop).
- Foreign keys ON. Soft-deletes via ``deleted_at`` columns rather than hard
  removal — keeps audit-style traceability and lets us reconstruct history
  if the consolidator misjudges.
- Per-profile DB isolation matches the existing ``data/profiles/{user_id}/``
  layout. No cross-profile leakage by construction.
"""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from config.settings import USER_PROFILES_DIR

DB_FILENAME = "memory.db"

# ---------- Migrations ----------
# Each migration is a (version, list[sql_statement]) pair. They run in order;
# the schema_version row is updated atomically per migration. Add new entries
# at the BOTTOM of MIGRATIONS — never edit a shipped one.

MIGRATIONS: list[tuple[int, list[str]]] = [
    (
        1,
        [
            # --- meta -----------------------------------------------------
            """
            CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """,
            # --- sessions -------------------------------------------------
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                source TEXT NOT NULL,           -- 'web' | 'discord' | 'cli' | 'background' | 'unknown'
                channel_id TEXT,                -- discord channel, web tab id, etc.
                user_id TEXT,                   -- the human's identifier (discord id, web session)
                title TEXT,
                started_at TEXT NOT NULL DEFAULT (datetime('now')),
                last_activity_at TEXT NOT NULL DEFAULT (datetime('now')),
                ended_at TEXT,
                metadata TEXT NOT NULL DEFAULT '{}'
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_sessions_started ON sessions(started_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_sessions_source ON sessions(source, started_at DESC)",
            # --- episodic memory -----------------------------------------
            """
            CREATE TABLE IF NOT EXISTS episodic_memory (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL CHECK (role IN ('user','assistant','system','tool','proactive')),
                content TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'unknown',
                task_id TEXT,
                importance REAL NOT NULL DEFAULT 0.5,   -- 0..1; drives working-memory eviction + consolidation priority
                strength REAL NOT NULL DEFAULT 1.0,     -- multiplier on retention curve
                embedding_id TEXT,                      -- FK to semantic_embeddings.id, nullable
                metadata TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                deleted_at TEXT,
                FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_episodic_session ON episodic_memory(session_id, created_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_episodic_recent ON episodic_memory(created_at DESC) WHERE deleted_at IS NULL",
            "CREATE INDEX IF NOT EXISTS idx_episodic_importance ON episodic_memory(importance DESC) WHERE deleted_at IS NULL",
            # --- profile facts (the part that decays/reinforces) ---------
            """
            CREATE TABLE IF NOT EXISTS profile_facts (
                id TEXT PRIMARY KEY,
                key TEXT NOT NULL,                     -- stable identifier; reinforce/protect/delete operate on this
                value TEXT NOT NULL,
                category TEXT NOT NULL DEFAULT 'general',  -- background | work | preferences | personal | other | general
                confidence REAL NOT NULL DEFAULT 1.0,  -- 0..1; reinforced on inject, decayed by consolidator
                source TEXT NOT NULL DEFAULT 'extracted', -- 'user' | 'extracted' | 'consolidated'
                version INTEGER NOT NULL DEFAULT 1,    -- bumped on every update; old rows soft-deleted
                protected INTEGER NOT NULL DEFAULT 0,  -- 1 = immortal; user-source rows default to 1
                last_referenced_at TEXT,               -- last time injected into a prompt
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                deleted_at TEXT
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_profile_key_active ON profile_facts(key) WHERE deleted_at IS NULL",
            "CREATE INDEX IF NOT EXISTS idx_profile_category ON profile_facts(category) WHERE deleted_at IS NULL",
            "CREATE INDEX IF NOT EXISTS idx_profile_confidence ON profile_facts(confidence DESC) WHERE deleted_at IS NULL",
            # --- semantic embeddings (for Phase 6) ------------------------
            """
            CREATE TABLE IF NOT EXISTS semantic_embeddings (
                id TEXT PRIMARY KEY,
                source_table TEXT NOT NULL,            -- 'episodic_memory' | 'profile_facts' | ...
                source_id TEXT NOT NULL,               -- FK to that table's id
                content TEXT NOT NULL,                 -- denormalized text for quick inspection
                vector BLOB NOT NULL,                  -- raw float32 array bytes
                dimensions INTEGER NOT NULL,
                model TEXT NOT NULL,                   -- model name used to embed
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_embeddings_source ON semantic_embeddings(source_table, source_id)",
            # --- background thoughts (Andrew's stream of consciousness) ---
            """
            CREATE TABLE IF NOT EXISTS background_thoughts (
                id TEXT PRIMARY KEY,
                session_id TEXT,                       -- nullable: thoughts can be sessionless
                content TEXT NOT NULL,
                delivered INTEGER NOT NULL DEFAULT 0,  -- 1 = pushed to web/discord, 0 = held back
                reject_reason TEXT,                    -- filter reason if not delivered
                strength REAL NOT NULL DEFAULT 1.0,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                deleted_at TEXT,
                FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE SET NULL
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_thoughts_recent ON background_thoughts(created_at DESC) WHERE deleted_at IS NULL",
            # --- working memory (persisted between restarts; unlike Adam) -
            """
            CREATE TABLE IF NOT EXISTS working_memory (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """,
            # --- audit log of memory mutations (lightweight) --------------
            """
            CREATE TABLE IF NOT EXISTS memory_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                action TEXT NOT NULL,           -- 'fact_set' | 'fact_reinforce' | 'fact_decay' | 'fact_protect' | 'fact_delete' | 'consolidate'
                target_table TEXT,
                target_id TEXT,
                details TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_audit_recent ON memory_audit(created_at DESC)",
        ],
    ),
    (
        2,
        [
            # --- task threads ---------------------------------------------
            # Explicit conversational/work threads with status. First-class
            # memory layer so the agent can answer "what's open?" without
            # rummaging through episodic prose.
            """
            CREATE TABLE IF NOT EXISTS task_threads (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                description TEXT,
                status TEXT NOT NULL CHECK (status IN ('open','blocked','done','abandoned')) DEFAULT 'open',
                owner TEXT,                              -- 'user' | 'andrew' | 'shared'
                related_artifacts TEXT NOT NULL DEFAULT '[]',  -- json array of paths/ids
                tags TEXT NOT NULL DEFAULT '[]',         -- json array
                session_id TEXT,                         -- session that opened it
                last_touched_at TEXT NOT NULL DEFAULT (datetime('now')),
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                closed_at TEXT,
                deleted_at TEXT,
                FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE SET NULL
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_threads_status ON task_threads(status, last_touched_at DESC) WHERE deleted_at IS NULL",
            "CREATE INDEX IF NOT EXISTS idx_threads_recent ON task_threads(last_touched_at DESC) WHERE deleted_at IS NULL",
            # --- continuity ledger ----------------------------------------
            # Rolling document — identity, projects, open threads,
            # decisions, unresolved directives. Latest row is what gets
            # pinned at the top of the system prompt.
            """
            CREATE TABLE IF NOT EXISTS continuity_ledger (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT NOT NULL,                 -- rendered markdown
                sections TEXT NOT NULL DEFAULT '{}',   -- json structured parts
                version INTEGER NOT NULL DEFAULT 1,
                built_by TEXT NOT NULL DEFAULT 'consolidator',  -- 'consolidator' | 'startup' | 'manual' | 'on_demand'
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_ledger_recent ON continuity_ledger(created_at DESC)",
            # --- recall events --------------------------------------------
            # Traceability for the deterministic recall router. When a user
            # phrase triggered a deep recall, what we found, what we showed.
            """
            CREATE TABLE IF NOT EXISTS recall_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT,
                query TEXT NOT NULL,
                trigger_type TEXT NOT NULL,            -- 'phrase' | 'tool' | 'guard' | 'startup'
                phrase_matched TEXT,
                hit_count INTEGER NOT NULL DEFAULT 0,
                sources TEXT NOT NULL DEFAULT '[]',    -- json array of source kinds
                block_preview TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_recall_recent ON recall_events(created_at DESC)",
        ],
    ),
]


# ---------- Connection management ----------

_lock = threading.Lock()
_connections: dict[str, sqlite3.Connection] = {}


def db_path(user_id: str) -> Path:
    return USER_PROFILES_DIR / user_id / DB_FILENAME


def _open(user_id: str) -> sqlite3.Connection:
    p = db_path(user_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        str(p),
        timeout=10.0,
        isolation_level=None,  # autocommit; explicit BEGIN/COMMIT used where atomicity matters
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA temp_store=MEMORY")
    return conn


def get_connection(user_id: str = "default") -> sqlite3.Connection:
    """Return a process-wide cached connection for the given profile."""
    with _lock:
        conn = _connections.get(user_id)
        if conn is None:
            conn = _open(user_id)
            _ensure_schema(conn)
            _connections[user_id] = conn
        return conn


def close_all() -> None:
    """Close every cached connection. Useful for tests and clean shutdown."""
    with _lock:
        for c in _connections.values():
            try:
                c.close()
            except sqlite3.Error:
                pass
        _connections.clear()


@contextmanager
def transaction(user_id: str = "default") -> Iterator[sqlite3.Connection]:
    """Run a block of statements atomically. Rolls back on exception."""
    conn = get_connection(user_id)
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


# ---------- Schema migration ----------


def _current_version(conn: sqlite3.Connection) -> int:
    try:
        row = conn.execute(
            "SELECT MAX(version) AS v FROM schema_version"
        ).fetchone()
    except sqlite3.OperationalError:
        return 0
    if not row or row["v"] is None:
        return 0
    return int(row["v"])


def _ensure_schema(conn: sqlite3.Connection) -> None:
    current = _current_version(conn)
    for version, statements in MIGRATIONS:
        if version <= current:
            continue
        conn.execute("BEGIN IMMEDIATE")
        try:
            for sql in statements:
                conn.execute(sql)
            conn.execute(
                "INSERT INTO schema_version (version) VALUES (?)", (version,)
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise


# ---------- Public introspection ----------


def schema_version(user_id: str = "default") -> int:
    return _current_version(get_connection(user_id))


def vacuum(user_id: str = "default") -> None:
    """Reclaim space after large soft-delete sweeps. Cheap to call after the
    consolidator prunes a batch."""
    conn = get_connection(user_id)
    conn.execute("VACUUM")
