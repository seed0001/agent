"""Persistent decision layer for intent, tool routing, and rejection learning."""
from __future__ import annotations

import json
import os
import re
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from config.settings import DATA_DIR


DECISION_DIR = DATA_DIR / "decision_layer"
DEFAULT_REJECTION_PATH = DECISION_DIR / "rejection_log.json"
DEFAULT_STATE_PATH = DECISION_DIR / "decision_state.json"


def _now() -> str:
    return datetime.now().isoformat()


def _read_json(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default
    return default


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp_path, path)


def _compact_text(text: str, limit: int = 500) -> str:
    text = " ".join(str(text or "").split())
    return text[:limit]


class RejectionDatabase:
    """Tracks rejected actions so the predictor avoids repeating bad paths."""

    def __init__(self, path: str | Path = DEFAULT_REJECTION_PATH):
        self.path = Path(path)
        self.rejections: list[dict[str, Any]] = []
        self.load()

    def load(self) -> None:
        data = _read_json(self.path, [])
        self.rejections = data if isinstance(data, list) else []

    def save(self) -> None:
        _write_json(self.path, self.rejections)

    def add_rejection(self, text: str, reason: str = "bad action", details: str = "") -> None:
        key = self._key(text)
        if not key:
            return
        for item in self.rejections:
            if isinstance(item, dict) and item.get("text") == key:
                item["reason"] = reason or item.get("reason", "")
                item["details"] = _compact_text(details)
                item["timestamp"] = _now()
                item["count"] = int(item.get("count", 1)) + 1
                self.save()
                return
        self.rejections.append(
            {
                "text": key,
                "reason": reason,
                "details": _compact_text(details),
                "timestamp": _now(),
                "count": 1,
            }
        )
        self.save()

    def clear(self) -> None:
        self.rejections = []
        self.save()

    def is_rejected(self, text: str, *, threshold: float = 0.92) -> bool:
        key = self._key(text)
        if not key:
            return False
        for item in self.rejections:
            if not isinstance(item, dict):
                continue
            rejected = str(item.get("text", ""))
            if rejected == key:
                return True
            if SequenceMatcher(None, rejected, key).ratio() >= threshold:
                return True
        return False

    def get_stats(self) -> dict[str, Any]:
        return {
            "total": len(self.rejections),
            "path": str(self.path),
            "last_updated": _now(),
        }

    @staticmethod
    def _key(text: str) -> str:
        return " ".join(str(text or "").strip().lower().split())


class IntentToolExtractor:
    """Extracts intent, tool names, action hints, and simple targets."""

    TOOL_KEYWORDS: dict[str, list[str]] = {
        "read_file": ["read", "open", "show", "load", "inspect", "view"],
        "write_file": ["write", "save", "create", "update", "modify", "edit", "fix"],
        "verify_file_exists": ["verify", "check file", "confirm file", "exists"],
        "list_dir": ["list", "show files", "directory", "folder", "dir"],
        "run_command": ["run", "execute", "launch", "start", "shell", "command"],
        "search_web": ["search", "look up", "find online", "web"],
        "spawn_subagent": ["spawn", "background", "subagent", "sub-agent"],
        "run_build": ["build", "test", "compile"],
        "search_memory": ["remember", "memory", "recall"],
    }

    INTENT_KEYWORDS: dict[str, list[str]] = {
        "stop": ["stop", "kill", "cancel", "halt"],
        "action": ["do", "make", "create", "fix", "modify", "implement", "wire", "integrate"],
        "query": ["what", "how", "why", "tell", "explain", "show me"],
    }

    _PATH_PATTERN = re.compile(
        r"(?:^|[\s`'\"])(?P<path>(?:[A-Za-z]:\\|/|\.{1,2}[\\/])(?:[^\s`'\"<>|]+[\\/]?)+)"
    )
    _REL_PATH_PATTERN = re.compile(
        r"(?:^|[\s`'\"])(?P<path>[\w.'-]+[\\/][\w.'/\\-]+\.[A-Za-z0-9]{1,8}\b)"
    )
    _COMMAND_PATTERN = re.compile(
        r"(?:run|execute|command)\s*:?\s*(?P<cmd>.+)$",
        re.IGNORECASE | re.DOTALL,
    )

    def extract(self, message: str) -> dict[str, Any]:
        raw_message = str(message or "")
        msg_lower = raw_message.lower()
        intent = "general"
        for candidate, keywords in self.INTENT_KEYWORDS.items():
            if any(word in msg_lower for word in keywords):
                intent = candidate
                break

        tools: list[str] = []
        for tool, keywords in self.TOOL_KEYWORDS.items():
            if any(keyword in msg_lower for keyword in keywords):
                tools.append(tool)

        action_hints: list[str] = []
        if any(word in msg_lower for word in ("file", "script", ".py", ".md", ".txt", ".json")):
            action_hints.append("file_operation")
        if any(word in msg_lower for word in ("train", "fine tune", "fine-tune", "dataset")):
            action_hints.append("training")
        if any(word in msg_lower for word in ("test", "pytest", "build", "compile")):
            action_hints.append("verification")

        targets = self._extract_targets(raw_message)
        return {
            "intent": intent,
            "tools": sorted(set(tools)),
            "action_hints": sorted(set(action_hints)),
            "targets": targets,
            "raw_message": raw_message,
        }

    def _extract_targets(self, message: str) -> dict[str, Any]:
        paths = [match.group("path").rstrip(".,);]") for match in self._PATH_PATTERN.finditer(message)]
        paths.extend(
            match.group("path").rstrip(".,);]")
            for match in self._REL_PATH_PATTERN.finditer(message)
        )
        command_match = self._COMMAND_PATTERN.search(message.strip())
        targets: dict[str, Any] = {}
        if paths:
            targets["paths"] = sorted(set(paths))
        if command_match:
            targets["command"] = command_match.group("cmd").strip()
        return targets


