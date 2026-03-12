"""Doctor Mode: built-in self-healing when something breaks."""
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Awaitable

type FixStrategy = Callable[..., Awaitable[Any]]


class FailureKind(Enum):
    API_ERROR = "api_error"
    TOOL_ERROR = "tool_error"
    CONNECTION = "connection"
    PERMISSION = "permission"
    NOT_FOUND = "not_found"
    TIMEOUT = "timeout"
    UNKNOWN = "unknown"


@dataclass
class FailureEvent:
    """Record of a failure for Doctor Mode."""
    kind: FailureKind
    message: str
    context: dict[str, Any] = field(default_factory=dict)
    attempted_strategies: list[str] = field(default_factory=list)


class DoctorMode:
    """
    Core self-healing. When something breaks:
    1. Diagnose the failure
    2. Generate alternative fix strategies
    3. Try them until one works or we escalate
    """

    MAX_ATTEMPTS = 5

    def __init__(self):
        self.current_failure: FailureEvent | None = None
        self.retry_count = 0

    def diagnose(self, error: Exception | str) -> FailureKind:
        """Classify the failure type. Accepts Exception or raw error string (e.g. from tool results)."""
        msg = (error if isinstance(error, str) else str(error)).lower()
        if "error:" in msg or "tool" in msg:
            return FailureKind.TOOL_ERROR
        if "api" in msg or "401" in msg or "403" in msg:
            return FailureKind.API_ERROR
        if "connection" in msg or "refused" in msg or "network" in msg:
            return FailureKind.CONNECTION
        if "permission" in msg or "access denied" in msg:
            return FailureKind.PERMISSION
        if "not found" in msg or "404" in msg or "enoent" in msg:
            return FailureKind.NOT_FOUND
        if "timeout" in msg or "timed out" in msg:
            return FailureKind.TIMEOUT
        return FailureKind.UNKNOWN

    def generate_strategies(self, failure: FailureEvent) -> list[str]:
        """Return alternative approaches (names) to try."""
        strategies = []
        kind = failure.kind

        if kind == FailureKind.TOOL_ERROR:
            strategies = ["try_different_path", "try_alternative_tool", "check_permissions", "create_if_missing"]
        elif kind == FailureKind.API_ERROR:
            strategies = ["retry_with_backoff", "check_api_key", "try_fallback_endpoint"]
        elif kind == FailureKind.CONNECTION:
            strategies = ["retry_connection", "check_network", "degrade_gracefully"]
        elif kind == FailureKind.PERMISSION:
            strategies = ["try_alternative_path", "escalate_to_user"]
        elif kind == FailureKind.NOT_FOUND:
            strategies = ["create_if_missing", "try_alternative_path", "ask_user"]
        elif kind == FailureKind.TIMEOUT:
            strategies = ["retry_with_longer_timeout", "chunk_request", "degrade_gracefully"]
        else:
            strategies = ["retry_once", "try_different_approach", "escalate_to_user"]

        # Filter out already attempted
        return [s for s in strategies if s not in failure.attempted_strategies]

    def suggest_for_tool_error(self, tool_name: str, error_result: str) -> str:
        """When a tool returns an error, return enriched message with alternative suggestions."""
        failure = FailureEvent(
            kind=self.diagnose(error_result),
            message=error_result[:500],
            context={"tool": tool_name},
        )
        strategies = self.generate_strategies(failure)
        suggestions = ", ".join(strategies[:3]) if strategies else "try a different approach"
        return (
            f"{error_result}\n\n"
            f"[Doctor Mode] This didn't work. Suggested alternatives: {suggestions}. "
            f"Consider trying a different tool, path, or method."
        )

    def user_facing_message(self, failure: FailureEvent, in_progress: bool = True) -> str:
        """Simple, calm message for the user."""
        if in_progress:
            return "Something's not working the way it should. I'm trying a few different ways to fix it."
        return (
            "I tried a few approaches but couldn't fully fix this. "
            "Here's what you can do next: check your connection and try again, "
            "or let me know if you'd like to try something else."
        )
