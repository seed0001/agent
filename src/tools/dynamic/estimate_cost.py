"""Dynamic tool: estimate_cost."""
from __future__ import annotations

import json

from src.cost_tracking import estimate_cost, estimate_task_cost

TOOL_DEF = {
    "name": "estimate_cost",
    "description": (
        "Estimate API cost from token counts. Can also estimate multi-step task cost "
        "using task_type + steps."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "provider": {"type": "string"},
            "model": {"type": "string"},
            "input_tokens": {"type": "integer"},
            "output_tokens": {"type": "integer"},
            "cached_input_tokens": {"type": "integer"},
            "reasoning_tokens": {"type": "integer"},
            "task_type": {"type": "string"},
            "steps": {"type": "integer"},
        },
        "required": ["provider", "model", "input_tokens", "output_tokens"],
    },
}


async def run(
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cached_input_tokens: int = 0,
    reasoning_tokens: int = 0,
    task_type: str = "",
    steps: int = 1,
) -> str:
    if task_type:
        out = estimate_task_cost(
            task_type=task_type,
            expected_input_tokens=input_tokens,
            expected_output_tokens=output_tokens,
            provider=provider,
            model=model,
            steps=steps,
            user_id="default",
        )
    else:
        out = estimate_cost(
            provider=provider,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=cached_input_tokens,
            reasoning_tokens=reasoning_tokens,
            user_id="default",
        )
    return json.dumps(out, ensure_ascii=False, indent=2)

