#!/usr/bin/env python3
"""
Core schemas for Andrew Hybrid Core Model.

Defines the strict input and output contracts between:
- Context Builder → Local Core Model
- Local Core Model → Policy/Tool Router & Interpreter
"""

from typing import Any, Literal, Optional
from pydantic import BaseModel, Field


# =============================================================================
# CORE INPUT SCHEMA
# =============================================================================

class BackendStatus(BaseModel):
    active_backend: str
    tool_capable: bool
    cost_tier: Literal["free", "low", "medium", "high", "unknown"]


class CostState(BaseModel):
    today_paid_usd: float = 0.0
    budget_note: Optional[str] = None


class CoreInput(BaseModel):
    """Input to the local Andrew core model."""
    user_message: str
    recent_context: str = ""
    active_task: Optional[str] = None
    memories: list[str] = Field(default_factory=list)
    values: list[str] = Field(default_factory=list)
    available_tools: list[str] = Field(default_factory=list)
    backend_status: BackendStatus
    cost_state: CostState = Field(default_factory=CostState)
    system_state: dict[str, Any] = Field(default_factory=dict)


# =============================================================================
# CORE OUTPUT SCHEMA
# =============================================================================

class ToolIntent(BaseModel):
    needed: bool = False
    tool: Optional[str] = None
    args: dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)


class MemoryIntent(BaseModel):
    should_store: bool = False
    target: Literal["profile", "contact", "schedule", "artifact", "values", "none"] = "none"
    fact: Optional[str] = None


class ProviderIntent(BaseModel):
    switch_needed: bool = False
    preferred_provider: Optional[str] = None
    reason: Optional[str] = None


class CoreOutput(BaseModel):
    """Structured output from the local Andrew core model."""
    feeling: str
    emotional_intensity: Literal["low", "medium", "high"] = "medium"
    desire: str
    values_active: list[str] = Field(default_factory=list)
    beliefs_about_situation: list[str] = Field(default_factory=list)
    action_direction: str
    tool_intent: ToolIntent = Field(default_factory=ToolIntent)
    memory_intent: MemoryIntent = Field(default_factory=MemoryIntent)
    provider_intent: ProviderIntent = Field(default_factory=ProviderIntent)
    learning_need: Optional[str] = None
    response_tone: str = "direct"
    response_constraints: list[str] = Field(default_factory=list)


# =============================================================================
# VALIDATION HELPERS
# =============================================================================

def validate_core_output(data: dict) -> CoreOutput:
    """Validate and return a CoreOutput instance."""
    return CoreOutput(**data)


def validate_core_input(data: dict) -> CoreInput:
    """Validate and return a CoreInput instance."""
    return CoreInput(**data)