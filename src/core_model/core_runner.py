#!/usr/bin/env python3
"""
Core Runner for Andrew Hybrid Core Model.

This wires the mock core to tool execution, interpreter prompt generation,
and automatic training data logging.
"""

from src.core_model.schemas import CoreInput, BackendStatus, CostState
from src.core_model.mock_core import mock_andrew_core
from src.core_model.interpreter_prompt import build_interpreter_prompt
from src.core_model.training_logger import training_logger


def execute_tool(tool_name: str, args: dict) -> str:
    """Placeholder tool executor."""
    if tool_name == "switch_backend_provider":
        target = args.get("target", "xai")
        return f"Switched to {target}. Tool calling is available."
    elif tool_name == "update_profile":
        return "Profile updated."
    else:
        return f"Executed tool: {tool_name}"


def run_andrew_turn(user_message: str, current_backend: str = "openai/gpt-5.5") -> str:
    """
    Full turn execution using the mock core + automatic logging.
    """
    core_input = CoreInput(
        user_message=user_message,
        recent_context="",
        available_tools=["switch_backend_provider", "update_profile"],
        backend_status=BackendStatus(
            active_backend=current_backend,
            tool_capable=True,
            cost_tier="high" if "gpt-5" in current_backend else "medium"
        ),
        cost_state=CostState(today_paid_usd=0.0)
    )

    core_output = mock_andrew_core(core_input)

    # Execute tool if core decided one
    tool_result = None
    if core_output.tool_intent.needed and core_output.tool_intent.tool:
        tool_result = execute_tool(
            core_output.tool_intent.tool,
            core_output.tool_intent.args
        )

    # Log the turn for future training
    training_logger.log_turn(
        user_message=user_message,
        core_input=core_input,
        core_output=core_output,
        tool_result=tool_result
    )

    # Build interpreter prompt
    prompt = build_interpreter_prompt(core_output, user_message, tool_result)

    return prompt


if __name__ == "__main__":
    test_messages = [
        "Switch your provider to xAI",
        "My dog's name is Chance.",
        "This is costing me money."
    ]

    for msg in test_messages:
        print("=" * 80)
        print(f"USER: {msg}")
        print("-" * 80)
        result = run_andrew_turn(msg)
        print(result)
        print()