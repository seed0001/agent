"""Lightweight automatic promotion of important user facts.

This is intentionally conservative. It catches facts Andrew repeatedly misses
without trying to replace the LLM's normal ``update_profile`` judgment.
"""
from __future__ import annotations

import re


def _clean_user_text(text: str) -> str:
    value = text or ""
    value = re.sub(r"^User:\s*", "", value, flags=re.I).strip()
    value = re.sub(r"^\[[^\]]+\]\s*", "", value).strip()
    value = re.sub(r"^Message from .*? who just said:\s*", "", value, flags=re.I).strip()
    return value


def extract_profile_facts(user_input: str) -> list[tuple[str, str]]:
    """Return ``[(category, fact)]`` to store as protected profile memory."""
    text = _clean_user_text(user_input)
    low = text.lower()
    facts: list[tuple[str, str]] = []

    if re.search(r"\bi take (my )?med(ication|s)?\b", low) and "morning" in low:
        facts.append(("personal", "Travis takes medication in the morning"))
    elif re.search(r"\bi take (my )?med(ication|s)?\b", low):
        facts.append(("personal", "Travis takes medication"))

    if "blood pressure" in low:
        facts.append(("personal", "Travis wants blood pressure included in medical check-ins"))
    if "mental health" in low:
        facts.append(("personal", "Travis wants mental health questions included in medical check-ins"))
    if "finance tracking" in low or "track" in low and "finance" in low:
        facts.append(("work", "Travis wants finance tracking added to Andrew's project work"))
    if "prediction engine" in low:
        facts.append(("work", "Travis wants a prediction engine for routines and daily patterns"))
    if "routine" in low and ("predict" in low or "tracking" in low or "track" in low):
        facts.append(("work", "Travis wants routine tracking for prediction and planning"))

    # Keep exact phrasing for pet facts if the user shares new details.
    if "chance" in low and "water" in low:
        facts.append(("personal", "Chance needs fresh water as part of Travis's routine"))
    if "chance" in low and ("chain" in low or "outside" in low or "let chance out" in low):
        facts.append(("personal", "Chance needs outside/chain time as part of Travis's routine"))

    deduped: list[tuple[str, str]] = []
    seen = set()
    for item in facts:
        key = (item[0], item[1].lower())
        if key not in seen:
            seen.add(key)
            deduped.append(item)
    return deduped


def promote_user_input(memory, user_input: str) -> list[str]:
    """Store extracted facts through ``MemoryStore.add_profile_fact``."""
    stored: list[str] = []
    for category, fact in extract_profile_facts(user_input):
        result = memory.add_profile_fact(category, fact)
        stored.append(result)
    return stored
