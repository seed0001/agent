"""Memory consolidator — the lifecycle's background worker.

Runs in the daemon alongside the existing background-thoughts loop. Each tick
does two things:

1. **Decay pass** — apply exponential decay to all unprotected, non-user
   profile facts. Anything below the floor is soft-deleted.
2. **Consolidate pass** — find episodic turns that are old enough to settle
   (older than ``consolidate_after_days`` but newer than the last consolidation
   watermark), group them by session, ask the LLM to extract durable facts,
   and insert each one through ``ProfileStore.set(source="consolidated")`` so
   the lifecycle can decay them like any other auto-extracted fact.

Stochastic interval (8-18 min, randomly jittered) follows the Neural-CA
inspired async-update model from seed0001/Adam — predictable cron schedules
create artifacts, randomness keeps the rhythm honest.

Idempotency: a watermark stored in ``WorkingState`` ensures we never
re-consolidate the same episodic entries. If the consolidator crashes
mid-tick, the watermark only advances on success.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable, Iterable

from src.agent.memory_stores import (
    DecayStats,
    EpisodicEntry,
    EpisodicStore,
    ProfileStore,
    WorkingState,
    _utcnow,
)

logger = logging.getLogger(__name__)

WATERMARK_KEY = "memory.consolidation.cutoff_iso"

# ---------- LLM client type ----------

# We don't import openai here so the consolidator can be unit-tested without
# any HTTP stack. Callers inject an async function with this shape.
LLMCall = Callable[[str, str], Awaitable[str]]


# ---------- Stats / config ----------


@dataclass
class ConsolidateStats:
    sessions_examined: int = 0
    sessions_skipped: int = 0
    facts_extracted: int = 0
    facts_inserted: int = 0
    llm_calls: int = 0
    parse_failures: int = 0


@dataclass
class ImportanceStats:
    examined: int = 0
    scored: int = 0
    parse_failures: int = 0


@dataclass
class EmbedStats:
    examined: int = 0
    embedded: int = 0
    available: bool = True


@dataclass
class LedgerStats:
    rebuilt: bool = False
    version: int = 0
    sections: int = 0
    error: str = ""


@dataclass
class TickStats:
    decay: DecayStats = field(default_factory=DecayStats)
    consolidate: ConsolidateStats = field(default_factory=ConsolidateStats)
    importance: ImportanceStats = field(default_factory=ImportanceStats)
    embed: EmbedStats = field(default_factory=EmbedStats)
    ledger: LedgerStats = field(default_factory=LedgerStats)
    started_at: datetime = field(default_factory=_utcnow)
    finished_at: datetime | None = None


@dataclass
class ConsolidatorConfig:
    """Tunable parameters. Defaults match seed0001/Adam where possible."""

    half_life_days: float = 30.0
    """Decay half-life for unprotected facts."""

    min_confidence: float = 0.25
    """Facts below this are pruned during decay."""

    consolidate_after_days: float = 1.0
    """How old an episodic entry must be before it's eligible for consolidation.
    Adam uses ~1 day so the conversation has 'settled' before facts get
    extracted as durable."""

    min_session_turns: int = 4
    """Sessions with fewer turns than this are skipped — not enough material
    to extract anything durable."""

    max_session_turns: int = 60
    """Don't blow up the LLM context window on long sessions."""

    sleep_min_seconds: int = 8 * 60
    sleep_max_seconds: int = 18 * 60

    extracted_confidence: float = 0.7
    """Initial confidence for facts the consolidator inserts. Lower than
    user-entered (1.0) so they decay if not reinforced by use."""

    max_facts_per_session: int = 5
    """Cap on facts extracted per session. The LLM is told this in the prompt."""

    importance_batch_size: int = 20
    """How many unscored episodic entries to rate per tick."""

    importance_min_age_minutes: int = 5
    """Don't rate turns this fresh — they're still in the active conversation."""

    embed_batch_size: int = 30
    """How many unembedded episodic entries to vectorize per tick."""

    enable_embeddings: bool = True
    """Set False to skip the embed pass entirely (e.g. headless eval)."""


