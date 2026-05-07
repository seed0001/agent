"""Slash-command parser for memory administration.

Both the chat layer (web + CLI) and the Discord adapter feed user input
through ``handle(text, memory)``. If the text starts with a known slash
command, we run it and return the response string. Otherwise we return None
and the caller proceeds to the LLM as usual.

Supported commands (mirrors seed0001/Adam):

    /memory                       — show profile facts with health bars
    /memory decay <days>          — set decay half-life for this profile
    /memory min <pct>             — set minimum confidence floor (0-100)
    /memory stats                 — counts and last-tick info
    /remember <key> = <value>     — store a protected fact (key in 'category::body' or just freeform)
    /remember <category>: <fact>  — convenience shorthand
    /forget <key|substring>       — soft-delete a fact (matches by key or value substring)
    /forget all                   — wipe all profile facts (asks for confirmation token)
    /protect <key|substring>      — make a fact immortal
    /unprotect <key|substring>    — let a fact decay again
    /thoughts [N]                 — show last N background thoughts
    /sessions [N]                 — show last N sessions
    /memory help                  — list these commands
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from src.agent.memory import MemoryStore, _fact_key
from src.agent.memory_stores import ProfileFact

# ---------- Match table -----------------------------------------------------

CONFIRM_TOKEN = "yes-i-am-sure"


@dataclass
class CommandResult:
    text: str
    handled: bool = True


def is_command(text: str) -> bool:
    if not text:
        return False
    t = text.lstrip()
    return t.startswith("/") and len(t) > 1 and not t.startswith("//")


# ---------- Public entry point ----------------------------------------------


def handle(text: str, memory: MemoryStore) -> str | None:
    """Try to handle ``text`` as a memory slash command.

    Returns the response string if handled, ``None`` to indicate the chat
    layer should proceed to the LLM as normal.
    """
    if not is_command(text):
        return None
    body = text.lstrip()[1:].strip()
    if not body:
        return _help()
    parts = body.split(maxsplit=1)
    cmd = parts[0].lower()
    rest = parts[1].strip() if len(parts) > 1 else ""

    try:
        if cmd in ("memory", "mem"):
            return _cmd_memory(rest, memory)
        if cmd == "remember":
            return _cmd_remember(rest, memory)
        if cmd == "forget":
            return _cmd_forget(rest, memory)
        if cmd == "protect":
            return _cmd_set_protected(rest, memory, True)
        if cmd == "unprotect":
            return _cmd_set_protected(rest, memory, False)
        if cmd == "thoughts":
            return _cmd_thoughts(rest, memory)
        if cmd == "sessions":
            return _cmd_sessions(rest, memory)
        if cmd in ("help", "?"):
            return _help()
    except Exception as e:
        return f"command error: {e}"
    return None  # not a memory command — let the LLM handle it


# ---------- /memory ---------------------------------------------------------


def _cmd_memory(rest: str, memory: MemoryStore) -> str:
    if not rest:
        return _format_memory_overview(memory)
    parts = rest.split(maxsplit=1)
    sub = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""
    if sub == "help":
        return _help()
    if sub == "stats":
        return _format_memory_stats(memory)
    if sub == "decay":
        return _cmd_memory_decay(arg, memory)
    if sub == "min":
        return _cmd_memory_min(arg, memory)
    return f"unknown /memory subcommand: {sub}. Try `/memory help`."


def _format_memory_overview(memory: MemoryStore) -> str:
    facts = memory.profile.get_all()
    if not facts:
        return "no profile facts yet."
    by_cat: dict[str, list[ProfileFact]] = {}
    for f in facts:
        by_cat.setdefault(f.category, []).append(f)

    lines = [f"## Profile memory  ({len(facts)} fact{'s' if len(facts) != 1 else ''})"]
    for cat in sorted(by_cat):
        lines.append(f"\n### {cat}")
        for f in by_cat[cat]:
            badge = "PROTECTED" if f.protected else f.source
            ref = (
                f.last_referenced_at.isoformat(timespec="seconds")
                if f.last_referenced_at
                else "never"
            )
            lines.append(
                f"  {_health_bar(f.confidence)}  {f.confidence:.2f}  "
                f"[{badge:>12s}]  v{f.version}  ref:{ref}\n"
                f"      {f.value}\n"
                f"      key: {f.key}"
            )
    return "\n".join(lines)


def _format_memory_stats(memory: MemoryStore) -> str:
    facts = memory.profile.get_all()
    protected = sum(1 for f in facts if f.protected)
    by_source: dict[str, int] = {}
    for f in facts:
        by_source[f.source] = by_source.get(f.source, 0) + 1
    eps = memory.episodic.count()
    sessions = memory.sessions.list_recent(limit=1000)
    thoughts = memory.thoughts.get_recent(limit=1000)
    return (
        f"facts: {len(facts)} ({protected} protected) by source: {by_source}\n"
        f"episodic turns: {eps}\n"
        f"sessions: {len(sessions)} (current: {memory.session_id[:8]}…)\n"
        f"thoughts: {len(thoughts)}"
    )


def _cmd_memory_decay(arg: str, memory: MemoryStore) -> str:
    try:
        days = float(arg)
    except (ValueError, TypeError):
        return "usage: /memory decay <days> (e.g. /memory decay 14)"
    if days <= 0:
        return "decay half-life must be positive"
    memory.state.set("memory.config.half_life_days", days)
    return f"decay half-life for this profile set to {days} days. (consolidator picks this up on next tick)"


def _cmd_memory_min(arg: str, memory: MemoryStore) -> str:
    try:
        pct = float(arg)
    except (ValueError, TypeError):
        return "usage: /memory min <0-100>"
    if pct < 0 or pct > 100:
        return "value must be in 0..100"
    memory.state.set("memory.config.min_confidence", pct / 100.0)
    return f"prune-floor set to {pct:.1f}% (={pct/100.0:.3f}). Takes effect on next decay pass."


# ---------- /remember -------------------------------------------------------


def _cmd_remember(rest: str, memory: MemoryStore) -> str:
    if not rest:
        return (
            "usage:\n"
            "  /remember <category>: <fact>\n"
            "  /remember <key> = <value>"
        )
    # Equals form: explicit key=value
    if "=" in rest and ":" not in rest.split("=", 1)[0]:
        key, value = rest.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or not value:
            return "both key and value are required"
        category = "general"
        try:
            memory.profile.set(
                key, value, category=category, source="user", protected=True
            )
            return f"remembered (key='{key}', protected)."
        except ValueError as e:
            return f"couldn't store: {e}"
    # Colon form: category: fact
    if ":" in rest:
        cat, fact = rest.split(":", 1)
        return memory.add_profile_fact(cat.strip(), fact.strip())
    # Free-form: drop into 'other'
    return memory.add_profile_fact("other", rest.strip())


# ---------- /forget ---------------------------------------------------------


def _cmd_forget(rest: str, memory: MemoryStore) -> str:
    if not rest:
        return "usage: /forget <key-or-substring>  |  /forget all " + CONFIRM_TOKEN
    if rest.lower().startswith("all"):
        token = rest[3:].strip()
        if token != CONFIRM_TOKEN:
            return (
                f"this wipes every profile fact for this profile.\n"
                f"to confirm, run: /forget all {CONFIRM_TOKEN}"
            )
        facts = memory.profile.get_all()
        n = 0
        for f in facts:
            if memory.profile.delete(f.key):
                n += 1
        return f"forgot {n} fact(s)."
    matches = _resolve_keys(rest, memory)
    if not matches:
        return f"no fact matches '{rest}'"
    if len(matches) > 1:
        listing = "\n".join(f"  - {m.key}  : {m.value[:80]}" for m in matches)
        return f"'{rest}' matched {len(matches)} facts. Be more specific:\n{listing}"
    target = matches[0]
    if memory.profile.delete(target.key):
        return f"forgot: {target.value!r}"
    return "could not delete (already gone?)"


# ---------- /protect /unprotect --------------------------------------------


def _cmd_set_protected(rest: str, memory: MemoryStore, protected: bool) -> str:
    if not rest:
        verb = "protect" if protected else "unprotect"
        return f"usage: /{verb} <key-or-substring>"
    matches = _resolve_keys(rest, memory)
    if not matches:
        return f"no fact matches '{rest}'"
    if len(matches) > 1:
        listing = "\n".join(f"  - {m.key}  : {m.value[:80]}" for m in matches)
        return f"'{rest}' matched {len(matches)} facts. Be more specific:\n{listing}"
    target = matches[0]
    if memory.profile.protect(target.key, protected=protected):
        word = "protected" if protected else "unprotected"
        return f"{word}: {target.value!r}"
    return "no change"


# ---------- /thoughts /sessions --------------------------------------------


def _cmd_thoughts(rest: str, memory: MemoryStore) -> str:
    n = _safe_int(rest, default=8, min_v=1, max_v=200)
    rows = memory.thoughts.get_recent(limit=n)
    if not rows:
        return "no background thoughts yet."
    lines = [f"## Last {len(rows)} thoughts (oldest first)"]
    for t in rows:
        flag = "✓" if t.delivered else " "
        reason = f" (rejected: {t.reject_reason})" if t.reject_reason else ""
        ts = t.created_at.isoformat(timespec="seconds")
        lines.append(f" {flag} [{ts}] {t.content}{reason}")
    return "\n".join(lines)


def _cmd_sessions(rest: str, memory: MemoryStore) -> str:
    n = _safe_int(rest, default=10, min_v=1, max_v=200)
    rows = memory.sessions.list_recent(limit=n)
    if not rows:
        return "no sessions on record."
    lines = [f"## Last {len(rows)} sessions"]
    for s in rows:
        marker = " (current)" if s.id == memory.session_id else ""
        active = "open" if s.ended_at is None else f"ended {s.ended_at.isoformat(timespec='seconds')}"
        lines.append(
            f"  {s.id[:8]}… {s.source:>8s}  started {s.started_at.isoformat(timespec='seconds')}  {active}{marker}"
        )
    return "\n".join(lines)


# ---------- helpers ---------------------------------------------------------


def _resolve_keys(query: str, memory: MemoryStore) -> list[ProfileFact]:
    """Return facts matching the query — exact key first, then substring on
    key, then substring on value (case-insensitive)."""
    q = query.strip()
    if not q:
        return []
    exact = memory.profile.get(q)
    if exact:
        return [exact]
    # Try the stable-key encoding for "category: fact" inputs
    if ":" in q:
        cat, body = q.split(":", 1)
        candidate = memory.profile.get(_fact_key(cat.strip(), body.strip()))
        if candidate:
            return [candidate]
    needle = q.lower()
    out: list[ProfileFact] = []
    for f in memory.profile.get_all():
        if needle in f.key.lower() or needle in f.value.lower():
            out.append(f)
    return out


def _safe_int(text: str, default: int, min_v: int, max_v: int) -> int:
    try:
        n = int(text)
    except (TypeError, ValueError):
        return default
    return max(min_v, min(max_v, n))


_BAR_WIDTH = 10


def _health_bar(confidence: float) -> str:
    confidence = max(0.0, min(1.0, confidence))
    full = int(round(confidence * _BAR_WIDTH))
    return "[" + ("=" * full) + (" " * (_BAR_WIDTH - full)) + "]"


_HELP = re.sub(r"^ {4}", "", """
    Memory commands:
      /memory                          show all profile facts with health bars
      /memory stats                    counts and current session info
      /memory decay <days>             set decay half-life for this profile
      /memory min <0-100>              set the prune floor as percent
      /remember <category>: <fact>     store a protected fact
      /remember <key> = <value>        store with explicit key
      /forget <key-or-substring>       soft-delete one fact
      /forget all yes-i-am-sure        wipe all profile facts (with confirmation)
      /protect <key-or-substring>      make a fact immortal
      /unprotect <key-or-substring>    let it decay naturally again
      /thoughts [N]                    show last N background thoughts (default 8)
      /sessions [N]                    show last N sessions (default 10)
      /memory help                     show this list
""", flags=re.MULTILINE).strip()


def _help() -> str:
    return _HELP
