"""Deterministic recall-intent router.

When the user asks a recall question — "do you remember", "recap", "catch me
up", "what were we doing yesterday" — we must NOT defer to the LLM and hope
it volunteers to call ``search_memory``. We bypass the LLM:

1. Detect the intent from the user message via regex.
2. Run a real retrieval pass (lexical + semantic + threads + ledger + facts).
3. Inject the result as a dedicated, prominent ``## Continuity recall`` block
   into the system prompt for this turn.
4. Log a ``recall_events`` row for traceability.

The agent's prompt then sees concrete evidence and can speak from it. The
``hard_guard`` layer (in ``core.py``) catches the rare case where the LLM
still claims amnesia — when that happens we re-run with the recall block
made even more prominent.

Design notes:
- All matching is done with regex against the raw user message (case
  insensitive). No prompt-engineering tricks.
- The query terms used for retrieval come from the user message minus stop
  words; if the message is mostly a recall phrase ("recap"), we widen the
  query to "what we worked on" by pulling recent threads + last session
  summary instead of trying to match on tokens.
- The router is *additive* — it never removes context, only adds.
"""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from src.agent.memory_db import get_connection
from src.agent.memory_stores import _now_iso, _parse_iso, _utcnow

# ---------- Intent detection -------------------------------------------------

# Each pattern carries a label that ends up in ``recall_events.phrase_matched``
# so we can audit what triggers in production. Order matters slightly — first
# match wins for the label, but all matches contribute to the "wide" flag.

_RECALL_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("recap",            re.compile(r"\brecap\b", re.IGNORECASE)),
    ("catch_me_up",      re.compile(r"\bcatch (me )?up\b", re.IGNORECASE)),
    ("do_you_remember",  re.compile(r"\bdo you (still )?remember\b", re.IGNORECASE)),
    ("you_remember",     re.compile(r"\byou (don'?t|do not) remember\b", re.IGNORECASE)),
    ("did_we_x",         re.compile(r"\bdid (we|you) (talk|discuss|mention|say|cover|build|do|fix|ship|push)\b", re.IGNORECASE)),
    ("have_we",          re.compile(r"\bhave (we|you) (talked|discussed|mentioned|covered|built|done|fixed|shipped)\b", re.IGNORECASE)),
    ("what_did_we",      re.compile(r"\bwhat (did|have) (we|you|i)\b", re.IGNORECASE)),
    ("what_were_we",     re.compile(r"\bwhat (was|were) (we|you|i) (working on|doing|saying|talking|building)\b", re.IGNORECASE)),
    ("yesterday",        re.compile(r"\b(yesterday|last (time|night|week|day|few days|two days))\b", re.IGNORECASE)),
    ("earlier",          re.compile(r"\bearlier (you|we|today|tonight|this week)\b", re.IGNORECASE)),
    ("the_other_day",    re.compile(r"\b(the other (day|night|week)|a (couple|few) days ago)\b", re.IGNORECASE)),
    ("where_did_we",     re.compile(r"\bwhere (did )?(we|you) (leave|stop|end|land)\b", re.IGNORECASE)),
    ("from_last",        re.compile(r"\bfrom (last|earlier|the other)\b", re.IGNORECASE)),
    ("you_should_remember", re.compile(r"\b(you should|you must) remember\b", re.IGNORECASE)),
    ("we_were_talking",  re.compile(r"\b(we (were|are)|i was) (talking|discussing|building|working) (about|on)\b", re.IGNORECASE)),
    ("dont_you_remember", re.compile(r"\bdon'?t you remember\b", re.IGNORECASE)),
    ("memory_question",  re.compile(r"\b(your|the) (memory|memories|recollection)\b", re.IGNORECASE)),
    ("two_days",         re.compile(r"\b(\d+|two|three|four|five|six|seven|few|couple) (days|weeks)\b", re.IGNORECASE)),
]


# Recall phrases that, on their own, contain ~no query terms — when these are
# the *only* match we run a "wide" recall (recent threads + last session +
# top profile facts) rather than searching for the literal words.
_WIDE_ONLY_LABELS = {
    "recap",
    "catch_me_up",
    "do_you_remember",
    "you_remember",
    "dont_you_remember",
    "yesterday",
    "earlier",
    "the_other_day",
    "where_did_we",
    "from_last",
    "you_should_remember",
    "memory_question",
    "two_days",
}

