"""
Access policy: which tools each contact tier can use.
Creator has full access. Edit this file or data/profiles/default/access_policy.json to customize.
"""
import json
from pathlib import Path

from config.settings import USER_PROFILES_DIR

ACCESS_POLICY_PATH = USER_PROFILES_DIR / "default" / "access_policy.json"

CONTACT_TIERS = ("stranger", "friend", "good_friend", "best_friend", "creator")

# Default: tools allowed per tier. Creator = None means all.
DEFAULT_POLICY = {
    "stranger": [
        "search_knowledge",
        "read_knowledge",
        "list_knowledge_topics",
    ],
    "friend": [
        "search_web",
        "read_file",
        "list_dir",
        "search_knowledge",
        "read_knowledge",
        "list_knowledge_topics",
        "get_contacts",
    ],
    "good_friend": [
        "search_web",
        "read_file",
        "list_dir",
        "get_system_info",
        "list_processes",
        "is_process_running",
        "run_build",
        "search_knowledge",
        "read_knowledge",
        "list_knowledge_topics",
        "get_contacts",
        "update_contact",
    ],
    "best_friend": [
        "search_web",
        "read_file",
        "write_file",
        "list_dir",
        "get_system_info",
        "list_processes",
        "is_process_running",
        "run_command",
        "run_build",
        "spawn_subagent",
        "subagent_status",
        "get_subagent_output",
        "search_knowledge",
        "read_knowledge",
        "list_knowledge_topics",
        "get_contacts",
        "update_contact",
        "set_working_memory",
        "create_task_dag",
        "get_next_dag_step",
        "complete_dag_step",
        "send_proactive_message",
        "generate_image",
        "get_image_usage",
    ],
    "creator": None,  # full access
}


def _load_policy() -> dict:
    if ACCESS_POLICY_PATH.exists():
        try:
            with open(ACCESS_POLICY_PATH, encoding="utf-8") as f:
                data = json.load(f)
                return {t: (None if v is None else list(v)) for t, v in data.items()}
        except (OSError, json.JSONDecodeError):
            pass
    return dict(DEFAULT_POLICY)


def is_tool_allowed(tier: str, tool_name: str) -> bool:
    """True if the tier allows the tool. Creator always allowed."""
    if tier == "creator" or tier not in CONTACT_TIERS:
        return True
    policy = _load_policy()
    allowed = policy.get(tier)
    if allowed is None:
        return True
    return tool_name in allowed


def get_allowed_tools(tier: str) -> list[str] | None:
    """List of allowed tools, or None for full access."""
    policy = _load_policy()
    return policy.get(tier)


def save_policy(policy: dict) -> None:
    """Persist policy to JSON. Call from API or tools."""
    ACCESS_POLICY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(ACCESS_POLICY_PATH, "w", encoding="utf-8") as f:
        json.dump(policy, f, indent=2)
