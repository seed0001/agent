"""Dynamic tool: switch_backend_provider."""
from __future__ import annotations

import json

from src.backend_switching import switch_backend_provider

TOOL_DEF = {
    "name": "switch_backend_provider",
    "description": (
        "Switch Andrew's backend provider/model using a target string (e.g. "
        "'grok', 'gpt-5.5', 'cheap fallback', 'local ollama'). "
        "Uses health checks and tool-capable fallback safety."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "target": {"type": "string", "description": "Target backend/provider/model alias."},
            "reason": {"type": "string", "description": "Reason for switch request."},
            "dry_run": {"type": "boolean", "description": "Validate and simulate without changing active backend."},
            "force": {"type": "boolean", "description": "Force attempt despite warnings."},
        },
        "required": ["target"],
    },
}


async def run(
    target: str,
    reason: str = "creator_requested",
    dry_run: bool = False,
    force: bool = False,
) -> str:
    result = await switch_backend_provider(
        target=target,
        reason=reason,
        dry_run=bool(dry_run),
        force=bool(force),
        requested_by="creator",
        user_id="default",
    )
    return json.dumps(result, ensure_ascii=False, indent=2)