# ---------- Prompt + parsing ----------


_EXTRACT_SYSTEM = """You are a quiet observer that extracts durable facts about the user from conversation transcripts.

Your job is to find facts that are likely to remain TRUE beyond this single conversation:
- background (where they live, age, identity, life context)
- work (what they do, projects they're building, skills, employer)
- preferences (likes, dislikes, communication style, tools they prefer)
- personal (relationships, family, hobbies, values, mood patterns)
- other (anything else durable that doesn't fit above)

Skip:
- One-off requests or transient state ("they want lunch", "they're tired tonight")
- Facts already very obvious from context (their name in the transcript header)
- Anything you can't ground in an explicit statement they made

Output strictly as a JSON array. Each element is {"category": "...", "fact": "..."}.
- "fact" is a single concrete statement in third person about the user (e.g. "Lives in Alabama").
- Maximum {max_facts} facts.
- If nothing durable is in the transcript, output exactly: []
- Never wrap the JSON in code fences or commentary.
"""


def _build_extract_user_prompt(transcript: str, max_facts: int) -> str:
    return (
        f"Conversation transcript:\n\n{transcript}\n\n"
        f"Extract at most {max_facts} durable facts about the user. JSON only."
    )


def _format_transcript(turns: Iterable[EpisodicEntry], max_chars: int = 8000) -> str:
    """Render episodic turns into a readable transcript for the LLM."""
    lines: list[str] = []
    used = 0
    for t in turns:
        prefix = {
            "user": "USER",
            "assistant": "ASSISTANT",
            "system": "SYSTEM",
            "tool": "TOOL",
            "proactive": "ASSISTANT (proactive)",
        }.get(t.role, t.role.upper())
        snippet = (t.content or "").strip()
        if not snippet:
            continue
        line = f"{prefix}: {snippet}"
        if used + len(line) > max_chars:
            break
        lines.append(line)
        used += len(line) + 1
    return "\n".join(lines)


_FACT_SCHEMA_RE = re.compile(r"^\s*(\[.*\])\s*$", re.DOTALL)
VALID_CATEGORIES = {"background", "work", "preferences", "personal", "other"}


_IMPORTANCE_SYSTEM = """You rate the long-term importance of conversation turns for a software lifeform's memory.

Each turn gets a score 0.0-1.0:
- 0.0-0.2: noise (greetings, acknowledgements, "ok", "thanks")
- 0.3-0.5: ordinary banter, small clarifications, transient state
- 0.6-0.8: meaningful — durable user info, decisions made, plans, problems being worked through
- 0.9-1.0: pivotal — names, identity, big decisions, irreversible commitments

You return strictly a JSON object mapping each turn's id to its score:
{"id-1": 0.4, "id-2": 0.85, ...}

No prose, no fences. If you can't rate one, omit it from the object.
"""


def _build_importance_user_prompt(turns: list[EpisodicEntry]) -> str:
    lines = ["Rate each of these turns. Return JSON object id -> score.\n"]
    for t in turns:
        snippet = (t.content or "").strip().replace("\n", " ")
        if len(snippet) > 400:
            snippet = snippet[:400] + "..."
        lines.append(f'id="{t.id}" role={t.role}: {snippet}')
    return "\n".join(lines)


def _parse_importance_scores(raw: str) -> dict[str, float]:
    if not raw:
        return {}
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    # Find the outer object
    i, j = text.find("{"), text.rfind("}")
    if i == -1 or j <= i:
        return {}
    try:
        data = json.loads(text[i : j + 1])
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, float] = {}
    for k, v in data.items():
        try:
            score = float(v)
        except (TypeError, ValueError):
            continue
        out[str(k)] = max(0.0, min(1.0, score))
    return out


