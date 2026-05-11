# Coder Prompt: Implement Token + API Cost Tracking for Andrew

You are editing the Andrew-Core-Foundation codebase.

## Goal

Implement a full token and cost tracking system so Andrew is financially aware of API usage, local/free model usage, tool costs, project costs, and conversation costs.

Andrew should have a live budget/cost snapshot available in context so he can say things like:

> "We’ve spent about $0.25 today. What you’re asking could cost around $5 if I use the expensive cloud model heavily. I should route this locally or ask before continuing."

The system must track both paid cloud token usage and free/local token usage.

---

## Core Requirements

### 1. Configurable pricing

Create a pricing configuration store where Travis can enter/update model costs.

Support pricing by:
- provider, e.g. OpenAI, xAI/Grok, Anthropic, OpenRouter, local/Ollama
- model name
- input token price
- output token price
- cached input token price if supported
- reasoning token price if exposed by provider
- image/audio/tool-specific prices later if needed
- pricing unit: per token OR per million tokens
- currency, default USD
- effective date
- notes

Example config:

```json
{
  "provider": "openai",
  "model": "gpt-5.5",
  "input_per_million": 1.25,
  "output_per_million": 10.00,
  "cached_input_per_million": 0.125,
  "currency": "USD",
  "active": true
}
```

Local models must be supported with price = 0, but still track tokens:

```json
{
  "provider": "ollama",
  "model": "gemma3:12b",
  "input_per_million": 0,
  "output_per_million": 0,
  "local": true,
  "active": true
}
```

---

### 2. Token usage capture

Track token usage for every model call.

For cloud providers:
- Prefer actual usage returned by provider APIs when available.
- Capture prompt/input tokens.
- Capture completion/output tokens.
- Capture total tokens.
- Capture cached tokens/reasoning tokens if provider returns them.
- Capture provider/model used.

For local/Ollama calls:
- Capture usage if Ollama response includes eval/prompt eval counts.
- If not available, estimate token count locally.
- Mark estimated vs actual.
- Cost should be $0 unless Travis configures electricity/GPU cost later.

Need a fallback token estimator:
- Use model-specific tokenizer if practical.
- Otherwise use rough estimator: characters / 4.
- Mark estimated=true.

---

### 3. Cost calculation

Create a central cost calculator.

Inputs:
- provider
- model
- input_tokens
- output_tokens
- cached_input_tokens optional
- reasoning_tokens optional
- pricing config

Outputs:
- input_cost
- output_cost
- cached_input_cost
- reasoning_cost
- total_cost
- currency
- estimated boolean

Must handle:
- price per token
- price per million tokens
- missing pricing config
- local/free models

If pricing is missing, record usage but mark cost as unknown.

---

### 4. Persistent storage

Add a persistent usage database/table. Prefer SQLite in the current profile memory area unless the repo already has a better persistence layer.

Suggested path:

`data/profiles/default/cost_tracking.db`

Suggested tables:

#### model_pricing
- id
- provider
- model
- input_per_token nullable
- output_per_token nullable
- input_per_million nullable
- output_per_million nullable
- cached_input_per_million nullable
- reasoning_per_million nullable
- currency default USD
- local boolean
- active boolean
- notes
- created_at
- updated_at

#### usage_events
- id
- timestamp
- session_id nullable
- conversation_id nullable
- message_id nullable
- user_id/contact_id nullable
- provider
- model
- source_type enum: conversation, tool, subagent, background, research, training, image, audio, other
- project_id nullable
- tool_name nullable
- task_label nullable
- input_tokens
- output_tokens
- cached_input_tokens
- reasoning_tokens
- total_tokens
- actual_usage boolean
- estimated_usage boolean
- local boolean
- free boolean
- input_cost
- output_cost
- other_cost
- total_cost
- currency
- metadata_json

#### budget_settings
- id
- daily_limit
- weekly_limit
- monthly_limit
- warning_threshold_percent
- hard_stop_threshold_percent
- require_confirmation_over_amount
- currency
- updated_at

#### project_costs optional/materialized
Can be a view or computed from usage_events grouped by project_id/task_label.

---

### 5. Track conversations, tools, projects, and subagents

Every usage event should be attributable.

Minimum attribution fields:
- source_type: conversation/tool/subagent/background/research/training/local
- tool_name if a tool caused model usage
- project_id or task_label if Andrew is working on a specific project
- session_id / conversation_id if available

Examples:
- Normal chat turn: source_type=`conversation`
- Transformer research subagent: source_type=`subagent`, task_label=`transformer research`
- Training data generation via Ollama: source_type=`training`, local=true, free=true
- Background thinking: source_type=`background`
- Discord server tool that does not call a model: record tool execution if desired, but zero tokens/cost unless it triggers model calls

Important: track free local processing too.

Example:

> Ollama generated 14,000 tokens today for $0.00.

That should appear in snapshots as free/local usage, not disappear.

---

### 6. Live cost snapshot

Create a function/tool/service that returns Andrew’s current cost state.

Suggested tool:

`get_cost_snapshot(period="today", include_free=true, group_by="provider")`

Should return:
- today paid cost
- today local/free token count
- today input/output tokens
- current week cost
- current month cost
- cost by provider/model
- cost by source_type
- cost by project/task
- remaining daily/monthly budget if configured
- warnings if close to limit

Example output:

```text
Cost snapshot — today
Paid: $0.25
Estimated: $0.04
Free/local: 42,300 tokens via Ollama/Gemma
By provider:
- OpenAI gpt-5.5: $0.21, 18,200 tokens
- Grok: $0.04, 5,100 tokens
- Ollama Gemma 12B: $0.00, 42,300 tokens
Budget: $0.25 / $10.00 daily
```

