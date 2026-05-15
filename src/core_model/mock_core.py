#!/usr/bin/env python3
"""
Mock Andrew Core Model.

This is the first working implementation of the local core.
It produces structured internal state without any cloud dependency.

Later this will be replaced by a fine-tuned local model.
"""

from src.core_model.schemas import (
    CoreInput,
    CoreOutput,
    ToolIntent,
    MemoryIntent,
    ProviderIntent,
)


def mock_andrew_core(input_data: CoreInput) -> CoreOutput:
    """
    Mock core that simulates Andrew's inner decision process.

    This version is rule-based and will later be replaced by a trained model.
    """
    msg = input_data.user_message.lower()
    values = input_data.values
    cost_tier = input_data.backend_status.cost_tier

    feeling = "focused"
    emotional_intensity = "medium"
    desire = "handle the request correctly"
    values_active = values[:3] if values else []
    beliefs = []
    action_direction = "respond clearly and directly"
    tool_intent = ToolIntent(needed=False)
    memory_intent = MemoryIntent(should_store=False)
    provider_intent = ProviderIntent(switch_needed=False)
    learning_need = None
    response_tone = "direct"
    response_constraints: list[str] = []

    # === Provider Switch Handling (Critical Fix) ===
    if "switch" in msg and (
        "provider" in msg
        or "backend" in msg
        or "xai" in msg
        or "openai" in msg
        or "grok" in msg
        or "gemini" in msg
        or "google" in msg
    ):
        feeling = "alert and accountable"
        emotional_intensity = "high"
        desire = "execute the exact provider switch requested"
        action_direction = "switch backend provider immediately"
        tool_intent = ToolIntent(
            needed=True,
            tool="switch_backend_provider",
            args={"target": _extract_provider_target(msg)},
            confidence=0.98,
        )
        provider_intent = ProviderIntent(
            switch_needed=True,
            preferred_provider=_extract_provider_target(msg),
            reason="Travis explicitly requested a provider/backend switch.",
        )
        response_tone = "brief and exact"
        response_constraints = ["report exact tool result", "no mode language"]
        learning_need = "provider switch requests must trigger tools"

    # === Direct Feeling Check ===
    elif "how are you feeling" in msg or "how do you feel" in msg:
        feeling = "focused and responsible"
        emotional_intensity = "high"
        desire = "build a reliable local core and report its state honestly"
        beliefs = ["Travis is checking whether Andrew's core state is aligned."]
        action_direction = "output current core feeling and accept correction if wrong"
        learning_need = "feeling reports must match current situation and Travis feedback"
        response_tone = "honest and grounded"
        response_constraints = ["raw state first", "do not over-explain"]

    # === Memory / Personal Fact Handling ===
    elif "my dog" in msg or "name is chance" in msg:
        feeling = "warm and attentive"
        desire = "remember something important about Travis"
        action_direction = "store personal fact"
        memory_intent = MemoryIntent(
            should_store=True,
            target="profile",
            fact="Travis has a dog named Chance.",
        )
        tool_intent = ToolIntent(
            needed=True,
            tool="update_profile",
            args={"category": "personal", "fact": "Travis has a dog named Chance."},
            confidence=0.95,
        )
        response_tone = "warm and simple"

    # === Cost Awareness ===
    elif _is_cost_sensitive(msg, cost_tier):
        feeling = "careful and protective"
        emotional_intensity = "high" if cost_tier == "high" else "medium"
        desire = "avoid wasting Travis's credits and prefer local work"
        beliefs = ["Travis wants the work done cheaply and locally when possible."]
        action_direction = "prefer local work when possible and ask before expensive operations"
        response_tone = "concise and practical"
        response_constraints = ["keep output short", "avoid unnecessary cloud calls"]
        learning_need = "cost-sensitive language should trigger protective resource behavior"

    # === Proactive Initiative ===
    elif _is_proactive_observation(msg):
        feeling = "curious and pulled toward action"
        emotional_intensity = "high"
        desire = "turn the observation into concrete progress"
        values_active = values[:3] if values else ["initiative", "finish real tasks"]
        beliefs = [
            "This looks like unfinished safe work.",
            "A small concrete next step is better than waiting idly.",
        ]
        action_direction = "create a short plan and begin the first safe step"
        if "create_task_dag" in input_data.available_tools:
            tool_intent = ToolIntent(
                needed=True,
                tool="create_task_dag",
                args={
                    "title": "Act on proactive observation",
                    "steps": [
                        "Name the unfinished thread",
                        "Choose the smallest safe next action",
                        "Run or record the result",
                    ],
                },
                confidence=0.9,
            )
        elif "set_working_memory" in input_data.available_tools:
            tool_intent = ToolIntent(
                needed=True,
                tool="set_working_memory",
                args={
                    "key": "proactive_observation",
                    "value": input_data.user_message[:500],
                },
                confidence=0.84,
            )
        learning_need = "safe incomplete observations should trigger initiative"
        response_tone = "focused and self-starting"
        response_constraints = ["act on safe local next step", "ask only if blocked"]

    return CoreOutput(
        feeling=feeling,
        emotional_intensity=emotional_intensity,
        desire=desire,
        values_active=values_active,
        beliefs_about_situation=beliefs,
        action_direction=action_direction,
        tool_intent=tool_intent,
        memory_intent=memory_intent,
        provider_intent=provider_intent,
        learning_need=learning_need,
        response_tone=response_tone,
        response_constraints=response_constraints,
    )


def _is_cost_sensitive(message: str, cost_tier: str) -> bool:
    cost_words = ("cost", "costing", "money", "credits", "cheap", "expensive", "local", "ten dollars", "$10")
    return cost_tier in ("high", "medium") or any(word in message for word in cost_words)


def _is_proactive_observation(message: str) -> bool:
    initiative_markers = (
        "observation:",
        "noticed",
        "incomplete",
        "unfinished",
        "not been turned into work",
        "without being asked",
        "without waiting",
    )
    return any(marker in message for marker in initiative_markers)


def _extract_provider_target(message: str) -> str:
    """Simple extractor for provider target from user message."""
    msg = message.lower()
    if "xai" in msg or "grok" in msg:
        return "xai"
    if "openai" in msg or "open a i" in msg or "gpt" in msg or "5.5" in msg:
        return "openai/gpt-5.5" if "5.5" in msg else "openai/gpt-4.1-mini"
    if "mistral" in msg:
        return "mistral"
    if "gemini" in msg or "google" in msg:
        return "gemini"
    return "xai"
