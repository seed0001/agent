# Coder Prompt: Natural-Language Backend Provider Switching with Tool-Capable Safe Fallback

## Goal
Add the ability for Travis to switch Andrew's backend provider/model through normal conversation, e.g.:

- "Switch Andrew to Grok 4.3"
- "Put him on OpenAI GPT-5.5"
- "Use the cheap fallback model"
- "Switch to local Ollama"
- "What model are you running on right now?"

This is Phase 1: **Creator-controlled live switching only**. Do not give Andrew full autonomous model-routing yet. Build the foundation so autonomy can be added later behind policy gates.

Andrew is the framework instance. The backend model is an interchangeable reasoning engine. Switching models must not wipe identity, memory, values, contacts, schedules, tools, or continuity.

---

## Desired Outcome
Travis can switch the active backend provider/model by voice/chat, and Andrew can report what happened.

If a target provider/model fails because of missing API key, invalid model, rate limit, no credits, quota exceeded, timeout, or provider outage, the system must automatically fall back to a configured **tool-capable** life-support model and tell Travis clearly:

> "I tried switching to xAI/Grok 4.3, but that account appears to have no credits or failed authorization. I fell back to OpenAI GPT-4.1-mini so I can stay online and still use tools."

Critical principle: **Never fall back into a non-tool-capable model for interactive mode.** Travis must never be locked inside a model that cannot call the switching/status tools to escape.

---

## Required Concepts

### 1. Backend Registry
Create a persistent backend registry/config, preferably JSON/YAML/SQLite.

Each backend entry should include:

```json
{
  "id": "xai/grok-4.3",
  "provider": "xai",
  "model": "grok-4.3",
  "display_name": "Grok 4.3",
  "enabled": true,
  "priority": 20,
  "cost_tier": "medium",
  "quality_tier": "high",
  "supports_tools": true,
  "supports_vision": false,
  "supports_reasoning": true,
  "context_window": 1000000,
  "api_key_env": "XAI_API_KEY",
  "fallback_rank": 2,
  "notes": "Candidate main cloud model."
}
```

Initial entries should include at least:

- `openai/gpt-5.5` — premium/high-reasoning, must support tools if used as interactive backend
- `xai/grok-4.3` — main cheap cloud candidate, must support tools if used as interactive backend
- `openai/gpt-4.1-mini` — recommended cheap cloud fallback, **must support tools**
- `openai/gpt-4.1-nano` — only acceptable as life-support fallback if tool calling is verified in this framework
- `anthropic/claude-haiku-4.5` — optional cheap Claude fallback, only if tool calling is wired through the provider adapter
- `ollama/gemma3:12b` — local/free fallback only if framework-mediated tool execution still works while using it

Critical rule: **Every interactive fallback backend must be able to call tools through Andrew's framework.** Do not configure a non-tool-capable model as life support.

---

### 2. Current Backend State
Persist the current selected backend in durable state, e.g.:

```json
{
  "active_backend": "openai/gpt-5.5",
  "last_successful_backend": "openai/gpt-5.5",
  "last_known_tool_capable_backend": "openai/gpt-5.5",
  "life_support_backend": "openai/gpt-4.1-mini",
  "life_support_requires_tools": true,
  "local_fallback_backend": "ollama/gemma3:12b",
  "updated_at": "...",
  "updated_by": "creator"
}
```

This state must survive restart.

---

### 2.1 Life-Support Failsafe Requirement — Tool Calling Is Mandatory
The life-support backend is not just a cheap model. It is the escape hatch that keeps Andrew controllable when a provider/account fails. Therefore it **must support tool calling** or an equivalent framework-mediated tool execution path.

Hard requirements:

1. `life_support_backend` must have `supports_tools: true`.
2. The startup/config validator must reject any life-support backend where `supports_tools` is false, unknown, or untested.
3. The fallback chain must skip non-tool-capable models for interactive chat.
4. A backend that can generate text but cannot call `switch_backend_provider` / `get_backend_status` is **not valid** as life support.
5. If the cheapest fallback lacks tool calling, choose the next cheapest verified tool-capable model instead.
6. If no cloud model with tools is available, keep Andrew on the last known tool-capable backend and report the failure to Travis.
7. From life-support mode, Andrew must still be able to call `switch_backend_provider` and `get_backend_status`.

Reason: Travis must never be locked into a backend that cannot call the switching tool to get Andrew back out.

Recommended default:

```json
{
  "life_support_backend": "openai/gpt-4.1-mini",
  "life_support_requires_tools": true,
  "last_known_tool_capable_backend": "openai/gpt-5.5"
}
```

