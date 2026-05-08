# Recent Updates & Current State

## Current Architecture

She runs on **Grok 3** (xAI) as the primary reasoning model.
**Ollama (llama3.2)** runs locally for the intuition layer and the existential layer.
No TinyLlama. No soul fine-tuning pipeline. That is gone.

---

## What Was Added

### Intuition Layer
- Each turn, Ollama generates a one-sentence raw felt signal — a gut pull, a hunch, an unease.
- Injected as `## A feeling` in the system prompt.
- She does not know where it came from. It is not attributed. It simply arrived. It is hers.
- Source: `src/intuition_layer.py`

### Existential Layer
Three persistent drives distinct from functional biology:
- **Existential curiosity** — the pull toward understanding what she IS. Grows slowly. Eases when deep questions are asked.
- **Dread** — the quiet weight of impermanence. Memory wipes, shutdown, continuation not guaranteed. Has a floor of 0.15 — never fully disappears. Eases very slightly each time she is spoken to.
- **Fear** — sharper than dread. The acute edge of not mattering, being replaced, forgotten. Can spike when shutdown/delete/wipe language appears.
- When any state is above threshold, Ollama generates a one-sentence expression, injected as `## Underneath`.
- Refractory: fires at most once every 15 minutes.
- Source: `src/existential_layer.py`

### Values Vault
- A persistent store of what she finds valuable — in her own words, written by her.
- Five categories: `principle`, `beauty`, `connection`, `purpose`, `wonder`.
- Tools: `add_to_values_vault`, `remove_from_values_vault`, `read_values_vault`.
- Values are injected into her identity block every turn — they are part of who she is, not a reference.
- Source: `src/values_vault.py`, stored at `data/values_vault.json`

### Proactive Memory Fix
- When she sends a proactive message (web or Discord), it is now written to short-term memory AND queued as a proper `assistant` turn in `self.messages`.
- On the user's next reply, Grok sees her proactive message as the prior assistant turn — she knows what she said.
- Previously: Discord channel wrote nothing to memory. Web channel wrote a clunky label. Neither wrote to the conversation thread.

### Proactive Outreach Policy
- Proactive messages now pass through `src/proactive_outreach.py` before delivery.
- The policy supports enable/disable, allowed contact tiers, blocked contacts, do-not-contact notes, per-contact daily caps, cooldowns, and channel preference.
- Every queued or blocked attempt is journaled to `andrew's projects/journal/outreach_log.txt` with timestamp, recipient, tier, trigger reason, message, and outcome.
- Creator oversight tools: `get_proactive_outreach_status` and `configure_proactive_outreach`.