def _parse_extracted_facts(raw: str) -> list[dict[str, str]]:
    """Parse the LLM response into normalized fact dicts. Best-effort:
    strips code fences, finds the outer JSON array, ignores malformed entries.
    """
    if not raw:
        return []
    text = raw.strip()
    # Strip markdown fences if the model added them despite instructions
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    m = _FACT_SCHEMA_RE.match(text)
    if not m:
        # Fallback: find the first '[' to last ']'
        i, j = text.find("["), text.rfind("]")
        if i == -1 or j <= i:
            return []
        text = text[i : j + 1]
    else:
        text = m.group(1)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    out: list[dict[str, str]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        cat = str(item.get("category") or "other").lower().strip()
        if cat not in VALID_CATEGORIES:
            cat = "other"
        fact = str(item.get("fact") or "").strip()
        if not fact:
            continue
        out.append({"category": cat, "fact": fact})
    return out


# ---------- Helpers ----------


def _fact_key(category: str, fact: str) -> str:
    """Same stable key used by the MemoryStore facade — see ``memory._fact_key``.
    Duplicated locally so this module has no dependency on the facade."""
    cat = (category or "other").lower().strip()
    body = " ".join((fact or "").lower().split())
    return f"{cat}::{body[:120]}"


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


# ---------- The consolidator ----------


class MemoryConsolidator:
    """Periodically runs decay + LLM consolidation against a single profile.

    Designed to be safe to instantiate multiple times (e.g. one per profile)
    and to coexist with the agent that's actively writing to the same DB —
    SQLite WAL mode + transactions handle the concurrency.
    """

    def __init__(
        self,
        user_id: str = "default",
        config: ConsolidatorConfig | None = None,
        llm: LLMCall | None = None,
    ):
        self.user_id = user_id
        self.config = config or ConsolidatorConfig()
        self._llm = llm  # may be None — consolidate_pass becomes a no-op then
        self.profile = ProfileStore(user_id)
        self.episodic = EpisodicStore(user_id)
        self.state = WorkingState(user_id)
        self._semantic = None  # lazy
        self._ledger_memory = None  # lazy: avoid circular import on construct

    # ---- decay ----

    def decay_pass(self) -> DecayStats:
        return self.profile.decay(
            half_life_days=self.config.half_life_days,
            min_confidence=self.config.min_confidence,
        )

    # ---- consolidation ----

    def _watermark(self) -> datetime:
        raw = self.state.get(WATERMARK_KEY)
        if raw:
            dt = _parse_iso(raw)
            if dt:
                return dt
        # First run: anchor to "1 year ago" so we capture existing episodic
        # entries on the first sweep without re-doing all of history forever.
        return _utcnow() - timedelta(days=365)

    def _set_watermark(self, when: datetime) -> None:
        self.state.set(WATERMARK_KEY, when.isoformat(sep=" ", timespec="microseconds"))

    def _eligible_episodic(self, until: datetime, since: datetime) -> list[EpisodicEntry]:
        """Episodic entries created in (since, until]. Caps at a sane batch size.

        We use ``get_older_than(days)`` then filter to the window — keeps us
        on the existing API without adding a new query method.
        """
        days_until = max(0.0, (_utcnow() - until).total_seconds() / 86400.0)
        candidates = self.episodic.get_older_than(days=days_until, limit=2000)
        return [e for e in candidates if since < e.created_at <= until]

    def _group_by_session(self, entries: list[EpisodicEntry]) -> dict[str, list[EpisodicEntry]]:
        out: dict[str, list[EpisodicEntry]] = {}
        for e in entries:
            out.setdefault(e.session_id, []).append(e)
        for sid in out:
            out[sid].sort(key=lambda e: e.created_at)
        return out

    async def _extract_facts_for_session(
        self, transcript: str
    ) -> tuple[list[dict[str, str]], bool]:
        """Returns (facts, parse_failed)."""
        if not self._llm:
            return [], False
        system = _EXTRACT_SYSTEM.replace("{max_facts}", str(self.config.max_facts_per_session))
        user = _build_extract_user_prompt(transcript, self.config.max_facts_per_session)
        try:
            raw = await self._llm(system, user)
        except Exception as e:
            logger.warning("consolidator LLM call failed: %s", e)
            return [], True
        facts = _parse_extracted_facts(raw)
        return facts, len(facts) == 0 and raw.strip() not in ("[]", "")

    async def consolidate_pass(self) -> ConsolidateStats:
        stats = ConsolidateStats()
        cfg = self.config

        until = _utcnow() - timedelta(days=cfg.consolidate_after_days)
        since = self._watermark()
        if until <= since:
            return stats  # nothing new to settle yet

        candidates = self._eligible_episodic(until, since)
        if not candidates:
            self._set_watermark(until)
            return stats

        sessions = self._group_by_session(candidates)
        for sid, turns in sessions.items():
            stats.sessions_examined += 1
            if len(turns) < cfg.min_session_turns:
                stats.sessions_skipped += 1
                continue
            transcript = _format_transcript(turns[: cfg.max_session_turns])
            stats.llm_calls += 1
            facts, parse_failed = await self._extract_facts_for_session(transcript)
            if parse_failed:
                stats.parse_failures += 1
            stats.facts_extracted += len(facts)
            for f in facts:
                key = _fact_key(f["category"], f["fact"])
                try:
                    existing = self.profile.get(key)
                    if existing and existing.protected:
                        # Don't downgrade a user-protected fact with a
                        # consolidator-derived one. Reinforce instead.
                        self.profile.reinforce(key)
                        continue
                    self.profile.set(
                        key,
                        f["fact"],
                        category=f["category"],
                        source="consolidated",
                        confidence=cfg.extracted_confidence,
                        protected=False,
                    )
                    stats.facts_inserted += 1
                except ValueError:
                    continue

        # Only advance the watermark if everything ran without exception. If
        # any session fell over, we still moved past the entries we processed
        # — re-running would just attempt them again, which is acceptable.
        self._set_watermark(until)
        return stats

    # ---- importance scoring ----

    async def importance_pass(self) -> ImportanceStats:
        """Rate any episodic entries that are still at the default importance.

        Skipped entirely without an LLM. Cheap-batched: one LLM call per
        ``importance_batch_size`` entries.
        """
        stats = ImportanceStats()
        if not self._llm:
            return stats
        cfg = self.config
        candidates = self.episodic.get_unscored_for_importance(
            limit=cfg.importance_batch_size,
            older_than_minutes=cfg.importance_min_age_minutes,
        )
        if not candidates:
            return stats
        stats.examined = len(candidates)
        user = _build_importance_user_prompt(candidates)
        try:
            raw = await self._llm(_IMPORTANCE_SYSTEM, user)
        except Exception as e:
            logger.warning("importance LLM call failed: %s", e)
            stats.parse_failures += 1
            return stats
        scores = _parse_importance_scores(raw)
        if not scores:
            stats.parse_failures += 1
            return stats
        # Only update entries we got back
        valid_scores = {eid: s for eid, s in scores.items() if eid in {c.id for c in candidates}}
        stats.scored = self.episodic.set_importance_many(valid_scores)
        return stats

    # ---- semantic embedding ----

    def _semantic_index(self):
        if self._semantic is None:
            try:
                from src.agent.memory_embeddings import SemanticIndex
                self._semantic = SemanticIndex(self.user_id)
            except Exception as e:
                logger.warning("semantic index unavailable: %s", e)
                self._semantic = False
        return self._semantic if self._semantic is not False else None

    def embed_pass(self) -> EmbedStats:
        """Vectorize any episodic rows missing an embedding. Returns counts.
        Cheap when the model is loaded (CPU-bound matmul); skipped if
        sentence-transformers can't load."""
        stats = EmbedStats()
        if not self.config.enable_embeddings:
            stats.available = False
            return stats
        idx = self._semantic_index()
        if idx is None or not idx.service.is_available:
            stats.available = False
            return stats
        candidates = idx.get_unembedded_episodic_ids(limit=self.config.embed_batch_size)
        if not candidates:
            return stats
        stats.examined = len(candidates)
        batch = [("episodic_memory", eid, content) for eid, content in candidates if (content or "").strip()]
        if not batch:
            return stats
        result = idx.add_many(batch)
        stats.embedded = len(result)
        return stats

    # ---- ledger ----

    def ledger_pass(self) -> LedgerStats:
        """Rebuild the continuity ledger from current memory state.

        Uses a *separate* MemoryStore-like surface — we lazy-import the
        facade to avoid an import cycle (the facade itself instantiates the
        consolidator's stores). Cheap deterministic build; no LLM required.
        """
        stats = LedgerStats()
        try:
            from src.agent.continuity_ledger import ContinuityLedger, LedgerBuilder
            from src.agent.memory import MemoryStore
            if self._ledger_memory is None:
                # We construct a separate facade pinned to this profile.
                # NOTE: this creates its own session row, so don't keep it
                # — pull what we need and discard.
                pass
            # Build directly off our existing stores to avoid spinning up
            # another session row each tick. We synthesize the duck-typed
            # surface the LedgerBuilder needs.
            class _Surface:
                pass
            surface = _Surface()
            surface.profile = self.profile  # type: ignore[attr-defined]
            surface.episodic = self.episodic  # type: ignore[attr-defined]
            from src.agent.task_threads import TaskThreadStore
            surface.threads = TaskThreadStore(self.user_id)  # type: ignore[attr-defined]
            from src.agent.memory_stores import SessionStore
            surface.sessions = SessionStore(self.user_id)  # type: ignore[attr-defined]
            surface.session_id = ""  # consolidator isn't tied to a session
            ledger = ContinuityLedger(self.user_id)
            row = LedgerBuilder(surface).build_and_persist(ledger, built_by="consolidator")
            if row is not None:
                stats.rebuilt = True
                stats.version = row.version
                stats.sections = len(row.sections or {})
        except Exception as e:
            stats.error = str(e)[:200]
            logger.warning("ledger_pass failed: %s", e)
        return stats

    # ---- single tick + loop ----

    async def tick(self) -> TickStats:
        stats = TickStats()
        try:
            stats.decay = self.decay_pass()
        except Exception as e:
            logger.exception("decay_pass failed: %s", e)
        try:
            stats.consolidate = await self.consolidate_pass()
        except Exception as e:
            logger.exception("consolidate_pass failed: %s", e)
        try:
            stats.importance = await self.importance_pass()
        except Exception as e:
            logger.exception("importance_pass failed: %s", e)
        try:
            stats.embed = self.embed_pass()
        except Exception as e:
            logger.exception("embed_pass failed: %s", e)
        try:
            stats.ledger = self.ledger_pass()
        except Exception as e:
            logger.exception("ledger_pass failed: %s", e)
        stats.finished_at = _utcnow()
        return stats

    async def run_loop(self, stop_event: asyncio.Event | None = None) -> None:
        """Stochastic forever-loop. Sleeps a random interval between ticks.

        Runs one tick immediately on startup so newly-decayed-or-stale facts
        clean up at boot. Subsequent ticks wait the full jittered interval.
        """
        cfg = self.config
        stop = stop_event or asyncio.Event()
        first = True
        while not stop.is_set():
            try:
                stats = await self.tick()
                logger.info(
                    "consolidator tick: decay=%s consolidate=%s ledger=%s",
                    stats.decay.__dict__,
                    stats.consolidate.__dict__,
                    stats.ledger.__dict__,
                )
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.exception("consolidator tick crashed: %s", e)
            if first:
                first = False
            wait = random.randint(cfg.sleep_min_seconds, cfg.sleep_max_seconds)
            try:
                await asyncio.wait_for(stop.wait(), timeout=wait)
                # If we got here without timeout, stop was set
                return
            except asyncio.TimeoutError:
                continue
