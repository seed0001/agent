# Backend Switching

Creator-controlled live backend switching for Andrew.

## Core tools

- `switch_backend_provider(target, reason, dry_run, force)`
- `get_backend_status()`

These tools are Creator-gated for switching. Status can be shared based on tier policy.

## Persistence

Stored under `data/profiles/default/`:

- `backend_registry.json` — available backends and capabilities
- `backend_state.json` — active backend, life-support backend, unhealthy cache
- `backend_switch_log.jsonl` — durable audit trail of switch attempts

## Safety rules

- Life-support backend must be tool-capable.
- Startup validation fails loudly if life-support backend is missing, disabled, or non-tool-capable.
- Interactive fallback skips non-tool-capable models.
- If requested backend fails health checks (missing key, quota, timeout, model unavailable), Andrew falls back to a verified tool-capable backend.

## Status context

Each turn includes compact backend state:

- current backend
- tool-capable flag
- life-support backend
- last successful/last known tool-capable backend
- unhealthy backend reasons + timestamps