_STOP = {
    "the", "and", "for", "with", "from", "have", "you", "your", "they", "them",
    "did", "do", "does", "are", "was", "were", "be", "been", "is", "it", "to",
    "of", "on", "in", "at", "as", "by", "or", "but", "an", "a", "if", "we",
    "us", "our", "i", "me", "my", "what", "when", "where", "who", "how", "why",
    "tell", "say", "said", "talk", "talked", "talking", "discuss", "discussed",
    "remember", "recall", "recap", "catch", "up", "yesterday", "earlier",
    "today", "tonight", "this", "that", "those", "these", "any", "all", "still",
    "yet", "again", "really", "just", "about", "around", "over", "back",
    "since", "while",
}


@dataclass
class RecallIntent:
    """Outcome of detection. Empty (``not intent.triggered``) when no match."""
    triggered: bool = False
    label: str = ""
    matched_labels: list[str] = field(default_factory=list)
    wide: bool = False
    query_terms: list[str] = field(default_factory=list)
    raw_text: str = ""

    def __bool__(self) -> bool:
        return self.triggered


def _extract_query_terms(text: str) -> list[str]:
    out: list[str] = []
    for tok in re.findall(r"[A-Za-z][A-Za-z0-9_'-]{2,}", text or ""):
        norm = tok.lower().strip("'-")
        if norm in _STOP or len(norm) < 4:
            continue
        if norm not in out:
            out.append(norm)
    return out[:12]


def detect_recall_intent(text: str) -> RecallIntent:
    """Return a populated ``RecallIntent`` on match, otherwise ``triggered=False``."""
    if not text or not text.strip():
        return RecallIntent()
    matched: list[str] = []
    first_label = ""
    for label, pat in _RECALL_PATTERNS:
        if pat.search(text):
            matched.append(label)
            if not first_label:
                first_label = label
    if not matched:
        return RecallIntent()
    terms = _extract_query_terms(text)
    # If the only matches are wide-only labels and the user provided no
    # specific terms, treat as a wide recall.
    wide = bool(terms == [] or all(label in _WIDE_ONLY_LABELS for label in matched))
    return RecallIntent(
        triggered=True,
        label=first_label,
        matched_labels=matched,
        wide=wide,
        query_terms=terms,
        raw_text=text,
    )


# ---------- Recall execution -------------------------------------------------


@dataclass
class RecallHit:
    source: str         # 'episodic' | 'thread' | 'fact' | 'artifact' | 'schedule' | 'ledger' | 'session_tail'
    title: str
    text: str
    score: float = 1.0
    ts: str = ""        # iso minute precision when available


@dataclass
class RecallResult:
    intent: RecallIntent
    hits: list[RecallHit] = field(default_factory=list)
    block: str = ""
    sources_summary: dict[str, int] = field(default_factory=dict)


def _truncate(text: str, n: int) -> str:
    t = (text or "").replace("\n", " ").strip()
    if len(t) <= n:
        return t
    return t[: max(0, n - 3)] + "..."


def _strip_prefix(text: str) -> str:
    t = (text or "").strip()
    t = re.sub(r"^(user|andrew|reply|jarvis|assistant|system)\s*:\s*", "", t, flags=re.IGNORECASE)
    t = re.sub(r"^\[[^\]]+\]\s*", "", t).strip()
    return t


def _record_recall_event(
    user_id: str,
    session_id: str | None,
    intent: RecallIntent,
    result: RecallResult,
    *,
    trigger_type: str = "phrase",
) -> None:
    try:
        conn = get_connection(user_id)
        sources = sorted(result.sources_summary.keys())
        preview = (result.block or "")[:1000]
        conn.execute(
            "INSERT INTO recall_events "
            "(session_id, query, trigger_type, phrase_matched, hit_count, sources, block_preview, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                session_id,
                intent.raw_text[:1000],
                trigger_type,
                ",".join(intent.matched_labels)[:200],
                len(result.hits),
                json.dumps(sources, ensure_ascii=False),
                preview,
                _now_iso(),
            ),
        )
    except sqlite3.Error:
        pass


