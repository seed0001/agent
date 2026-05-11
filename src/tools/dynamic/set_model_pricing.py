"""Dynamic tool: set_model_pricing."""
from __future__ import annotations

from src.cost_tracking import set_model_pricing

TOOL_DEF = {
    "name": "set_model_pricing",
    "description": (
        "Create or update pricing for a provider/model pair. "
        "Supports per-token or per-million pricing and local/free models."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "provider": {"type": "string"},
            "model": {"type": "string"},
            "input_per_million": {"type": "number"},
            "output_per_million": {"type": "number"},
            "input_per_token": {"type": "number"},
            "output_per_token": {"type": "number"},
            "cached_input_per_million": {"type": "number"},
            "reasoning_per_million": {"type": "number"},
            "local": {"type": "boolean"},
            "currency": {"type": "string"},
            "notes": {"type": "string"},
            "effective_date": {"type": "string"},
            "active": {"type": "boolean"},
        },
        "required": ["provider", "model"],
    },
}


async def run(
    provider: str,
    model: str,
    input_per_million: float | None = None,
    output_per_million: float | None = None,
    input_per_token: float | None = None,
    output_per_token: float | None = None,
    cached_input_per_million: float | None = None,
    reasoning_per_million: float | None = None,
    local: bool = False,
    currency: str = "USD",
    notes: str = "",
    effective_date: str = "",
    active: bool = True,
) -> str:
    return set_model_pricing(
        provider=provider,
        model=model,
        input_per_million=input_per_million,
        output_per_million=output_per_million,
        input_per_token=input_per_token,
        output_per_token=output_per_token,
        cached_input_per_million=cached_input_per_million,
        reasoning_per_million=reasoning_per_million,
        local=bool(local),
        currency=currency,
        notes=notes,
        effective_date=effective_date,
        active=bool(active),
        user_id="default",
    )

