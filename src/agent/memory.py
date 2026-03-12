"""Five-layer memory system for the assistive agent."""
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from config.settings import MEMORY_DIR, USER_PROFILES_DIR


@dataclass
class MemoryEntry:
    """Single memory entry."""
    content: str
    timestamp: datetime = field(default_factory=datetime.now)
    metadata: dict[str, Any] = field(default_factory=dict)


class MemoryStore:
    """5-layer memory: immediate, short-term, working, episodic, user profile."""

    def __init__(self, user_id: str = "default"):
        self.user_id = user_id
        self.user_dir = USER_PROFILES_DIR / user_id
        self.user_dir.mkdir(parents=True, exist_ok=True)

        # Layer 1: Immediate (current turn)
        self.immediate: list[MemoryEntry] = []

        # Layer 2: Short-term (last N turns)
        self.short_term: list[MemoryEntry] = []
        self.short_term_max = 20

        # Layer 3: Working (active task state)
        self.working: dict[str, Any] = {}

        # Layer 4: Episodic (past sessions - persisted)
        self.episodic_path = self.user_dir / "episodic.jsonl"

        # Layer 5: User profile (persisted)
        self.profile_path = self.user_dir / "profile.json"

    def add_immediate(self, content: str, **metadata):
        self.immediate.append(MemoryEntry(content=content, metadata=metadata))

    def add_short_term(self, content: str, **metadata):
        self.short_term.append(MemoryEntry(content=content, metadata=metadata))
        if len(self.short_term) > self.short_term_max:
            # Promote oldest to episodic
            old = self.short_term.pop(0)
            self._append_episodic(old.content, old.metadata)

    def set_working(self, key: str, value: Any):
        if value is None:
            self.working.pop(key, None)
        else:
            self.working[key] = value

    def get_working(self, key: str, default=None):
        return self.working.get(key, default)

    def clear_immediate(self):
        self.immediate.clear()

    def _load_recent_episodic(self, max_lines: int = 15) -> str | None:
        """Load recent episodic entries for context."""
        if not self.episodic_path.exists():
            return None
        import json
        lines = []
        try:
            with open(self.episodic_path, encoding="utf-8") as f:
                all_lines = f.readlines()
            for line in all_lines[-max_lines:]:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    lines.append(data.get("content", ""))
                except json.JSONDecodeError:
                    continue
        except OSError:
            return None
        if not lines:
            return None
        return "\n".join(f"- {c}" for c in lines)

    def get_context_for_agent(self, max_immediate: int = 5, max_short: int = 15) -> str:
        """Build context string for the agent from all 5 layers."""
        parts = []

        if self.immediate:
            recent = self.immediate[-max_immediate:]
            parts.append("## Immediate context (current turn)")
            for e in recent:
                parts.append(f"- {e.content}")

        if self.short_term:
            recent = self.short_term[-max_short:]
            parts.append("\n## Recent conversation")
            for e in recent:
                parts.append(f"- [{e.timestamp.isoformat()}] {e.content}")

        if self.working:
            parts.append("\n## Working memory (active task)")
            for k, v in self.working.items():
                parts.append(f"- {k}: {v}")

        episodic = self._load_recent_episodic()
        if episodic:
            parts.append("\n## Episodic memory (past sessions)")
            parts.append(episodic)

        profile = self._load_profile()
        if profile:
            parts.append("\n## User profile")
            parts.append(profile)

        return "\n".join(parts) if parts else ""

    def _append_episodic(self, content: str, metadata: dict):
        with open(self.episodic_path, "a", encoding="utf-8") as f:
            import json
            f.write(json.dumps({"content": content, "metadata": metadata, "ts": datetime.now().isoformat()}) + "\n")

    def _load_profile(self) -> str | None:
        if not self.profile_path.exists():
            return None
        import json
        with open(self.profile_path, encoding="utf-8") as f:
            return json.load(f).get("summary", "")

    def update_profile(self, summary: str):
        import json
        data = {"summary": summary, "updated": datetime.now().isoformat()}
        with open(self.profile_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
