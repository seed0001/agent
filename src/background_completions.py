"""Persistent queue of background task completions. Injected into context so Nova can review and notify."""
import json
from pathlib import Path

from config.settings import USER_PROFILES_DIR


def _path(user_id: str = "default") -> Path:
    return USER_PROFILES_DIR / user_id / "pending_completions.jsonl"


def add(aid: str, task: str, status: str, user_id: str = "default") -> None:
    """Record a completion. Call when subagent finishes."""
    p = _path(user_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    entry = {"aid": aid, "task": task, "status": status}
    from datetime import datetime
    entry["ts"] = datetime.now().isoformat()
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def get_pending(user_id: str = "default") -> list[dict]:
    """Return list of pending completions (not yet acknowledged)."""
    p = _path(user_id)
    if not p.exists():
        return []
    out = []
    with open(p, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


def acknowledge(aid: str, user_id: str = "default") -> bool:
    """Remove a completion from pending. Returns True if found."""
    orig = get_pending(user_id)
    had = any(e.get("aid") == aid for e in orig)
    pending = [e for e in orig if e.get("aid") != aid]
    p = _path(user_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        for e in pending:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    return had


def get_context_block(user_id: str = "default") -> str:
    """Format pending completions for context injection."""
    pending = get_pending(user_id)
    if not pending:
        return ""
    lines = [f"- {e['aid']} ({e['task']}): {e['status']}" for e in pending]
    return (
        "\n## Pending background completions (review & notify Creator)\n"
        + "\n".join(lines)
        + "\n\nFor each: get_subagent_output(aid), verify OK, then notify Creator (direct Discord/web fallback). "
        "Call acknowledge_background_completion(aid) when done.\n"
    )
