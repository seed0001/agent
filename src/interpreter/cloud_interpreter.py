#!/usr/bin/env python3
"""Cloud interpreter wrapper for Andrew core output."""

from __future__ import annotations

from src.core_model.core_veto import CoreVetoGate, VetoDecision
from src.core_model.interpreter_prompt import build_interpreter_prompt
from src.core_model.schemas import CoreOutput
from src.provider_control.provider_manager import ProviderManager, ProviderResult


class CloudInterpreter:
    def __init__(self, provider_manager: ProviderManager, veto_gate: CoreVetoGate | None = None):
        self.provider_manager = provider_manager
        self.veto_gate = veto_gate or CoreVetoGate()

    def interpret(
        self,
        core_output: CoreOutput,
        user_message: str,
        tool_result: str | None = None,
        preferred_provider: str | None = None,
    ) -> tuple[ProviderResult, VetoDecision]:
        prompt = build_interpreter_prompt(core_output, user_message, tool_result)
        provider = preferred_provider or core_output.provider_intent.preferred_provider or None
        veto = self.veto_gate.evaluate_provider_request(
            core_output=core_output,
            provider=provider or "policy-default",
            prompt=prompt,
            metadata={"user_message": user_message},
        )
        if not veto.allowed:
            return ProviderResult(False, provider or "blocked", error=veto.reason), veto

        result = self.provider_manager.call(
            prompt,
            preferred=provider,
            metadata={"source": "cloud_interpreter", "core_feeling": core_output.feeling},
        )
        return result, veto
