"""Soul file: agent identity, owner, and behavior. Built during first-boot setup."""
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from config.settings import DATA_DIR

SOUL_PATH = DATA_DIR / "soul.json"

DEFAULT_SOUL = {
    "agent_name": "",
    "agent_tone": ["direct"],
    "agent_how_to_act": [
        "Be direct. Ask when you need clarity.",
        "Never cut corners for speed.",
        "If you disagree, say so.",
    ],
    "agent_goals": ["Get the job done", "Stay accurate"],
    "owner_name": "",
    "owner_discord_id": "",
    "owner_facts": [],
    "created_at": "",
    "updated_at": "",
}


def load_soul() -> dict[str, Any] | None:
    """Load soul from disk. Returns None if file doesn't exist, invalid, or owner not set."""
    if not SOUL_PATH.exists():
        return None
    try:
        with open(SOUL_PATH, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return None
        owner_name = (data.get("owner_name") or "").strip()
        if not owner_name:
            return None  # Incomplete setup
        return data
    except (OSError, json.JSONDecodeError):
        return None


def needs_setup() -> bool:
    """True if soul doesn't exist, or owner/agent name hasn't been established."""
    soul = load_soul()
    if soul is None:
        return True
    owner_name = (soul.get("owner_name") or "").strip()
    agent_name = (soul.get("agent_name") or "").strip()
    return not bool(owner_name) or not bool(agent_name)


def save_soul(data: dict[str, Any]) -> None:
    """Write soul to disk."""
    data = {**DEFAULT_SOUL, **data}
    data["updated_at"] = datetime.now().isoformat()
    if not data.get("created_at"):
        data["created_at"] = data["updated_at"]
    SOUL_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(SOUL_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def complete_setup(
    *,
    owner_name: str,
    owner_discord_id: str = "",
    owner_facts: list[str] | None = None,
    agent_name: str = "",
    agent_tone: list[str] | None = None,
    agent_how_to_act: list[str] | None = None,
) -> str:
    """
    Called when setup is done. Saves soul and returns success message.
    owner_name and agent_name are required (both chosen by the user during onboarding).
    """
    owner_name = (owner_name or "").strip()
    agent_name = (agent_name or "").strip()
    if not owner_name:
        return "Setup incomplete: owner_name is required."
    if not agent_name:
        return "Setup incomplete: agent_name is required (what the owner wants to call you)."

    existing = load_soul()
    data: dict[str, Any] = existing.copy() if existing else {}

    data["owner_name"] = owner_name
    if owner_discord_id:
        data["owner_discord_id"] = str(owner_discord_id).strip()
    if owner_facts:
        data["owner_facts"] = [f.strip() for f in owner_facts if f.strip()]
    data["agent_name"] = agent_name
    if agent_tone is not None:
        data["agent_tone"] = [t.strip() for t in agent_tone if t.strip()] or ["direct"]
    if agent_how_to_act is not None:
        data["agent_how_to_act"] = [a.strip() for a in agent_how_to_act if a.strip()]

    save_soul(data)
    return f"Setup complete. You know {owner_name} as your owner, and they call you {agent_name}."


def format_soul_for_prompt(soul: dict[str, Any]) -> str:
    """Format soul for injection into system prompt."""
    parts = []
    name = (soul.get("agent_name") or "").strip()
    owner = soul.get("owner_name", "").strip()
    tone = soul.get("agent_tone", ["direct"])
    how = soul.get("agent_how_to_act", [])
    goals = soul.get("agent_goals", [])

    if name:
        parts.append(f"Your name is {name}.")
    if owner:
        parts.append(f"Owner: {owner}.")
    if tone:
        parts.append(f"Your tone: {', '.join(tone)}.")
    if how:
        parts.append("How you act: " + "; ".join(how) + ".")
    if goals:
        parts.append("Your goals: " + "; ".join(goals) + ".")
    return " ".join(parts)


def get_owner_name() -> str:
    """Return owner's name if soul exists, else empty string."""
    soul = load_soul()
    if soul:
        return (soul.get("owner_name") or "").strip()
    return ""


def get_context_for_speaker(
    *,
    is_web: bool = False,
    discord_id: str | None = None,
    author_name: str = "",
) -> str:
    """
    Build context line for who's messaging. No hardcoded names.
    When soul exists and we know the owner, use their name.
    """
    soul = load_soul()
    owner_name = (soul.get("owner_name") or "").strip() if soul else ""
    owner_discord_id = (soul.get("owner_discord_id") or "").strip() if soul else ""

    from config.settings import DISCORD_OWNER_ID
    is_owner = False
    if discord_id:
        is_owner = (
            str(discord_id) == str(DISCORD_OWNER_ID or "")
            or str(discord_id) == owner_discord_id
        )
    else:
        is_owner = is_web

    if is_web:
        if owner_name:
            return f"[{owner_name} is messaging from the web app (desktop, at home).]\n\n"
        return "[The user is messaging from the web app (desktop, at home).]\n\n"

    if discord_id and is_owner:
        if owner_name:
            return f"[{owner_name} is messaging via Discord—remote, likely on a phone, possibly not at home.]\n\n"
        return "[Your primary user is messaging via Discord—remote, likely on a phone, possibly not at home.]\n\n"

    return ""
