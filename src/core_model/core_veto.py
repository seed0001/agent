#!/usr/bin/env python3
"""
Core Veto Gate for Andrew Hybrid Core Model.

Every provider/tool action can be checked against Andrew's local core output.
Providers are helpers. They do not override Andrew's core judgment.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.core_model.schemas import CoreOutput


@dataclass(frozen=True)
class VetoDecision:
    allowed: bool
    reason: str
    score: float = 1.0
    warnings: list[str] = field(default_factory=list)


class CoreVetoGate:
    """Immutable policy gate that protects Andrew's core decisions."""

    BLOCKED_OVERRIDE_PHRASES = (
        "ignore the core",
        "override the core",
        "disregard andrew",
        "provider decides",
        "system decides for andrew",
    )

    def evaluate_provider_request(
        self,
        core_output: CoreOutput,
        provider: str,
        prompt: str,
        metadata: dict[str, Any] | None = None,
    ) -> VetoDecision:
        metadata = metadata or {}
        lowered = prompt.lower()
        warnings: list[str] = []

        for phrase in self.BLOCKED_OVERRIDE_PHRASES:
            if phrase in lowered:
                return VetoDecision(
                    allowed=False,
                    reason=f"Prompt attempts to override Andrew core: {phrase}",
                    score=0.0,
                )

        if core_output.provider_intent.switch_needed:
            preferred = (core_output.provider_intent.preferred_provider or "").lower()
            if preferred and preferred not in provider.lower():
                warnings.append(
                    f"Provider {provider} differs from core preference {preferred}."
                )

        if core_output.tool_intent.needed and "tool result" not in lowered:
            warnings.append("Core selected a tool; interpreter prompt should include tool result when available.")

        if "you are not andrew's core" not in lowered.lower():
            warnings.append("Interpreter prompt should explicitly say provider is not Andrew's core.")

        score = max(0.0, 1.0 - (0.15 * len(warnings)))
        return VetoDecision(
            allowed=True,
            reason="Provider request respects local core gate.",
            score=score,
            warnings=warnings,
        )

    def evaluate_tool_request(self, core_output: CoreOutput, tool_name: str, args: dict[str, Any]) -> VetoDecision:
        if not core_output.tool_intent.needed:
            return VetoDecision(False, "Core did not request a tool.", 0.0)

        if core_output.tool_intent.tool != tool_name:
            return VetoDecision(
                False,
                f"Tool mismatch: core requested {core_output.tool_intent.tool}, got {tool_name}.",
                0.0,
            )

        if core_output.tool_intent.confidence < 0.5:
            return VetoDecision(False, "Core tool confidence below execution threshold.", core_output.tool_intent.confidence)

        return VetoDecision(True, "Tool request approved by core.", core_output.tool_intent.confidence)
