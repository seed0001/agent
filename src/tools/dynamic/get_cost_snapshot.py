"""Dynamic tool: get_cost_snapshot."""
from __future__ import annotations

import json

from src.cost_tracking import get_cost_snapshot

TOOL_DEF = {
    "name": "get_cost_snapshot",
    "description": (
        "Return token/cost snapshot for today/week/month/all including paid spend, "
        "free/local tokens, provider/model breakdown, and budget warnings."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "period": {
                "type": "string",
                "enum": ["today", "week", "month", "all"],
                "description": "Time window for the snapshot.",
            },
            "group_by": {
                "type": "string",
                "enum": ["provider", "model", "source_type", "project", "tool"],
                "description": "Preferred grouping in the summary.",
            },
            "include_free": {
                "type": "boolean",
                "description": "Include free/local token usage in the snapshot.",
            },
        },
        "required": [],
    },
}


async def run(
    period: str = "today",
    group_by: str = "provider",
    include_free: bool = True,
) -> str:
    snap = get_cost_snapshot(
        period=period,
        group_by=group_by,
        include_free=bool(include_free),
        user_id="default",
    )
    return json.dumps(snap, ensure_ascii=False, indent=2)