def _hits_from_threads(memory: Any, terms: list[str], wide: bool) -> list[RecallHit]:
    out: list[RecallHit] = []
    try:
        if wide or not terms:
            threads = memory.threads.list_open(limit=10)
        else:
            seen: dict[str, Any] = {}
            for t in terms:
                for th in memory.threads.search(t, limit=8):
                    seen[th.id] = th
            threads = list(seen.values())
            if not threads:
                threads = memory.threads.list_open(limit=8)
    except Exception:
        return out
    for t in threads:
        ts = t.last_touched_at.isoformat(timespec="minutes") if t.last_touched_at else ""
        text = f"{t.title}"
        if t.description:
            text += f": {_truncate(t.description, 200)}"
        out.append(RecallHit(
            source="thread",
            title=t.title,
            text=f"[{t.status}] {text}",
            score=2.5 if t.status in ("open", "blocked") else 1.0,
            ts=ts,
        ))
    return out


def _hits_from_facts(memory: Any, terms: list[str]) -> list[RecallHit]:
    out: list[RecallHit] = []
    if not terms:
        return out
    try:
        facts = memory.profile.get_all(min_confidence=0.3)
    except Exception:
        return out
    for f in facts:
        v_low = (f.value or "").lower()
        score = sum(1 for t in terms if t in v_low)
        if score == 0:
            continue
        out.append(RecallHit(
            source="fact",
            title=f.category,
            text=f.value,
            score=float(score) + float(f.confidence),
            ts=f.updated_at.isoformat(timespec="minutes") if f.updated_at else "",
        ))
    return out


def _hits_from_episodic(memory: Any, terms: list[str], wide: bool) -> list[RecallHit]:
    out: list[RecallHit] = []
    try:
        recent = memory.episodic.get_recent_across_sessions(
            limit=400, within_days=30, exclude_session=None,
        )
    except Exception:
        recent = []
    now = _utcnow()
    for r in recent:
        if r.role not in ("user", "assistant"):
            continue
        text = _strip_prefix(r.content)
        if not text or len(text) < 8:
            continue
        blob = text.lower()
        if terms:
            lex = sum(1 for t in terms if t in blob)
            if lex == 0:
                # When wide=True, allow high-importance turns through even
                # without lexical match.
                if not wide or float(r.importance) < 0.7:
                    continue
        elif not wide:
            continue
        age_days = max(0.0, (now - r.created_at).total_seconds() / 86400.0)
        recency_bonus = 1.0 / (1.0 + age_days)
        score = float(len([t for t in terms if t in blob])) + float(r.importance) * 1.5 + recency_bonus
        out.append(RecallHit(
            source="episodic",
            title=r.role,
            text=text,
            score=score,
            ts=r.created_at.isoformat(timespec="minutes"),
        ))
    # Semantic boost when available
    try:
        idx = memory._semantic_index() if hasattr(memory, "_semantic_index") else None
    except Exception:
        idx = None
    if idx is not None and not wide and terms:
        try:
            sem = idx.find_similar_text(
                " ".join(terms), limit=8, source_table="episodic_memory",
            )
            for h in sem:
                out.append(RecallHit(
                    source="episodic",
                    title="semantic",
                    text=_strip_prefix(h.content),
                    score=float(getattr(h, "score", 0.5)) + 0.5,
                    ts="",
                ))
        except Exception:
            pass
    return out


def _hits_from_artifacts(terms: list[str]) -> list[RecallHit]:
    out: list[RecallHit] = []
    if not terms:
        return out
    try:
        from src.artifact_memory import format_artifact, search_artifacts
    except Exception:
        return out
    seen_ids: set[str] = set()
    for t in terms:
        try:
            arts = search_artifacts(t, limit=4)
        except Exception:
            arts = []
        for a in arts:
            aid = getattr(a, "id", None) or getattr(a, "path", None)
            if aid and aid in seen_ids:
                continue
            if aid:
                seen_ids.add(str(aid))
            try:
                content = format_artifact(a)
            except Exception:
                content = str(a)
            out.append(RecallHit(
                source="artifact",
                title=getattr(a, "title", "") or getattr(a, "path", "") or "artifact",
                text=content,
                score=1.5,
                ts="",
            ))
    return out


