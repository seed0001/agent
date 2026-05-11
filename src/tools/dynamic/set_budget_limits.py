"""Dynamic tool: set_budget_limits."""
from __future__ import annotations

import json

from src.cost_tracking import get_budget_settings, set_budget_limits

TOOL_DEF = {
    "name": "set_budget_limits",
    "description": (
        "Configure daily/weekly/monthly budget limits and warning thresholds "
        "for API spending."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "daily_limit": {"type": "number"},
            "weekly_limit": {"type": "number"},
            "monthly_limit": {"type": "number"},
            "warning_threshold_percent": {"type": "number"},
            "hard_stop_threshold_percent": {"type": "number"},
            "require_confirmation_over_amount": {"type": "number"},
            "currency": {"type": "string"},
        },
        "required": [],
    },
}


async def run(
    daily_limit: float | None = None,
    weekly_limit: float | None = None,
    monthly_limit: float | None = None,
    warning_threshold_percent: float | None = None,
    hard_stop_threshold_percent: float | None = None,
    require_confirmation_over_amount: float | None = None,
    currency: str | None = None,
) -> str:
    msg = set_budget_limits(
        daily_limit=daily_limit,
        weekly_limit=weekly_limit,
        monthly_limit=monthly_limit,
        warning_threshold_percent=warning_threshold_percent,
        hard_stop_threshold_percent=hard_stop_threshold_percent,
        require_confirmation_over_amount=require_confirmation_over_amount,
        currency=currency,
        user_id="default",
    )
    payload = {"message": msg, "budget": get_budget_settings(user_id="default")}
    return json.dumps(payload, ensure_ascii=False, indent=2)

