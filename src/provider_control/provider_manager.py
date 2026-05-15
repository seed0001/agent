#!/usr/bin/env python3
"""
Provider Manager Facade.

Centralizes provider access so background scripts and interpreters do not create
silent direct OpenAI/xAI/Mistral clients.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
import json

from src.provider_control.provider_policy import ProviderPolicy


ProviderCallable = Callable[[str, dict[str, Any]], str]


@dataclass
class ProviderResult:
    success: bool
    provider: str
    output: str | None = None
    error: str | None = None
    fallback_used: bool = False
    attempted: list[str] = field(default_factory=list)


class ProviderManager:
    """Small provider facade with fallback and logging."""

    def __init__(self, policy: ProviderPolicy | None = None, log_path: str = "logs/provider_activity.jsonl"):
        self.policy = policy or ProviderPolicy()
        self.backends: dict[str, ProviderCallable] = {}
        self.log_path = Path(log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def register(self, name: str, handler: ProviderCallable) -> None:
        self.backends[name] = handler

    def call(self, prompt: str, preferred: str | None = None, metadata: dict[str, Any] | None = None) -> ProviderResult:
        metadata = metadata or {}
        attempted: list[str] = []
        errors: list[str] = []
        first = True

        for provider in self.policy.ordered_candidates(preferred):
            attempted.append(provider)
            handler = self.backends.get(provider)
            if handler is None:
                errors.append(f"{provider}: not registered")
                continue

            try:
                output = handler(prompt, metadata)
                result = ProviderResult(
                    success=True,
                    provider=provider,
                    output=output,
                    fallback_used=not first,
                    attempted=attempted,
                )
                self._log("provider_call_success", result, metadata)
                return result
            except Exception as exc:  # noqa: BLE001 - manager must catch provider failures
                errors.append(f"{provider}: {exc}")
                first = False
                continue

        result = ProviderResult(
            success=False,
            provider=attempted[-1] if attempted else "none",
            error="; ".join(errors),
            fallback_used=len(attempted) > 1,
            attempted=attempted,
        )
        self._log("provider_call_failure", result, metadata)
        return result

    def _log(self, event: str, result: ProviderResult, metadata: dict[str, Any]) -> None:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "provider": result.provider,
            "success": result.success,
            "fallback_used": result.fallback_used,
            "attempted": result.attempted,
            "error": result.error,
            "metadata": metadata,
        }
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
