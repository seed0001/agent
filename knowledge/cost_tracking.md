# Cost Tracking

Track token usage and API spend across cloud and local calls.

## What it tracks

- Cloud usage (prompt/output/total tokens) from provider response usage when available.
- Local/Ollama usage (prompt eval + eval tokens) as free/local usage.
- Cost using configurable model pricing.
- Attribution fields: source type, tool name, task label, session/conversation ids.

## Persistence

- Database path: `data/profiles/default/cost_tracking.db`
- Tables:
  - `model_pricing`
  - `usage_events`
  - `budget_settings`

## Tools

- `get_cost_snapshot(period, group_by, include_free)`
- `set_model_pricing(provider, model, ...)`
- `estimate_cost(provider, model, input_tokens, output_tokens, ...)`
- `set_budget_limits(...)`

## API endpoints

- `GET /api/cost/snapshot?period=today`
- `GET /api/cost/events?limit=100`
- `POST /api/cost/pricing`
- `POST /api/cost/budget`

## Budget behavior

- Budget settings support warning and confirmation thresholds.
- Unknown pricing does not invent fake costs; events are marked cost unknown.
- Local/Ollama defaults to free usage unless pricing is explicitly configured.