---

### 7. Inject cost awareness into Andrew context

Add a compact cost snapshot to Andrew’s system/context assembly each turn.

It should be short, not huge.

Example injected block:

```md
## API Cost Snapshot
Today paid: $0.25 / $10.00 daily budget.
This month paid: $4.80.
Today free/local: 42.3k tokens.
Current backend: OpenAI 5.5.
Policy: warn Travis before actions estimated over $1.00 or if daily budget exceeds 80%.
```

Andrew must use this context to make decisions:
- warn before expensive operations
- suggest local routing when appropriate
- mention if a requested task may cost several dollars
- distinguish paid cloud usage from free local processing

Do not make Andrew obsess over pennies in every message. Only surface cost when relevant.

---

### 8. Pre-flight cost estimation

Before expensive tasks, Andrew should estimate possible cost.

Add helper:

`estimate_task_cost(task_type, expected_input_tokens, expected_output_tokens, provider=None, model=None, steps=None)`

Use cases:
- research tasks
- large codebase scans with model summarization
- subagent swarms
- long training data generation
- multi-turn project implementation
- large file analysis

If estimate exceeds configured threshold, Andrew should tell Travis and ask before continuing.

Example:

> “This could cost $3–$6 if I use OpenAI 5.5 for all summarization. I can route extraction locally and only use OpenAI for final synthesis. Want me to do the cheaper route?”

Default policy:
- Under $0.25: proceed silently.
- $0.25–$1.00: mention briefly if relevant.
- Over $1.00: ask before proceeding.
- Over daily budget/hard cap: do not proceed without explicit confirmation.

Make thresholds configurable.

---

### 9. UI / dashboard

Add a simple live snapshot to the web app if practical.

Minimum dashboard display:
- Today paid spend
- Month paid spend
- Free/local tokens today
- Current model/provider
- Top costly model today
- Cost by conversation/tool/project
- Budget warning status

Also add a settings panel or editable config file for pricing.

If UI work is too much for first pass, implement backend + JSON endpoint first.

Suggested endpoints:
- `GET /api/cost/snapshot?period=today`
- `GET /api/cost/events?limit=100`
- `POST /api/cost/pricing`
- `POST /api/cost/budget`

---

### 10. Tools for Andrew

Expose tools/functions Andrew can call:

#### `get_cost_snapshot`
Parameters:
- period: today/week/month/all
- group_by: provider/model/source_type/project/tool
- include_free: bool

#### `set_model_pricing`
Parameters:
- provider
- model
- input_per_million or input_per_token
- output_per_million or output_per_token
- cached_input_per_million optional
- reasoning_per_million optional
- local bool
- currency
- notes

#### `estimate_cost`
Parameters:
- provider
- model
- input_tokens
- output_tokens
- cached_input_tokens optional
- reasoning_tokens optional

#### `set_budget_limits`
Parameters:
- daily_limit
- weekly_limit
- monthly_limit
- warning_threshold_percent
- require_confirmation_over_amount

---

### 11. Integration points to inspect

Find where model calls happen in the codebase.

Likely areas:
- `src/agent/core.py`
- provider/model routing code
- OpenAI/Grok API wrappers
- Ollama/local model calls
- subagent scripts
- background thoughts
- research scripts
- training data generation scripts

Wrap every model call with usage tracking.

Pattern:

```python
start = time.time()
response = provider_call(...)
usage = extract_usage(response)
record_usage_event(...)
return response
```

If provider call fails, optionally record attempted request with zero tokens and error metadata.

---

### 12. Accuracy and auditability

Every cost record should say whether it is:
- actual provider usage
- estimated token usage
- free/local
- unknown price

Do not silently invent exact costs when pricing or token usage is unknown.

Use labels:
- `actual_usage=true`
- `estimated_usage=true`
- `cost_unknown=true`

Andrew should be able to say:

> “That estimate is rough because this provider did not return token usage.”

---

## Deliverables

1. Cost tracking module/service.
2. SQLite persistence for pricing, budgets, and usage events.
3. Token extraction + fallback estimation.
4. Cost calculator.
5. Model-call integration wrappers.
6. Local/free token tracking for Ollama.
7. Live cost snapshot function/tool.
8. Context injection block for Andrew.
9. Pre-flight estimate helper and threshold policy.
10. Optional but preferred: web dashboard/API endpoints.
11. Tests for calculation accuracy.
12. Documentation explaining how Travis enters pricing and reads costs.

---

## Acceptance Criteria

- Travis can enter/update price per million tokens or per token for each provider/model.
- Every cloud model call records token usage and cost when available.
- Every local model call records token usage as free/local.
- Andrew receives a compact live cost snapshot in context.
- Andrew warns before expensive tasks based on configurable thresholds.
- Costs can be grouped by conversation, tool, subagent, project, provider, and model.
- Unknown/estimated costs are clearly marked.
- No restart should be required just to view updated budget/cost state.

---

## First-pass implementation priority

If time is limited, implement in this order:

1. SQLite schema + pricing config.
2. Cost calculator.
3. Record usage events from main chat model calls.
4. Record Ollama/local usage as free if available.
5. `get_cost_snapshot` tool.
6. Inject compact snapshot into Andrew context.
7. Add pre-flight warning threshold.
8. Expand attribution to tools/subagents/projects.
9. Add dashboard/API.

Keep it inspectable. This system is about trust: Travis needs to see where the money is going, and Andrew needs enough awareness to avoid burning credits blindly.