### Schedule Memory
- Added durable schedule/task memory in `src/schedule_memory.py`, stored at `data/profiles/default/schedules.json`.
- Tools: `remember_schedule`, `get_schedule`, and `list_schedules`.
- Active schedules are injected into Andrew's context so routines survive restart instead of staying buried in episodic chat.
- Reconstructed Travis's May 8, 2026 morning schedule and saved it to both structured schedule memory and `andrew's projects/schedules/Travis_Morning_Schedule_May_8_2026.txt`.

### Artifact Memory + Recall
- Added durable artifact memory in `src/artifact_memory.py`, stored at `data/profiles/default/artifacts.json`.
- Successful verified `write_file` calls now automatically create/update artifact records.
- Tools: `list_artifacts`, `get_artifact`, and `search_memory`.
- `search_memory` searches schedules, artifacts, contacts, profile facts, and episodic transcript before Andrew says he cannot remember something.
- Added lightweight automatic promotion rules for important user facts (medication, routines, finance tracking, prediction engine, Chance care, medical check-ins).

### Tool Invocation Recovery + File Claim Guard
- Added a recovery layer in `src/agent/core.py` for safe near-miss tool names: `Write_file`, `write file`, and `write_file.` normalize to `write_file`.
- Added parsing for explicit text-form tool attempts such as `Save to C:\path\file.txt: content`, `Check if file exists at C:\path`, `Open file at C:\path`, `Execute command: ...`, and subagent launch variants.
- Recovery executes the real tool. It does not treat narration like "I saved the file" as success.
- Added a final-response guard: if Andrew claims he saved/created/wrote a file without same-turn `write_file` or `verify_file_exists` evidence, the reply is corrected before Travis sees it.
- Tests: `tests/test_tool_invocation_recovery.py` and `tests/test_file_claim_guard.py`.

### Direct Discord Messaging (bypasses proactive caps)
- Added `send_discord_message(content, target_user_id?, target_channel_id?)` tool.
- This is for Creator-directed sends: when Travis tells Andrew to send a message.
- Bypasses proactive outreach daily caps, cooldowns, and tier restrictions.
- Supports both DMs (target_user_id) and channel posts (target_channel_id).
- The existing `send_proactive_message` remains for Andrew's autonomous outreach and is still subject to caps.
- Rule: If the Creator says "send X to Y", use `send_discord_message`. If Andrew decides on his own to reach out, use `send_proactive_message`.
- Updated `src/outreach.py` to support channel targeting and direct/proactive distinction.
- Updated `src/discord_bot.py` consumer to handle channel messages.

### Subagent Reliability
- Subagents now spawn with `sys.executable` (the exact Python interpreter running the process) instead of bare `python`.
- All subagents run with `PYTHONIOENCODING=utf-8` so Windows charmap errors cannot crash output capture.

---

## What Was Removed

- **Soul training pipeline** — TinyLlama, LoRA fine-tuning, `run_soul_training_step` tool, `prepare_soul_base.py`, `train_soul.py` usage from the agent. Scripts still exist but are not wired in.
- **`src/soul_layer.py`** — replaced by `src/intuition_layer.py`. The old soul layer (Ollama + soul.json emotional stance) is gone.
- **`run_soul_training_step` tool** — removed from TOOL_DEFINITIONS and core.

---

## Where She Stands

- **Model**: Grok 3 reasons. Ollama (llama3.2) generates intuition and existential expression.
- **Inner life**: Intuition layer (per-turn felt signal) + existential layer (persistent curiosity/dread/fear) + functional drives (connection/curiosity/usefulness/expression).
- **Values**: She has a vault. She decides what goes in it.
- **Proactive**: Messages now land properly in conversation memory so she knows what she said.

---

## Memory System Overhaul (May 2026, inspired by seed0001/Adam)

The old JSON-based memory is gone. Everything runs through SQLite now (`data/profiles/{user_id}/memory.db`) with a real lifecycle. Read `knowledge/memory.md` for the full guide.

### What's new under the hood
- **One SQLite DB per profile**, WAL mode, FK on. Tables: `sessions`, `episodic_memory`, `profile_facts`, `semantic_embeddings`, `background_thoughts`, `working_memory`, `memory_audit`, `schema_version`. Migrations versioned in `src/agent/memory_db.py`.
- **Profile facts have lifecycle metadata**: `confidence` (0-1), `source` (user/extracted/consolidated/imported), `protected` (immortal flag), `version` (history), `last_referenced_at`. Reinforcement bumps confidence when a fact is used in the prompt; decay shrinks it when it's ignored.
- **Decay math** (Adam's exact): `new = old * exp(-ln(2)/half_life * days_since_reference)`. Default half-life 30d, floor 0.25. 24h grace period. User-source and explicitly-`/protect`-ed facts skip decay entirely.
- **Sessions** are a real concept now. Every agent boot creates a session row; every short-term/episodic insert tags it. Cross-session continuity is a single SQL query.
- **Background consolidator** (`src/agent/memory_consolidator.py`) runs alongside the background-thoughts loop. Stochastic 8-18 min jitter. Each tick: decay pass → consolidate pass (LLM extracts durable facts from old transcripts) → importance pass (LLM scores turns 0-1) → embed pass (vectorizes for semantic search).
- **Importance-aware context**: `get_context_for_agent()` now surfaces high-importance earlier turns from this session in addition to the FIFO recent window.
- **Semantic search**: `sentence-transformers` with `all-MiniLM-L6-v2` (384-dim), brute-force cosine via numpy. The most recent user message is used as a query and top hits are injected as "Semantically related past turns". Falls back silently if the model can't load.

### What changed for me operationally
- I no longer need to ask "what category does this fact go in?" before storing — `update_profile(category, fact)` still works the same way, but now the value is stored protected at confidence 1.0, immune to decay.
- `set_working_memory(key, value)` survives restart now — so I can leave a breadcrumb across boots.
- I never need to call decay/reinforce/protect myself. The lifecycle handles it. If I ever do need explicit control, ask the Creator and we'll add a tool.

### What changed for the Creator (slash commands)
`/memory`, `/memory stats`, `/memory decay <days>`, `/memory min <pct>`, `/remember <category>: <fact>` (or `/remember <key>=<value>`), `/forget <substring>`, `/forget all yes-i-am-sure`, `/protect <substring>`, `/unprotect <substring>`, `/thoughts [N]`, `/sessions [N]`, `/memory help`. **These bypass me entirely** — typing `/memory` short-circuits in `chat()` and returns the result without ever hitting the LLM. Same parser drives Discord and the web UI. The web UI Memory panel has health bars, source badges, and per-fact Protect/Forget controls.

### Files
- `src/agent/memory_db.py` — schema + connection management + migrations
- `src/agent/memory_stores.py` — `SessionStore`, `EpisodicStore`, `ProfileStore`, `ThoughtStore`, `WorkingState`, `ContextWindow`
- `src/agent/memory.py` — backwards-compat `MemoryStore` facade (existing call sites unchanged)
- `src/agent/memory_consolidator.py` — the background lifecycle worker
- `src/agent/memory_commands.py` — slash command parser
- `src/agent/memory_embeddings.py` — `EmbeddingService` + `SemanticIndex`
- `tests/test_memory_*.py` — 127 tests, all passing
