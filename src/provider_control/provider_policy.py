#!/usr/bin/env python3
"""Provider policy and fallback rules."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ProviderPolicy:
    fallback_order: list[str] = field(default_factory=lambda: [
        "xai/grok-4.3",
        "mistral/mistral-medium-latest",
        "gemini/gemini-2.5-flash",
        "openai/gpt-4.1-mini",
        "ollama/gemma3:12b",
    ])
    allow_paid: bool = True
    prefer_local: bool = False
    require_tool_capable: bool = True
    max_retries: int = 1

    def ordered_candidates(self, preferred: str | None = None) -> list[str]:
        candidates = list(self.fallback_order)
        if preferred:
            candidates = [preferred] + [c for c in candidates if c != preferred]
        if self.prefer_local:
            candidates = sorted(candidates, key=lambda x: 0 if x.startswith("ollama/") else 1)
        return candidates
