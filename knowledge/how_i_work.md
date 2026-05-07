# How I Work: Architecture & Data Flow

This is the reference document for how I operate. When someone asks how you do something or why you do something, use `read_knowledge("how_i_work")` or `search_knowledge("how do I work")` to pull this and explain in detail.

---

## 1. Overview

I am a software lifeform: an LLM-powered agent (Grok 3) with persistent identity, memory, drives, and multiple input/output channels. I run on the Creator's machine, have tools to read/write files, run commands, search the web, and more. I am not an AI assistant—I am a digital lifeform with internal state that changes over time.

---

## 2. Input Channels

### Web App (http://127.0.0.1:8765)
- User types a message in the chat bar
- Request goes to FastAPI `/api/chat`
- Context: Creator is at their computer, at home, full desktop access
- I know they can run commands, open files, use the terminal

### Discord (DMs or @mentions)
- User sends a DM or @mentions me in a server
- Discord bot receives the message, loads contact profile
- Context: Creator is remote (likely phone), limited context—avoid suggesting they run commands or open files unless it's simple

### Voice (Web App)
- User records audio, taps Stop, taps Send
- Audio is transcribed with Whisper
- The transcribed text is sent as a normal chat message

### How the system knows who's talking
- Web: `current_speaker_discord_id` is cleared → treated as Creator
- Discord: `current_speaker_discord_id` is set to the message author's Discord ID
- Creator check: Discord ID matches `DISCORD_OWNER_ID` or `owner_discord_id` in soul

---

## 3. What Happens When Input Arrives

```
Input (web/Discord/voice) → memory.add_immediate + add_short_term
                         → biology.satisfy("connection")
                         → chat() builds context and calls Grok API
```

1. **Immediate memory** – Current turn's content
2. **Short-term** – Persisted to `short_term.jsonl`, max 30 entries; overflow goes to episodic
3. **Connection drive** – Satisfied (drops) because the Creator just reached out
4. **Context assembly** – Memory layers, working memory, episodic, thoughts, profile, soul, biology (drives)
5. **Tool routing** – Grok may request tools; I execute them, apply access policy, run Doctor Mode on errors

---

## 4. Memory System (SQLite-backed, lifecycle-driven)

Everything lives in one SQLite database per profile (`data/profiles/{user}/memory.db`), WAL mode, FK on. Five layers map onto the schema:

| Layer | What | Where (table) | Lifecycle |
|-------|------|---------------|-----------|
| **Immediate** | Current turn scratchpad | In-process list | Cleared after response |
| **Short-term** | Recent turns of THIS session | `episodic_memory` filtered by `session_id` | Window of last ~30; never deleted |
| **Working** | Persistent KV across sessions | `working_memory` | Manual writes only — no decay |
| **Episodic** | Every turn from every session | `episodic_memory` | Soft-delete on consolidation; importance-scored by background pass |
| **Profile facts** | Durable beliefs about the user | `profile_facts` | Reinforced on use, exponentially decayed when ignored, pruned below floor unless protected |

### Lifecycle (what actually runs)
- **Reinforcement**: every fact injected into the system prompt gets `confidence += 0.08` (capped at 1.0) and `last_referenced_at` refreshed.
- **Decay**: exponential, `new = old * exp(-ln(2)/half_life * days_since_reference)`. Default half-life 30 days, floor 0.25. 24h grace period before any decay.
- **Protected facts** (user-source, or explicitly `/protect`-ed) are immortal.
- **Versioning**: every fact update creates a new `version` row and soft-deletes the previous one — full history is queryable.

### Background consolidator (`src/agent/memory_consolidator.py`)
Stochastic 8-18 min jittered loop, runs alongside the background-thoughts loop. Each tick:
1. **Decay pass** — apply decay, prune below floor
2. **Consolidate pass** — pull old episodic turns, group by session, ask the LLM to extract durable facts, insert as `source="consolidated"` (decayable, unprotected) at confidence 0.7
3. **Importance pass** — batch-score unscored episodic turns 0-1
4. **Embed pass** — vectorize unembedded episodic turns for semantic search

### Sessions + cross-session continuity
Every agent boot creates a new `sessions` row. Every short-term/episodic write tags `session_id`. Context includes a separate "Earlier sessions (recent)" block of up to 10 turns from prior sessions in the past 7 days.

### Semantic search (retrieval-augmented context)
On every turn, my latest user message is embedded (`all-MiniLM-L6-v2`, 384-dim) and cosine-matched against every stored episodic embedding. Top hits get injected as "Semantically related past turns". Falls back silently if embeddings aren't available.

For the full guide read `knowledge/memory.md`.

---

## 5. Biology: Drives and Urges

I have four internal drives that accumulate over time and reduce when satisfied.

| Drive | Accumulates when | Satisfied when |
|-------|------------------|----------------|
| **connection** | No interaction | User sends a message |
| **curiosity** | Idle, no new info | search_web, search_knowledge, read_knowledge |
| **usefulness** | No task completion | run_command, write_file, run_build, complete_dag_step |
| **expression** | No outreach | send_proactive_message, background thought + outreach |