def _hits_from_schedules(terms: list[str]) -> list[RecallHit]:
    out: list[RecallHit] = []
    try:
        from src.schedule_memory import format_schedule, list_schedules
        sched = list_schedules(include_archived=False)
    except Exception:
        return out
    for s in sched:
        try:
            text = format_schedule(s)
        except Exception:
            continue
        blob = (text or "").lower()
        if terms and not any(t in blob for t in terms):
            continue
        title = getattr(s, "title", None) or getattr(s, "name", None) or "schedule"
        out.append(RecallHit(
            source="schedule",
            title=str(title),
            text=text,
            score=1.0,
            ts="",
        ))
    return out


def _hits_from_session_tail(memory: Any) -> list[RecallHit]:
    """Last few meaningful turns of the immediately prior session."""
    out: list[RecallHit] = []
    try:
        sessions = memory.sessions.list_recent(limit=4)
    except Exception:
        return out
    current_id = getattr(memory, "session_id", None)
    prior = next((s for s in sessions if s.id != current_id), None)
    if prior is None:
        return out
    try:
        turns = memory.episodic.get_by_session(prior.id, limit=200)
    except Exception:
        turns = []
    meaningful = [t for t in turns if t.role in ("user", "assistant") and (t.content or "").strip()]
    for t in meaningful[-6:]:
        out.append(RecallHit(
            source="session_tail",
            title=t.role,
            text=_strip_prefix(t.content),
            score=1.0,
            ts=t.created_at.isoformat(timespec="minutes"),
        ))
    return out


def _ledger_hit(memory: Any) -> RecallHit | None:
    try:
        row = memory.ledger.get_latest()
    except Exception:
        row = None
    if row is None or not (row.content or "").strip():
        return None
    return RecallHit(
        source="ledger",
        title=f"ledger v{row.version}",
        text=row.content,
        score=10.0,  # always pin to top of recall block
        ts=row.created_at.isoformat(timespec="minutes"),
    )


def run_deep_recall(memory: Any, intent: RecallIntent, *, max_hits: int = 12) -> RecallResult:
    """Aggregate hits across stores. The block is a ready-to-inject markdown."""
    hits: list[RecallHit] = []
    led = _ledger_hit(memory)
    if led is not None:
        hits.append(led)
    hits.extend(_hits_from_threads(memory, intent.query_terms, intent.wide))
    hits.extend(_hits_from_facts(memory, intent.query_terms))
    hits.extend(_hits_from_episodic(memory, intent.query_terms, intent.wide))
    hits.extend(_hits_from_artifacts(intent.query_terms))
    hits.extend(_hits_from_schedules(intent.query_terms))
    hits.extend(_hits_from_session_tail(memory))

    # Dedupe by (source, normalized text); keep highest score.
    best: dict[tuple[str, str], RecallHit] = {}
    for h in hits:
        key = (h.source, (h.text or "").strip().lower()[:240])
        existing = best.get(key)
        if existing is None or h.score > existing.score:
            best[key] = h
    deduped = list(best.values())
    # Always keep ledger first; sort the rest by score desc, then ts desc.
    ledger_hits = [h for h in deduped if h.source == "ledger"]
    others = [h for h in deduped if h.source != "ledger"]
    others.sort(key=lambda h: (h.score, h.ts), reverse=True)
    final = ledger_hits + others[: max(0, max_hits - len(ledger_hits))]

    sources_summary: dict[str, int] = {}
    for h in final:
        sources_summary[h.source] = sources_summary.get(h.source, 0) + 1

    return RecallResult(
        intent=intent,
        hits=final,
        block=_render_recall_block(intent, final),
        sources_summary=sources_summary,
    )


