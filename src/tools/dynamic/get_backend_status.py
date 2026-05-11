"""Dynamic tool: get_backend_status."""
from __future__ import annotations

import json

from src.backend_switching import get_backend_status

TOOL_DEF = {
    "name": "get_backend_status",
    "description": (
        "Return active backend/provider/model, fallback state, health markers, "
        "and integrated cost snapshot."
    ),
    "parameters": {"type": "object", "properties": {}},
}


async def run() -> str:
    return json.dumps(get_backend_status(user_id="default"), ensure_ascii=False, indent=2)

