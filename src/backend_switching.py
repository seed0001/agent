"""Backend provider/model switching with tool-capable fallback guarantees."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
from openai import AsyncOpenAI

from config.settings import (
    OLLAMA_API_KEY,
    OLLAMA_BASE_URL,
    OLLAMA_MODEL,
    OPENAI_API_KEY,
    OPENAI_BASE_URL,
    OPENAI_MODEL,
    USER_PROFILES_DIR,
    XAI_API_KEY,
    XAI_BASE_URL,
    XAI_MODEL,
    MISTRAL_API_KEY,
    MISTRAL_BASE_URL,
    MISTRAL_MODEL,
    get_chat_model,
    get_llm_provider,
)
from src.cost_tracking import get_cost_snapshot


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(ts: datetime | None = None) -> str:
    return (ts or _utcnow()).isoformat()


def _profile_dir(user_id: str = "default") -> Path:
    p = USER_PROFILES_DIR / user_id
    p.mkdir(parents=True, exist_ok=True)
    return p


def _registry_path(user_id: str = "default") -> Path:
    return _profile_dir(user_id) / "backend_registry.json"


def _state_path(user_id: str = "default") -> Path:
    return _profile_dir(user_id) / "backend_state.json"


def _audit_path(user_id: str = "default") -> Path:
    return _profile_dir(user_id) / "backend_switch_log.jsonl"


@dataclass
class BackendEntry:
    id: str
    provider: str
    model: str
    display_name: str
    enabled: bool
    priority: int
    cost_tier: str
    quality_tier: str
    supports_tools: bool
    supports_vision: bool
    supports_reasoning: bool
    context_window: int
    api_key_env: str
    fallback_rank: int
    notes: str
    base_url: str
    api_key: str

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "BackendEntry":
        provider = str(d.get("provider", "")).strip().lower()
        base_url = str(d.get("base_url", "")).strip()
        api_key = str(d.get("api_key", ""))
        if provider == "ollama":
            # OpenAI-compatible client calls require the /v1 suffix.
            if base_url and not base_url.rstrip("/").endswith("/v1"):
                base_url = base_url.rstrip("/") + "/v1"
            # Ollama ignores API key, but OpenAI SDK expects a value.
            if not api_key:
                api_key = OLLAMA_API_KEY or "ollama"
        return cls(
            id=str(d.get("id", "")).strip(),
            provider=provider,
            model=str(d.get("model", "")).strip(),
            display_name=str(d.get("display_name", "")).strip() or str(d.get("model", "")).strip(),
            enabled=bool(d.get("enabled", True)),
            priority=int(d.get("priority", 100)),
            cost_tier=str(d.get("cost_tier", "unknown")),
            quality_tier=str(d.get("quality_tier", "unknown")),
            supports_tools=bool(d.get("supports_tools", False)),
            supports_vision=bool(d.get("supports_vision", False)),
            supports_reasoning=bool(d.get("supports_reasoning", False)),
            context_window=int(d.get("context_window", 0) or 0),
            api_key_env=str(d.get("api_key_env", "")),
            fallback_rank=int(d.get("fallback_rank", 999)),
            notes=str(d.get("notes", "")),
            base_url=base_url,
            api_key=api_key,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "provider": self.provider,
            "model": self.model,
            "display_name": self.display_name,
            "enabled": self.enabled,
            "priority": self.priority,
            "cost_tier": self.cost_tier,
            "quality_tier": self.quality_tier,
            "supports_tools": self.supports_tools,
            "supports_vision": self.supports_vision,
            "supports_reasoning": self.supports_reasoning,
            "context_window": self.context_window,
            "api_key_env": self.api_key_env,
            "fallback_rank": self.fallback_rank,
            "notes": self.notes,
            "base_url": self.base_url,
            "api_key": self.api_key,
        }


def _default_registry() -> list[dict[str, Any]]:
    return [
        {
            "id": "openai/gpt-5.5",
            "provider": "openai",
            "model": "gpt-5.5",
            "display_name": "OpenAI GPT-5.5",
            "enabled": True,
            "priority": 10,
            "cost_tier": "high",
            "quality_tier": "premium",
            "supports_tools": True,
            "supports_vision": True,
            "supports_reasoning": True,
            "context_window": 1_000_000,
            "api_key_env": "OPENAI_API_KEY",
            "fallback_rank": 5,
            "notes": "Premium model.",
            "base_url": OPENAI_BASE_URL,
            "api_key": OPENAI_API_KEY,
        },
        {
            "id": "xai/grok-4.3",
            "provider": "xai",
            "model": "grok-4.3",
            "display_name": "xAI Grok 4.3",
            "enabled": True,
            "priority": 20,
            "cost_tier": "medium",
            "quality_tier": "high",
            "supports_tools": True,
            "supports_vision": False,
            "supports_reasoning": True,
            "context_window": 1_000_000,
            "api_key_env": "XAI_API_KEY",
            "fallback_rank": 4,
            "notes": "Main cheap cloud candidate.",
            "base_url": XAI_BASE_URL,
            "api_key": XAI_API_KEY,
        },
        {
            "id": "openai/gpt-4.1-mini",
            "provider": "openai",
            "model": "gpt-4.1-mini",
            "display_name": "OpenAI GPT-4.1-mini",
            "enabled": True,
            "priority": 40,
            "cost_tier": "low",
            "quality_tier": "medium",
            "supports_tools": True,
            "supports_vision": False,
            "supports_reasoning": True,
            "context_window": 256_000,
            "api_key_env": "OPENAI_API_KEY",
            "fallback_rank": 1,
            "notes": "Default life-support backend (tool-capable).",
            "base_url": OPENAI_BASE_URL,
            "api_key": OPENAI_API_KEY,
        },
        {
            "id": "openai/gpt-4.1-nano",
            "provider": "openai",
            "model": "gpt-4.1-nano",
            "display_name": "OpenAI GPT-4.1-nano",
            "enabled": True,
            "priority": 50,
            "cost_tier": "very_low",
            "quality_tier": "low",
            "supports_tools": False,
            "supports_vision": False,
            "supports_reasoning": False,
            "context_window": 128_000,
            "api_key_env": "OPENAI_API_KEY",
            "fallback_rank": 2,
            "notes": "Not life-support unless tool-calling is explicitly verified.",
            "base_url": OPENAI_BASE_URL,
            "api_key": OPENAI_API_KEY,
        },
        {
            "id": "anthropic/claude-haiku-4.5",
            "provider": "anthropic",
            "model": "claude-haiku-4.5",
            "display_name": "Claude Haiku 4.5",
            "enabled": False,
            "priority": 60,
            "cost_tier": "low",
            "quality_tier": "medium",
            "supports_tools": False,
            "supports_vision": False,
            "supports_reasoning": True,
            "context_window": 200_000,
            "api_key_env": "ANTHROPIC_API_KEY",
            "fallback_rank": 3,
            "notes": "Optional future adapter.",
            "base_url": "",
            "api_key": "",
        },
        {
            "id": "mistral/mistral-medium-latest",
            "provider": "mistral",
            "model": "mistral-medium-latest",
            "display_name": "Mistral Medium Latest",
            "enabled": True,
            "priority": 45,
            "cost_tier": "low",
            "quality_tier": "medium",
            "supports_tools": True,
            "supports_vision": False,
            "supports_reasoning": True,
            "context_window": 128_000,
            "api_key_env": "MISTRAL_API_KEY",
            "fallback_rank": 3,
            "notes": "OpenAI-compatible fallback candidate.",
            "base_url": MISTRAL_BASE_URL,
            "api_key": MISTRAL_API_KEY,
        },
        {
            "id": f"ollama/{OLLAMA_MODEL}",
            "provider": "ollama",
            "model": OLLAMA_MODEL,
            "display_name": f"Ollama {OLLAMA_MODEL}",
            "enabled": True,
            "priority": 80,
            "cost_tier": "free",
            "quality_tier": "variable",
            "supports_tools": False,
            "supports_vision": False,
            "supports_reasoning": False,
            "context_window": 32_000,
            "api_key_env": "OLLAMA_API_KEY",
            "fallback_rank": 10,
            "notes": "Local model. Enable tools only after explicit verification.",
            "base_url": OLLAMA_BASE_URL,
            "api_key": OLLAMA_API_KEY,
        },
    ]


def _default_state() -> dict[str, Any]:
    env_provider = get_llm_provider()
    env_model = get_chat_model()
    active = f"{env_provider}/{env_model}"
    return {
        "active_backend": active,
        "last_successful_backend": active,
        "last_known_tool_capable_backend": "openai/gpt-4.1-mini",
        "life_support_backend": "openai/gpt-4.1-mini",
        "life_support_requires_tools": True,
        "local_fallback_backend": f"ollama/{OLLAMA_MODEL}",
        "updated_at": _iso(),
        "updated_by": "bootstrap",
        "last_switch": {},
        "unhealthy_backends": {},
    }


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _read_json(path: Path) -> Any:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_registry(user_id: str = "default") -> list[BackendEntry]:
    p = _registry_path(user_id)
    if not p.exists():
        _write_json(p, _default_registry())
    raw = _read_json(p)
    entries = [BackendEntry.from_dict(x) for x in (raw or [])]
    # Persist normalized entries (e.g., Ollama base_url /v1 correction).
    if (raw or []) != [e.to_dict() for e in entries]:
        save_registry(entries, user_id)
    return entries


def save_registry(entries: list[BackendEntry], user_id: str = "default") -> None:
    _write_json(_registry_path(user_id), [e.to_dict() for e in entries])


def load_state(user_id: str = "default") -> dict[str, Any]:
    p = _state_path(user_id)
    if not p.exists():
        _write_json(p, _default_state())
    data = _read_json(p)
    if not isinstance(data, dict):
        data = _default_state()
    return data


def save_state(state: dict[str, Any], user_id: str = "default") -> None:
    state["updated_at"] = _iso()
    _write_json(_state_path(user_id), state)


def append_audit(entry: dict[str, Any], user_id: str = "default") -> None:
    p = _audit_path(user_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _registry_map(user_id: str = "default") -> dict[str, BackendEntry]:
    return {e.id: e for e in load_registry(user_id)}


def get_active_backend(user_id: str = "default") -> BackendEntry:
    state = load_state(user_id)
    reg = _registry_map(user_id)
    active = str(state.get("active_backend", "")).strip()
    if active in reg and reg[active].enabled:
        return reg[active]
    # Last known fallback to life support.
    life = str(state.get("life_support_backend", "")).strip()
    if life in reg and reg[life].enabled:
        state["active_backend"] = life
        save_state(state, user_id)
        return reg[life]
    # Last resort: first enabled tool-capable backend.
    for entry in load_registry(user_id):
        if entry.enabled and entry.supports_tools:
            state["active_backend"] = entry.id
            save_state(state, user_id)
            return entry
    raise RuntimeError("No enabled tool-capable backend available.")


def _normalize_target_aliases(target: str, user_id: str = "default") -> tuple[str | None, str]:
    t = (target or "").strip().lower()
    if not t:
        return None, "missing_target"
    reg = load_registry(user_id)
    ids = [e.id for e in reg if e.enabled]

    # Direct id
    if t in {i.lower() for i in ids}:
        for i in ids:
            if i.lower() == t:
                return i, "direct"

    # Common aliases
    if "switch back" in t or "last model" in t:
        st = load_state(user_id)
        b = str(st.get("last_successful_backend", "")).strip()
        return (b if b else None), "alias_last_successful"
    if "grok" in t or "xai" in t:
        for cand in ("xai/grok-4.3", f"xai/{XAI_MODEL}"):
            if cand in ids:
                return cand, "alias_grok"
    if "cheap openai" in t or "fallback" in t or "mini" in t:
        if "openai/gpt-4.1-mini" in ids:
            return "openai/gpt-4.1-mini", "alias_cheap_openai"
    if "nano" in t and "openai/gpt-4.1-nano" in ids:
        return "openai/gpt-4.1-nano", "alias_nano"
    if "premium" in t or "best" in t or "gpt-5.5" in t:
        if "openai/gpt-5.5" in ids:
            return "openai/gpt-5.5", "alias_premium"
    if "ollama" in t or "local" in t:
        st = load_state(user_id)
        local_id = str(st.get("local_fallback_backend", "")).strip()
        if local_id in ids:
            return local_id, "alias_local"
    if "mistral" in t:
        for i in ids:
            if i.startswith("mistral/"):
                return i, "alias_mistral"

    # Fuzzy contains
    matches = [i for i in ids if t in i.lower()]
    if len(matches) == 1:
        return matches[0], "fuzzy"
    if len(matches) > 1:
        return None, "ambiguous"
    return None, "not_found"


def _classify_error(msg: str) -> str:
    m = (msg or "").lower()
    if "api key" in m and ("missing" in m or "not set" in m):
        return "missing_api_key"
    if "invalid api key" in m or "unauthorized" in m or "authentication" in m:
        return "invalid_api_key"
    if "quota" in m or "credit" in m or "insufficient" in m:
        return "quota_exceeded_or_no_credits"
    if "rate limit" in m or "429" in m:
        return "rate_limited"
    if "not found" in m or "does not exist" in m or "unknown model" in m:
        return "model_not_found"
    if "timeout" in m:
        return "timeout"
    if "connection" in m or "temporarily unavailable" in m or "503" in m:
        return "provider_unavailable"
    if "tool" in m and "support" in m:
        return "tool_calling_unavailable"
    if "tool_choice" in m and "no tools were specified" in m:
        return "tool_calling_unavailable"
    if "invalid argument" in m or "invalid request content" in m:
        return "unknown_error"
    return "unknown_error"


async def health_check_backend(
    backend_id: str,
    *,
    require_tools: bool = True,
    user_id: str = "default",
) -> dict[str, Any]:
    reg = _registry_map(user_id)
    b = reg.get(backend_id)
    if b is None:
        return {"healthy": False, "reason": "model_not_found", "message": f"Backend not found: {backend_id}"}
    if not b.enabled:
        return {"healthy": False, "reason": "model_not_found", "message": f"Backend disabled: {backend_id}"}
    if require_tools and not b.supports_tools:
        return {
            "healthy": False,
            "reason": "tool_calling_unavailable",
            "message": f"{backend_id} is not marked tool-capable for interactive use.",
        }

    if b.provider == "ollama":
        try:
            base = b.base_url.replace("/v1", "")
            async with httpx.AsyncClient(timeout=8.0) as client:
                tags = await client.get(f"{base}/api/tags")
                tags.raise_for_status()
                models = tags.json().get("models") or []
                names = {
                    str(m.get("name", "")).strip()
                    for m in models
                    if isinstance(m, dict)
                }
                if b.model not in names and not any(n.startswith(b.model + ":") for n in names):
                    return {
                        "healthy": False,
                        "reason": "local_model_unavailable",
                        "message": f"Ollama model not available: {b.model}",
                    }
            # Validate the OpenAI-compatible chat endpoint too; model listing
            # alone can pass while generation endpoint is unavailable.
            chat_client = AsyncOpenAI(api_key=(b.api_key or "ollama"), base_url=b.base_url)
            probe_kwargs: dict[str, Any] = {
                "model": b.model,
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 16,
            }
            if require_tools:
                probe_kwargs["tools"] = [
                    {
                        "type": "function",
                        "function": {
                            "name": "health_check_noop",
                            "description": "No-op tool capability probe.",
                            "parameters": {
                                "type": "object",
                                "properties": {},
                                "additionalProperties": False,
                            },
                        },
                    }
                ]
                probe_kwargs["tool_choice"] = "auto"
            await chat_client.chat.completions.create(**probe_kwargs)
            return {"healthy": True, "reason": "ok", "message": "healthy"}
        except Exception as e:
            msg = str(e)
            return {
                "healthy": False,
                "reason": _classify_error(msg),
                "message": msg,
            }

    if not b.api_key:
        return {
            "healthy": False,
            "reason": "missing_api_key",
            "message": f"{b.api_key_env} is not set.",
        }
    try:
        client = AsyncOpenAI(api_key=b.api_key, base_url=b.base_url)
        try:
            await client.chat.completions.create(
                model=b.model,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=16,
            )
        except Exception as first_err:
            first_msg = str(first_err).lower()
            # Some newer OpenAI models (e.g. GPT-5.x) require
            # max_completion_tokens instead of max_tokens.
            if (
                "unsupported parameter" in first_msg
                and "max_tokens" in first_msg
                and "max_completion_tokens" in first_msg
            ):
                await client.chat.completions.create(
                    model=b.model,
                    messages=[{"role": "user", "content": "ping"}],
                    max_completion_tokens=16,
                )
            else:
                raise
        return {"healthy": True, "reason": "ok", "message": "healthy"}
    except Exception as e:
        msg = str(e)
        return {"healthy": False, "reason": _classify_error(msg), "message": msg}


def _mark_unhealthy(backend_id: str, reason: str, message: str, user_id: str = "default") -> None:
    st = load_state(user_id)
    unhealthy = dict(st.get("unhealthy_backends") or {})
    unhealthy[backend_id] = {
        "backend": backend_id,
        "healthy": False,
        "tool_capable": bool(_registry_map(user_id).get(backend_id).supports_tools if _registry_map(user_id).get(backend_id) else False),
        "reason": reason,
        "message": message[:300],
        "checked_at": _iso(),
        "retry_after_seconds": 3600,
    }
    st["unhealthy_backends"] = unhealthy
    save_state(st, user_id)


def _clear_unhealthy(backend_id: str, user_id: str = "default") -> None:
    st = load_state(user_id)
    unhealthy = dict(st.get("unhealthy_backends") or {})
    if backend_id in unhealthy:
        unhealthy.pop(backend_id, None)
        st["unhealthy_backends"] = unhealthy
        save_state(st, user_id)


def _fallback_candidates(requested_backend: str, user_id: str = "default") -> list[str]:
    st = load_state(user_id)
    reg = load_registry(user_id)
    ids: list[str] = []
    for candidate in (
        str(st.get("last_successful_backend", "")).strip(),
        str(st.get("last_known_tool_capable_backend", "")).strip(),
        str(st.get("life_support_backend", "")).strip(),
    ):
        if candidate and candidate != requested_backend and candidate not in ids:
            ids.append(candidate)
    # Cheapest/most fallback-suitable tool-capable cloud choices next.
    for e in sorted(reg, key=lambda x: (x.fallback_rank, x.priority)):
        if not e.enabled or not e.supports_tools:
            continue
        if e.id not in ids and e.id != requested_backend:
            ids.append(e.id)
    return ids


def ensure_life_support_valid_or_raise(user_id: str = "default") -> None:
    reg = _registry_map(user_id)
    st = load_state(user_id)
    life = str(st.get("life_support_backend", "")).strip()
    if not life or life not in reg:
        raise RuntimeError("Life-support backend is missing or unknown.")
    entry = reg[life]
    if not entry.enabled:
        raise RuntimeError(f"Life-support backend is disabled: {life}")
    if not entry.supports_tools:
        raise RuntimeError(
            f"Life-support backend must be tool-capable, got supports_tools=false for {life}"
        )
    if not bool(st.get("life_support_requires_tools", True)):
        raise RuntimeError("life_support_requires_tools must be true.")


def bootstrap_backend_files(user_id: str = "default") -> None:
    # Ensure files exist and state points to known entries.
    reg = load_registry(user_id)
    st = load_state(user_id)
    reg_ids = {e.id for e in reg}
    active = str(st.get("active_backend", "")).strip()
    if active not in reg_ids:
        env_id = f"{get_llm_provider()}/{get_chat_model()}"
        st["active_backend"] = env_id if env_id in reg_ids else "openai/gpt-4.1-mini"
    life = str(st.get("life_support_backend", "")).strip()
    if life not in reg_ids:
        st["life_support_backend"] = "openai/gpt-4.1-mini"
    save_state(st, user_id)


async def switch_backend_provider(
    *,
    target: str,
    reason: str = "creator_requested",
    dry_run: bool = False,
    force: bool = False,
    requested_by: str = "creator",
    user_id: str = "default",
) -> dict[str, Any]:
    bootstrap_backend_files(user_id)
    ensure_life_support_valid_or_raise(user_id)
    state = load_state(user_id)
    previous_backend = str(state.get("active_backend", "")).strip()
    resolved, mode = _normalize_target_aliases(target, user_id)
    if not resolved:
        msg = (
            "Could not resolve target backend."
            if mode not in {"ambiguous"}
            else "Target is ambiguous. Be more specific (e.g. 'grok-4.3' or 'gpt-4.1-mini')."
        )
        out = {
            "success": False,
            "requested_backend": target,
            "active_backend": previous_backend,
            "fallback_used": False,
            "failure_reason": "model_not_found" if mode != "ambiguous" else "unknown_error",
            "message": msg,
        }
        append_audit(
            {
                "timestamp": _iso(),
                "requested_by": requested_by,
                "requested_target": target,
                "resolved_backend": None,
                "previous_backend": previous_backend,
                "final_backend": previous_backend,
                "success": False,
                "fallback_used": False,
                "failure_reason": out["failure_reason"],
                "tool_capable_final_backend": bool(_registry_map(user_id).get(previous_backend).supports_tools if _registry_map(user_id).get(previous_backend) else False),
                "message": out["message"],
            },
            user_id,
        )
        return out

    requested = _registry_map(user_id).get(resolved)
    if requested is None or not requested.enabled:
        return {
            "success": False,
            "requested_backend": resolved,
            "active_backend": previous_backend,
            "fallback_used": False,
            "failure_reason": "model_not_found",
            "message": f"Requested backend is unavailable: {resolved}",
        }

    if not force and not requested.supports_tools:
        fallback = str(state.get("life_support_backend", "")).strip()
        out = {
            "success": False,
            "requested_backend": resolved,
            "active_backend": fallback or previous_backend,
            "fallback_used": True,
            "failure_reason": "requested_backend_not_tool_capable",
            "message": (
                f"I did not switch to {resolved} because tool calling is not verified. "
                f"I stayed on {fallback or previous_backend} so I can still call switching tools."
            ),
        }
        append_audit(
            {
                "timestamp": _iso(),
                "requested_by": requested_by,
                "requested_target": target,
                "resolved_backend": resolved,
                "previous_backend": previous_backend,
                "final_backend": out["active_backend"],
                "success": False,
                "fallback_used": True,
                "failure_reason": out["failure_reason"],
                "tool_capable_final_backend": True,
                "message": out["message"],
            },
            user_id,
        )
        return out

    requested_health = await health_check_backend(resolved, require_tools=True, user_id=user_id)
    if requested_health.get("healthy"):
        if not dry_run:
            state["active_backend"] = resolved
            state["last_successful_backend"] = resolved
            if requested.supports_tools:
                state["last_known_tool_capable_backend"] = resolved
            state["last_switch"] = {
                "requested_target": target,
                "resolved_backend": resolved,
                "previous_backend": previous_backend,
                "final_backend": resolved,
                "success": True,
                "fallback_used": False,
                "reason": reason,
                "mode": mode,
                "at": _iso(),
            }
            state["updated_by"] = requested_by
            save_state(state, user_id)
            _clear_unhealthy(resolved, user_id)
        out = {
            "success": True,
            "requested_backend": resolved,
            "active_backend": resolved,
            "fallback_used": False,
            "tool_capable": requested.supports_tools,
            "message": f"Switched to {requested.display_name}. Tool calling is available.",
        }
        append_audit(
            {
                "timestamp": _iso(),
                "requested_by": requested_by,
                "requested_target": target,
                "resolved_backend": resolved,
                "previous_backend": previous_backend,
                "final_backend": resolved,
                "success": True,
                "fallback_used": False,
                "failure_reason": None,
                "tool_capable_final_backend": requested.supports_tools,
                "message": out["message"],
            },
            user_id,
        )
        return out

    _mark_unhealthy(
        resolved,
        requested_health.get("reason", "unknown_error"),
        requested_health.get("message", ""),
        user_id,
    )

    # fallback chain
    for cand in _fallback_candidates(resolved, user_id):
        entry = _registry_map(user_id).get(cand)
        if not entry or not entry.enabled or not entry.supports_tools:
            continue
        h = await health_check_backend(cand, require_tools=True, user_id=user_id)
        if not h.get("healthy"):
            _mark_unhealthy(cand, h.get("reason", "unknown_error"), h.get("message", ""), user_id)
            continue
        if not dry_run:
            state["active_backend"] = cand
            state["last_successful_backend"] = cand
            state["last_known_tool_capable_backend"] = cand
            state["last_switch"] = {
                "requested_target": target,
                "resolved_backend": resolved,
                "previous_backend": previous_backend,
                "final_backend": cand,
                "success": False,
                "fallback_used": True,
                "failure_reason": requested_health.get("reason", "unknown_error"),
                "reason": reason,
                "mode": mode,
                "at": _iso(),
            }
            state["updated_by"] = requested_by
            save_state(state, user_id)
            _clear_unhealthy(cand, user_id)
        msg = (
            f"I tried switching to {resolved}, but it failed ({requested_health.get('reason')}). "
            f"I fell back to {cand} so I can stay online and still use tools."
        )
        out = {
            "success": False,
            "requested_backend": resolved,
            "active_backend": cand,
            "fallback_used": True,
            "failure_reason": requested_health.get("reason", "unknown_error"),
            "tool_capable": True,
            "message": msg,
        }
        append_audit(
            {
                "timestamp": _iso(),
                "requested_by": requested_by,
                "requested_target": target,
                "resolved_backend": resolved,
                "previous_backend": previous_backend,
                "final_backend": cand,
                "success": False,
                "fallback_used": True,
                "failure_reason": out["failure_reason"],
                "tool_capable_final_backend": True,
                "message": out["message"],
            },
            user_id,
        )
        return out

    # Hard failure: keep current backend.
    msg = (
        f"I could not switch to {resolved}, and no tool-capable fallback passed health checks. "
        "I stayed on the last known working backend so you are not trapped in text-only mode."
    )
    out = {
        "success": False,
        "requested_backend": resolved,
        "active_backend": previous_backend,
        "fallback_used": False,
        "failure_reason": requested_health.get("reason", "unknown_error"),
        "tool_capable": bool(_registry_map(user_id).get(previous_backend).supports_tools if _registry_map(user_id).get(previous_backend) else False),
        "message": msg,
    }
    append_audit(
        {
            "timestamp": _iso(),
            "requested_by": requested_by,
            "requested_target": target,
            "resolved_backend": resolved,
            "previous_backend": previous_backend,
            "final_backend": previous_backend,
            "success": False,
            "fallback_used": False,
            "failure_reason": out["failure_reason"],
            "tool_capable_final_backend": out["tool_capable"],
            "message": out["message"],
        },
        user_id,
    )
    return out


def get_backend_status(user_id: str = "default") -> dict[str, Any]:
    bootstrap_backend_files(user_id)
    ensure_life_support_valid_or_raise(user_id)
    st = load_state(user_id)
    reg = _registry_map(user_id)
    active_id = str(st.get("active_backend", "")).strip()
    active = reg.get(active_id)
    life_id = str(st.get("life_support_backend", "")).strip()
    life = reg.get(life_id)
    cost_snapshot = get_cost_snapshot(period="today", include_free=True, user_id=user_id)
    return {
        "active_backend": active_id,
        "provider": active.provider if active else "",
        "model": active.model if active else "",
        "tool_capable": bool(active.supports_tools) if active else False,
        "last_successful_backend": st.get("last_successful_backend"),
        "last_known_tool_capable_backend": st.get("last_known_tool_capable_backend"),
        "life_support_backend": life_id,
        "life_support_tool_capable": bool(life.supports_tools) if life else False,
        "available_backends": [e.to_dict() for e in load_registry(user_id) if e.enabled],
        "unhealthy_backends": list((st.get("unhealthy_backends") or {}).values()),
        "last_switch": st.get("last_switch") or {},
        "cost_snapshot": cost_snapshot,
    }


def format_backend_context(user_id: str = "default") -> str:
    s = get_backend_status(user_id)
    unhealthy = s.get("unhealthy_backends") or []
    unhealthy_line = "none"
    if unhealthy:
        items = []
        for u in unhealthy[:3]:
            items.append(f"{u.get('backend')} {u.get('reason')} checked {u.get('checked_at')}")
        unhealthy_line = "; ".join(items)
    return (
        "## Backend Status\n"
        f"Current backend: {s.get('active_backend')}\n"
        f"Tool-capable: {'yes' if s.get('tool_capable') else 'no'}\n"
        f"Last successful backend: {s.get('last_successful_backend')}\n"
        f"Last known tool-capable backend: {s.get('last_known_tool_capable_backend')}\n"
        f"Life-support fallback: {s.get('life_support_backend')}, "
        f"{'tool-capable verified' if s.get('life_support_tool_capable') else 'NOT tool-capable'}\n"
        f"Unhealthy backends: {unhealthy_line}"
    )

