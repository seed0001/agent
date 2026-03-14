"""Five-layer memory system for the assistive agent. Persists profile and short-term across sessions.
Memory decay: power-law retention R(t) = 1/(1 + t/tau)^alpha (Jost/Wixted-Ebbesen).
"""
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config.settings import USER_PROFILES_DIR

PROFILE_CATEGORIES = ("background", "work", "preferences", "personal", "other")

# Memory decay (power-law: Jost 1897, Wixted & Ebbesen 1991)
MEMORY_TAU_SEC = 3600  # time constant (1 hour)
MEMORY_ALPHA = 0.2     # decay exponent
MEMORY_RETENTION_THRESHOLD = 0.05  # drop if R < this


def _retention(seconds_since: float, strength: float = 1.0) -> float:
    """Power-law retention: R = strength / (1 + t/tau)^alpha."""
    if seconds_since <= 0:
        return float(strength)
    return float(strength) / ((1 + seconds_since / MEMORY_TAU_SEC) ** MEMORY_ALPHA)


def _parse_ts(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return t
    except (ValueError, TypeError):
        return None


@dataclass
class MemoryEntry:
    """Single memory entry. strength=1.0 = baseline; affects decay rate."""
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

    @classmethod
    def from_dict(cls, d: dict) -> "MemoryEntry":
        return cls(
            content=d.get("content", ""),
            timestamp=d.get("timestamp", ""),
            metadata=d.get("metadata", {}),
            strength=float(d.get("strength", 1.0)),
        )


class MemoryStore:
    """5-layer memory: immediate, short-term (persisted), working, episodic, user profile (persisted)."""

    def __init__(self, user_id: str = "default"):
        self.user_id = user_id
        self.user_dir = USER_PROFILES_DIR / user_id
        self.user_dir.mkdir(parents=True, exist_ok=True)

        self.immediate: list[MemoryEntry] = []
        self.short_term: list[MemoryEntry] = []
        self.short_term_max = 30
        self.working: dict[str, Any] = {}

        self.short_term_path = self.user_dir / "short_term.jsonl"
        self.episodic_path = self.user_dir / "episodic.jsonl"
        self.profile_path = self.user_dir / "profile.json"
        self.working_path = self.user_dir / "working.json"
        self.thoughts_path = self.user_dir / "thoughts.jsonl"

        self._load_short_term()
        self._load_working()

    def _load_short_term(self) -> None:
        """Load short-term from disk so it survives restarts."""
        if not self.short_term_path.exists():
            return
        try:
            with open(self.short_term_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = json.loads(line)
                        self.short_term.append(MemoryEntry.from_dict(d))
                    except json.JSONDecodeError:
                        continue
            # Keep only the most recent
            if len(self.short_term) > self.short_term_max:
                self.short_term = self.short_term[-self.short_term_max :]
        except OSError:
            pass

    def _save_short_term(self) -> None:
        with open(self.short_term_path, "w", encoding="utf-8") as f:
            for e in self.short_term:
                f.write(json.dumps(e.to_dict(), ensure_ascii=False) + "\n")

    def _load_working(self) -> None:
        if not self.working_path.exists():
            return
        try:
            with open(self.working_path, encoding="utf-8") as f:
                self.working = json.load(f)
        except (OSError, json.JSONDecodeError):
            pass

    def _save_working(self) -> None:
        if not self.working:
            if self.working_path.exists():
                self.working_path.unlink()
            return
        with open(self.working_path, "w", encoding="utf-8") as f:
            json.dump(self.working, f, indent=2)

    def add_immediate(self, content: str, **metadata: Any) -> None:
        self.immediate.append(MemoryEntry(content=content, metadata=metadata))

    def _is_blank_user_input(self, content: str) -> bool:
        c = (content or "").strip()
        return not c or c in ("User:", "User") or (c.startswith("User:") and not c[5:].strip())

    def add_short_term(self, content: str, **metadata: Any) -> None:
        if self._is_blank_user_input(content):
            return
        self.short_term.append(MemoryEntry(content=content, metadata=metadata))
        while len(self.short_term) > self.short_term_max:
            old = self.short_term.pop(0)
            self._append_episodic(old.content, old.metadata)
        self._save_short_term()

    def set_working(self, key: str, value: Any) -> None:
        if value is None:
            self.working.pop(key, None)
        else:
            self.working[key] = value
        self._save_working()

    def get_working(self, key: str, default: Any = None) -> Any:
        return self.working.get(key, default)

    def clear_immediate(self) -> None:
        self.immediate.clear()

    def _append_episodic(self, content: str, metadata: dict) -> None:
        if self._is_blank_user_input(content):
            return
        with open(self.episodic_path, "a", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    {
                        "content": content,
                        "metadata": metadata,
                        "ts": datetime.now().isoformat(),
                        "strength": 1.0,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )

    def add_profile_fact(self, category: str, fact: str) -> str:
        """Add a fact to the user profile. Category: background, work, preferences, personal, other."""
        cat = category.lower() if category else "other"
        if cat not in PROFILE_CATEGORIES:
            cat = "other"
        data = self._load_profile_data()
        facts = data.setdefault("facts", {})
        lst = facts.setdefault(cat, [])
        fact = fact.strip()
        if not fact:
            return "Empty fact, not stored."
        if fact not in lst:
            lst.append(fact)
        data["updated"] = datetime.now().isoformat()
        with open(self.profile_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return f"Stored in profile ({cat})."

    def _load_profile_data(self) -> dict:
        if not self.profile_path.exists():
            return {"facts": {}, "summary": "", "updated": ""}
        try:
            with open(self.profile_path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return {"facts": {}, "summary": "", "updated": ""}

    def _load_recent_episodic(self, max_lines: int = 20) -> str | None:
        if not self.episodic_path.exists():
            return None
        entries: list[tuple[datetime | None, str]] = []
        now = datetime.now(timezone.utc)
        try:
            with open(self.episodic_path, encoding="utf-8") as f:
                all_lines = f.readlines()
            for line in all_lines[-max_lines * 3:]:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                    content = d.get("content", "")
                    ts = _parse_ts(d.get("ts", ""))
                    strength = float(d.get("strength", 1.0))
                    if ts is None:
                        retention = 1.0
                    else:
                        sec = (now - ts).total_seconds()
                        retention = _retention(sec, strength)
                    if retention >= MEMORY_RETENTION_THRESHOLD and content:
                        entries.append((ts, content))
                except (json.JSONDecodeError, ValueError, TypeError):
                    continue
            # chronological order (most recent last), take last max_lines
            entries.sort(key=lambda x: (x[0] or datetime.min.replace(tzinfo=timezone.utc),))
            lines = [c for _, c in entries[-max_lines:]]
        except OSError:
            return None
        if not lines:
            return None
        return "\n".join(f"- {c}" for c in lines)

    def _load_recent_thoughts(self, max_lines: int = 8) -> str | None:
        """Load recent background thoughts for context. Applies memory decay."""
        if not self.thoughts_path.exists():
            return None
        entries: list[tuple[datetime | None, str]] = []
        now = datetime.now(timezone.utc)
        try:
            with open(self.thoughts_path, encoding="utf-8") as f:
                all_lines = f.readlines()
            for line in all_lines[-max_lines * 3:]:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                    content = d.get("content", "")
                    ts = _parse_ts(d.get("ts", ""))
                    strength = float(d.get("strength", 1.0))
                    if ts is None:
                        retention = 1.0
                    else:
                        sec = (now - ts).total_seconds()
                        retention = _retention(sec, strength)
                    if retention >= MEMORY_RETENTION_THRESHOLD and content:
                        entries.append((ts, content))
                except (json.JSONDecodeError, ValueError, TypeError):
                    continue
            entries.sort(key=lambda x: (x[0] or datetime.min.replace(tzinfo=timezone.utc),))
            lines_list = [c for _, c in entries[-max_lines:]]
        except OSError:
            return None
        if not lines_list:
            return None
        return "\n".join(f"- {c}" for c in lines_list)

    def append_thought(self, content: str, strength: float = 1.0) -> None:
        """Append a background thought (called by background_thoughts module)."""
        with open(self.thoughts_path, "a", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    {"content": content, "ts": datetime.now().isoformat(), "strength": strength},
                    ensure_ascii=False,
                )
                + "\n"
            )

    def _format_profile(self, data: dict) -> str:
        parts: list[str] = []
        facts = data.get("facts", {})
        for cat in PROFILE_CATEGORIES:
            items = facts.get(cat, [])
            if items:
                label = cat.replace("_", " ").title()
                parts.append(f"{label}: " + "; ".join(items))
        summary = (data.get("summary") or "").strip()
        if summary:
            parts.append("Summary: " + summary)
        return "\n".join(parts) if parts else ""

    def get_context_for_agent(self, max_immediate: int = 5, max_short: int = 20) -> str:
        parts: list[str] = []

        if self.immediate:
            recent = self.immediate[-max_immediate:]
            parts.append("## Immediate context (current turn)")
            for e in recent:
                parts.append(f"- {e.content}")

        if self.short_term:
            recent = self.short_term[-max_short:]
            parts.append("\n## Recent conversation")
            for e in recent:
                ts = e.timestamp[:19] if e.timestamp else ""
                parts.append(f"- [{ts}] {e.content}")

        if self.working:
            parts.append("\n## Working memory (active task)")
            for k, v in self.working.items():
                parts.append(f"- {k}: {v}")

        episodic = self._load_recent_episodic()
        if episodic:
            parts.append("\n## Episodic memory (past sessions)")
            parts.append(episodic)

        thoughts = self._load_recent_thoughts()
        if thoughts:
            parts.append("\n## Your recent background thoughts")
            parts.append(thoughts)

        data = self._load_profile_data()
        profile = self._format_profile(data)
        if profile:
            parts.append("\n## User profile (remember this person)")
            parts.append(profile)

        return "\n".join(parts) if parts else ""

    def get_profile_view(self) -> dict:
        """Return profile data for UI: facts by category, updated timestamp."""
        return self._load_profile_data()

    def get_episodic_view(self, max_items: int = 100) -> list[dict]:
        """Return episodic entries for UI: content, timestamp."""
        if not self.episodic_path.exists():
            return []
        entries: list[dict] = []
        try:
            with open(self.episodic_path, encoding="utf-8") as f:
                lines = f.readlines()
            for line in reversed(lines[-max_items:]):
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                    entries.insert(0, {"content": d.get("content", ""), "ts": d.get("ts", "")})
                except json.JSONDecodeError:
                    continue
        except OSError:
            pass
        return entries

    def get_working_view(self) -> dict:
        """Return working memory for UI."""
        return dict(self.working)

    def get_thoughts_view(self, max_items: int = 50) -> list[dict]:
        """Return background thoughts for UI."""
        if not self.thoughts_path.exists():
            return []
        entries: list[dict] = []
        try:
            with open(self.thoughts_path, encoding="utf-8") as f:
                lines = f.readlines()
            for line in reversed(lines[-max_items:]):
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                    entries.insert(0, {"content": d.get("content", ""), "ts": d.get("ts", "")})
                except json.JSONDecodeError:
                    continue
        except OSError:
            pass
        return entries