class ActionPredictor:
    """Scores next actions/tool combos using extraction state and rejection avoidance."""

    def __init__(
        self,
        rejection_db: RejectionDatabase,
        state_path: str | Path = DEFAULT_STATE_PATH,
    ):
        self.rejection_db = rejection_db
        self.state_path = Path(state_path)
        self.history: list[dict[str, Any]] = []
        self.load_state()

    def load_state(self) -> None:
        data = _read_json(self.state_path, [])
        self.history = data if isinstance(data, list) else []

    def save_state(self) -> None:
        _write_json(self.state_path, self.history[-1000:])

    def predict(self, extraction: dict[str, Any]) -> dict[str, Any]:
        candidates = self._build_candidates(extraction)
        candidates = [
            candidate
            for candidate in candidates
            if not self.rejection_db.is_rejected(candidate["action"])
        ]
        if not candidates:
            fallback = {"action": "respond_direct", "tool": None, "score": 0.5, "reason": "no safe tool candidate"}
            if self.rejection_db.is_rejected(fallback["action"]):
                fallback = {"action": "no_safe_action", "tool": None, "score": 0.0, "reason": "all candidates rejected"}
            self._record("prediction", extraction, fallback)
            return self._with_confidence(fallback)

        best = max(candidates, key=lambda item: item["score"])
        prediction = self._with_confidence(best)
        self._record("prediction", extraction, prediction)
        return prediction

    def record_execution_result(
        self,
        prediction: dict[str, Any] | None,
        *,
        tool: str,
        success: bool,
        result: str,
    ) -> None:
        if not prediction:
            return
        action = str(prediction.get("action") or f"use_{tool}")
        entry = {
            "timestamp": _now(),
            "event": "execution_result",
            "action": action,
            "tool": tool,
            "success": bool(success),
            "result": _compact_text(result),
        }
        self.history.append(entry)
        if not success:
            self.rejection_db.add_rejection(action, "failed_execution", result)
        self.save_state()

    def get_stats(self) -> dict[str, Any]:
        return {
            "history_count": len(self.history),
            "state_path": str(self.state_path),
            "rejections": self.rejection_db.get_stats(),
        }

    def _build_candidates(self, extraction: dict[str, Any]) -> list[dict[str, Any]]:
        intent = extraction.get("intent", "general")
        tools = list(extraction.get("tools") or [])
        hints = list(extraction.get("action_hints") or [])
        targets = extraction.get("targets") or {}
        candidates: list[dict[str, Any]] = []

        for tool in tools:
            score = 0.72
            if intent == "action":
                score += 0.12
            if tool in {"read_file", "write_file", "verify_file_exists"} and targets.get("paths"):
                score += 0.08
            if tool == "run_command" and targets.get("command"):
                score += 0.08
            candidates.append(
                {
                    "action": f"use_{tool}",
                    "tool": tool,
                    "score": round(min(score, 0.98), 3),
                    "reason": f"message matched {tool} keywords",
                    "targets": targets,
                }
            )

        for hint in hints:
            score = 0.65 if intent != "action" else 0.74
            candidates.append(
                {
                    "action": f"handle_{hint}",
                    "tool": self._tool_for_hint(hint),
                    "score": score,
                    "reason": f"message included {hint} hint",
                    "targets": targets,
                }
            )

        if intent == "query" and not tools:
            candidates.append(
                {
                    "action": "respond_direct",
                    "tool": None,
                    "score": 0.62,
                    "reason": "query without tool signal",
                    "targets": targets,
                }
            )
        return candidates

    @staticmethod
    def _tool_for_hint(hint: str) -> str | None:
        return {
            "file_operation": "read_file",
            "training": "spawn_subagent",
            "verification": "run_build",
        }.get(hint)

    def _with_confidence(self, prediction: dict[str, Any]) -> dict[str, Any]:
        score = float(prediction.get("score", 0.0))
        if score >= 0.85:
            confidence = "high"
        elif score >= 0.65:
            confidence = "medium"
        elif score > 0:
            confidence = "low"
        else:
            confidence = "none"
        out = dict(prediction)
        out["confidence"] = confidence
        out["needs_confirmation"] = out.get("tool") in {"run_command", "write_file", "spawn_subagent"}
        return out

    def _record(self, event: str, extraction: dict[str, Any], prediction: dict[str, Any]) -> None:
        self.history.append(
            {
                "timestamp": _now(),
                "event": event,
                "input": extraction,
                "prediction": prediction,
            }
        )
        self.save_state()


def format_decision_for_prompt(extraction: dict[str, Any], prediction: dict[str, Any]) -> str:
    """Render compact routing guidance for the outer LLM/tool loop."""
    action = prediction.get("action", "respond_direct")
    tool = prediction.get("tool") or "none"
    confidence = prediction.get("confidence", "low")
    reason = prediction.get("reason", "")
    targets = extraction.get("targets") or {}
    target_text = json.dumps(targets, ensure_ascii=False) if targets else "{}"
    return (
        "## Decision Layer Guidance\n"
        f"Predicted action: {action}\n"
        f"Predicted tool: {tool}\n"
        f"Confidence: {confidence}\n"
        f"Reason: {reason}\n"
        f"Targets: {target_text}\n"
        "Use this as routing guidance. Only call tools when the arguments are clear "
        "and the existing access policy allows it; otherwise answer directly or ask for the missing argument."
    )