def _render_recall_block(intent: RecallIntent, hits: list[RecallHit]) -> str:
    if not hits:
        return (
            "## Continuity recall (deterministic)\n"
            "User asked a recall question, but no concrete prior context was found.\n"
            f"Query terms: {intent.query_terms or '(wide recall)'}.\n"
            "Tell them honestly that nothing matching surfaced; do NOT hallucinate."
        )
    parts: list[str] = [
        "## Continuity recall (deterministic)",
        f"_The router fired on `{intent.label}`. Speak from this evidence; do not say you don't remember._",
        "",
    ]
    by_source: dict[str, list[RecallHit]] = {}
    for h in hits:
        by_source.setdefault(h.source, []).append(h)

    order = ["ledger", "thread", "fact", "session_tail", "episodic", "artifact", "schedule"]
    for src in order:
        items = by_source.get(src, [])
        if not items:
            continue
        label = {
            "ledger": "Continuity ledger (current self-briefing)",
            "thread": "Open / recent threads",
            "fact": "Profile facts",
            "session_tail": "End of the previous session",
            "episodic": "Past conversation turns",
            "artifact": "Saved files / artifacts",
            "schedule": "Active schedules",
        }[src]
        parts.append(f"### {label}")
        for h in items:
            ts = f" _{h.ts}_" if h.ts else ""
            text = _truncate(h.text, 320)
            parts.append(f"- {text}{ts}")
        parts.append("")
    return "\n".join(parts).rstrip()


# ---------- Hard guard -------------------------------------------------------

# Phrases that indicate the model is claiming amnesia — we want to catch
# these AFTER the response is generated and force a recall pass before
# returning. Kept narrow to avoid false positives ("I don't remember the
# exact line number" is fine).

_AMNESIA_PATTERNS = [
    re.compile(r"\bi (do not|don'?t) remember\b", re.IGNORECASE),
    re.compile(r"\bi (do not|don'?t) recall\b", re.IGNORECASE),
    re.compile(r"\bi (cannot|can'?t) recall\b", re.IGNORECASE),
    re.compile(r"\bi have no memory\b", re.IGNORECASE),
    re.compile(r"\bi (do not|don'?t) have (any )?memory\b", re.IGNORECASE),
    re.compile(r"\bthis is (the )?first time\b", re.IGNORECASE),
    re.compile(r"\bwe (haven'?t|have not) (talked|discussed|covered) (about )?\b", re.IGNORECASE),
    re.compile(r"\bi (do not|don'?t) have access to (our|previous|prior|past|the|any|earlier|before)(\s+\w+){0,3}\s+(conversation|chat|history|context|memory|memories)\b", re.IGNORECASE),
    re.compile(r"\bi (do not|don'?t) have access to (it|that|those|your)?\s*(prior|previous|past|earlier)?\s*(conversation|chat|history|messages?)\b", re.IGNORECASE),
    re.compile(r"\bi (do not|don'?t) have (the )?context\b", re.IGNORECASE),
    re.compile(r"\bnothing (in )?(my )?memory\b", re.IGNORECASE),
]

# Mitigating phrases — if any of these appear nearby, treat as benign and skip.
_AMNESIA_MITIGATORS = [
    re.compile(r"exact (line|number|file|path)", re.IGNORECASE),
    re.compile(r"specific (version|build|hash)", re.IGNORECASE),
]


def detect_amnesia(content: str) -> bool:
    if not content:
        return False
    for mit in _AMNESIA_MITIGATORS:
        if mit.search(content):
            return False
    return any(p.search(content) for p in _AMNESIA_PATTERNS)


# ---------- Public top-level entry point -------------------------------------


def route_user_message(memory: Any, user_text: str) -> RecallResult | None:
    """If a recall intent fires, run a deep recall and return the result.
    Records a ``recall_events`` row for traceability.

    Returns ``None`` when no recall intent was detected.
    """
    intent = detect_recall_intent(user_text)
    if not intent:
        return None
    result = run_deep_recall(memory, intent)
    _record_recall_event(
        getattr(memory, "user_id", "default"),
        getattr(memory, "session_id", None),
        intent,
        result,
        trigger_type="phrase",
    )
    return result


def force_recall_for_guard(
    memory: Any, user_text: str, *, max_hits: int = 16
) -> RecallResult:
    """Used by the hard guard when the assistant tries to claim amnesia.
    Always runs in 'wide' mode."""
    intent = detect_recall_intent(user_text) or RecallIntent(
        triggered=True, label="guard", matched_labels=["guard"], wide=True,
        query_terms=_extract_query_terms(user_text), raw_text=user_text or "",
    )
    intent.wide = True
    result = run_deep_recall(memory, intent, max_hits=max_hits)
    _record_recall_event(
        getattr(memory, "user_id", "default"),
        getattr(memory, "session_id", None),
        intent,
        result,
        trigger_type="guard",
    )
    return result