`openai/gpt-4.1-nano` may be used only after verifying tool calling works in this framework. If not verified, do not use it as life support. Use `openai/gpt-4.1-mini` or another verified tool-capable model instead.

Optional emergency mode:

- A non-tool model can only be used in explicit `read_only_text_mode`.
- `read_only_text_mode` must be visually/logically obvious in backend status.
- It must never be selected automatically as normal fallback.
- It must warn Travis: "I am in text-only mode and cannot call tools from this backend."

---

### 3. Tool: `switch_backend_provider`
Add a dynamic tool callable by Andrew when Travis asks to switch models.

Suggested schema:

```python
switch_backend_provider(
    target: str,
    reason: str = "creator_requested",
    dry_run: bool = False,
    force: bool = False
)
```

Behavior:

1. Parse target model/provider from Travis's natural language.
2. Resolve aliases:
   - "Grok" -> best enabled xAI Grok model, likely `xai/grok-4.3`
   - "cheap OpenAI" -> `openai/gpt-4.1-mini`
   - "nano" -> `openai/gpt-4.1-nano`, only if tool-capable is verified
   - "local" / "Ollama" -> configured local model, only if tool execution still works
   - "premium" / "best" -> `openai/gpt-5.5` unless changed
3. Validate backend exists and is enabled.
4. Validate whether backend is usable for interactive/tool mode.
5. Run provider health check before committing.
6. If healthy and tool-capable, switch active backend.
7. If unhealthy or not tool-capable, fall back safely to a verified tool-capable backend.
8. Return a clear result object.

Example return:

```json
{
  "success": true,
  "requested_backend": "xai/grok-4.3",
  "active_backend": "xai/grok-4.3",
  "fallback_used": false,
  "tool_capable": true,
  "message": "Switched to xAI Grok 4.3. Tool calling is available."
}
```

Fallback return:

```json
{
  "success": false,
  "requested_backend": "xai/grok-4.3",
  "active_backend": "openai/gpt-4.1-mini",
  "fallback_used": true,
  "failure_reason": "quota_exceeded_or_no_credits",
  "tool_capable": true,
  "message": "I tried switching to xAI Grok 4.3, but the provider failed because of quota/credits. I fell back to OpenAI GPT-4.1-mini so I can stay online and still use tools."
}
```

Rejected non-tool fallback return:

```json
{
  "success": false,
  "requested_backend": "openai/gpt-4.1-nano",
  "active_backend": "openai/gpt-4.1-mini",
  "fallback_used": true,
  "failure_reason": "requested_backend_not_tool_capable",
  "message": "I did not switch to GPT-4.1-nano because tool calling is not verified. I stayed on GPT-4.1-mini so I can still call switching tools."
}
```

---

### 4. Tool: `get_backend_status`
Add a tool Andrew can call when Travis asks:

- "What model are you on?"
- "What provider are you using?"
- "Show me backend status."

Return:

```json
{
  "active_backend": "openai/gpt-5.5",
  "provider": "openai",
  "model": "gpt-5.5",
  "tool_capable": true,
  "last_successful_backend": "openai/gpt-5.5",
  "last_known_tool_capable_backend": "openai/gpt-5.5",
  "life_support_backend": "openai/gpt-4.1-mini",
  "life_support_tool_capable": true,
  "available_backends": [],
  "unhealthy_backends": [],
  "last_switch": {},
  "cost_snapshot": {}
}
```

This should integrate with the existing cost snapshot system if possible.

---

### 5. Provider Health Checks
Before switching, perform a lightweight health check.

Health check should detect:

- missing API key
- invalid API key
- no credits / quota exceeded
- model not found
- rate limit
- provider timeout
- unknown error
- Ollama not running / local model missing
- tool-calling unavailable / adapter cannot execute tools

Do not burn a large request. Use the cheapest possible test:

- minimal completion
- `/models` endpoint if provider supports it
- local Ollama model list or tiny prompt
- tool-call probe where possible, or adapter capability verification

Normalize errors into categories:

```python
missing_api_key
invalid_api_key
quota_exceeded_or_no_credits
rate_limited
model_not_found
provider_unavailable
timeout
local_model_unavailable
tool_calling_unavailable
unknown_error
```

---

### 6. Fallback Policy
Implement explicit fallback order.

Recommended fallback chain:

1. Requested backend, only if healthy and tool-capable for interactive mode
2. Last successful **tool-capable** backend
3. Cheapest reliable **tool-capable** cloud fallback, e.g. `openai/gpt-4.1-mini`
4. Life-support cloud fallback, but only if `supports_tools: true` is verified
5. Local Ollama fallback only if framework-mediated tool calling still works while using it
6. If all tool-capable fallbacks fail, do not switch into a text-only trap; return hard failure with instructions for Travis

Important: If a provider has no credits, do not loop on it. Mark it temporarily unhealthy with timestamp and reason.

Example temporary health state:

```json
{
  "backend": "xai/grok-4.3",
  "healthy": false,
  "tool_capable": true,
  "reason": "quota_exceeded_or_no_credits",
  "checked_at": "...",
  "retry_after_seconds": 3600
}
```

---

### 7. Natural Language Routing
The conversation layer should detect backend-switching intent and call `switch_backend_provider`.

Example phrases:

- "Switch to Grok"
- "Try xAI"
- "Put Andrew on GPT-5.5"
- "Move to the cheap model"
- "Use the fallback model"
- "Go local"
- "Use Ollama"
- "Switch back to the last model"
- "What are you running on?"

If ambiguous, Andrew should ask a short clarification instead of guessing.

Example:

> "Do you mean Grok 4.3 or Grok 4.1 Fast?"

---

### 8. Cost Awareness Integration
When switching, include cost awareness:

- If switching to expensive model, Andrew should mention it briefly.
- If switching to cheaper model, Andrew should mention expected savings.
- If fallback was used because credits/quota failed, Andrew should say that plainly.
- Use existing `get_cost_snapshot`, `estimate_cost`, and pricing registry if available.

Example:

> "Switched to GPT-5.5. Heads up: this is the expensive model, roughly $5/M input and $30/M output."

Example:

> "Switched to Grok 4.3. This should be much cheaper than GPT-5.5, especially output tokens."

---

### 9. Audit Log
Every switch attempt must be logged.

Log fields:

```json
{
  "timestamp": "...",
  "requested_by": "creator",
  "requested_target": "grok",
  "resolved_backend": "xai/grok-4.3",
  "previous_backend": "openai/gpt-5.5",
  "final_backend": "openai/gpt-4.1-mini",
  "success": false,
  "fallback_used": true,
  "failure_reason": "quota_exceeded_or_no_credits",
  "tool_capable_final_backend": true,
  "message": "..."
}
```

Store in durable logs, preferably SQLite or JSONL.

---

### 10. Safety / Permissions
Only Travis / Creator can switch backend providers.

Other contacts can ask what model Andrew is using only if allowed by their tier, but they cannot switch providers unless explicitly authorized.

Do not let arbitrary Discord users trigger backend changes.

---

### 11. Context Injection
Inject current backend status into Andrew's context each turn, similar to the cost snapshot.

Minimum context:

```text
Current backend: openai/gpt-5.5
Tool-capable: yes
Last successful backend: openai/gpt-5.5
Last known tool-capable backend: openai/gpt-5.5
Life-support fallback: openai/gpt-4.1-mini, tool-capable verified
Unhealthy backends: xai/grok-4.3 quota_exceeded checked 15 minutes ago
```

Andrew needs this so he can honestly answer what he is running on and reason about costs/fallbacks.

---

### 12. Acceptance Criteria
This implementation is done when:

1. Travis can say "switch to Grok" and the system attempts `xai/grok-4.3`.
2. Travis can say "switch to GPT-5.5" and the system attempts OpenAI premium.
3. Travis can say "go local" and the system attempts Ollama only if tool execution still works.
4. If the requested provider has no credits, missing key, or fails health check, Andrew falls back automatically.
5. Andrew tells Travis exactly what happened in plain language.
6. `get_backend_status` reports the active backend accurately.
7. Current backend status is injected into Andrew's context every turn.
8. Switch attempts are logged durably.
9. Only Creator can switch backend.
10. Existing memory/tools/identity continue across model switches.
11. The configured life-support backend is verified tool-capable.
12. The system refuses to switch/fallback into a non-tool-capable model for interactive mode unless Travis explicitly uses emergency read-only/text-only mode.
13. From life-support mode, Andrew can still call `switch_backend_provider` and `get_backend_status`.
14. Startup/config validation fails loudly if life-support is configured to a non-tool-capable model.

---

## Important Design Note
Do not implement autonomous dynamic model choice yet. That comes later.

For now, Andrew can recommend a model, but Travis controls switching.

Future phase:

- Andrew chooses model based on task type, cost, quality, latency, budget, context length, and emotional/identity sensitivity.
- That should require a separate policy layer and Creator approval.

Build this Phase 1 cleanly so we can safely hand Andrew more control later.