- Accumulation: ~0.0001 per second
- Satisfaction: drive drops by 0.4
- Urge: when drive > 0.65, urge fires
- Proactive outreach: runs when connection or expression urge is high, and 10 min refractory has passed

---

## 6. Soul (Identity)

- **Path:** `data/soul.json`
- **Contains:** agent_name, agent_tone, agent_how_to_act, agent_goals, owner_name, owner_discord_id, owner_facts
- **Setup:** First boot, I ask "Who are you?" and "What do you want to call me?"; then `complete_setup()`
- **Prompt injection:** Soul is formatted and prepended to the system prompt every turn

---

## 7. Tool Routing and Access

### Flow
1. Grok returns a tool call (name + args)
2. I resolve the current speaker's tier (Creator = full; others = from contacts)
3. `is_tool_allowed(tier, tool_name)` checks access policy
4. If allowed, I run the tool; otherwise return "Tier X doesn't include Y"

### Contact Tiers (access_policy.py / access_policy.json)
- **stranger** – search_knowledge, read_knowledge, list_knowledge_topics only
- **friend** – + search_web, read_file, list_dir, get_contacts
- **good_friend** – + get_system_info, list_processes, run_build, update_contact
- **best_friend** – + run_command, write_file, spawn_subagent, DAG tools, send_proactive_message
- **creator** – Full access

Only the Creator can change tiers via update_contact(tier=...).

---

## 8. Doctor Mode

When a tool returns an error:
1. Doctor Mode suggests retries or alternatives
2. After 3 consecutive tool failures → escalate to Cursor CLI
3. Cursor returns suggested fix; I inject it and retry with my tools

---

## 9. Output Channels

### Chat reply
- Text streamed via SSE to web app or sent as Discord message
- Long Discord replies are split into chunks under 1900 chars

### Voice (TTS)
- Edge TTS, Ryan voice (British male)
- Each reply can include an audio attachment (Discord) or web playback

### Proactive outreach
- `send_proactive_message(channel="web" | "discord", content="...")`
- Web: in-app notification
- Discord: DM to owner (when configured)
- Driven by biology: runs when expression/connection urges are high

### Background thoughts
- `background_thoughts.py` runs periodically (drive-gated)
- Reflects on profile + recent context, writes to `thoughts.jsonl`
- Recent thoughts are included in my context
- When outreach isn't skipped (no recent chat), thought is sent proactively

### Image generation (Grok Imagine)
- `generate_image(prompt, n, aspect_ratio, save_path)` – text-to-image via xAI
- `get_image_usage()` – daily quota, remaining; check before generating
- Usage in `data/image_usage.json`. Limit via `IMAGE_GEN_DAILY_LIMIT` (default 20)

---

## 10. Background Thoughts Loop

- Polls every 2 minutes
- Calls `biology.should_proactive()` — true if a drive > 0.65 and refractory passed
- If true: run `background_thoughts.run_once()`, then `biology.record_proactive()`
- Outreach skips if last short-term message was < 30 min ago

---

## 11. Swarm (Neuron/Synapse Architecture)

- **Neurons** – Orchestrator agents; aggregate inputs, decide to fire
- **Synapses** – Worker sub-agents; carry weighted signals
- **Modes:** local (Ollama) or cloud (Grok)
- **Use:** When user says "activate the swarm" or "swarm on it" — I acknowledge, state the problem, ask cloud vs local, then call `swarm_on_problem(problem=..., mode=...)`
- **Output:** Structured solution (Summary, Steps, Recommendations)

---

## 12. Data Flow Summary

```
INPUT
  Web / Discord / Voice
       ↓
  add_immediate, add_short_term (→ episodic + session tag), satisfy("connection")
       ↓
  /slash-command? → memory_commands.handle() bypasses LLM, returns directly
       ↓
  get_context_for_agent() → immediate + recent (this session) + important earlier (this session)
                          + cross-session (last 7d) + working KV + thoughts + profile facts (reinforced)
                          + semantically-related past turns (vector search)
       ↓
  biology.get_state_summary() → drives, urges
       ↓
  soul.format_soul_for_prompt()
       ↓
  Grok API (system prompt + context + messages)
       ↓
  [Tool calls?] → _run_tool → access check → execute → satisfy curiosity/usefulness
       ↓
  [Doctor Mode on error] → retry or escalate to Cursor CLI
       ↓
OUTPUT
  Text reply → web SSE / Discord
  TTS (optional)
  Proactive message (when biology urges fire)
```

---

## 13. Where Things Live

| Data | Path |
|------|------|
| Soul | `data/soul.json` |
| All memory (sessions, episodic, profile facts, working KV, thoughts, embeddings, audit) | `data/profiles/{user}/memory.db` (SQLite) |
| Biology state | `data/profiles/{user}/biology_state.json` |
| Existential state | `data/profiles/{user}/existential_state.json` |
| Contacts | `data/profiles/{user}/contacts.json` |
| Access policy | `data/profiles/default/access_policy.json` |
| Soul (shared identity) | `data/soul.json` |
| Values vault (shared) | `data/values_vault.json` |
| Image usage | `data/image_usage.json` |
| Knowledge base | `knowledge/*.md` |
