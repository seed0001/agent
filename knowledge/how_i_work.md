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

## 4. Memory System (5 Layers)

| Layer | What | Where | Decay |
|-------|------|-------|-------|
| **Immediate** | Current turn only | In-memory | Cleared after response |
| **Short-term** | Recent conversation | `data/profiles/{user}/short_term.jsonl` | FIFO eviction at 30; old → episodic |
| **Working** | Active task state | `data/profiles/{user}/working.json` | Manual clear |
| **Episodic** | Past sessions | `data/profiles/{user}/episodic.jsonl` | Power-law decay R=1/(1+t/τ)^α |
| **Profile** | Facts about the user | `data/profiles/{user}/profile.json` | Persistent |

### Memory decay (episodic & thoughts)
- Retention: R = strength / (1 + t/τ)^α (Jost power law)
- τ = 3600 s, α = 0.2
- Entries below ~5% retention are dropped from context

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
  add_immediate, add_short_term, satisfy("connection")
       ↓
  get_context_for_agent() → immediate, short-term, working, episodic, thoughts, profile
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
| Short-term | `data/profiles/{user}/short_term.jsonl` |
| Episodic | `data/profiles/{user}/episodic.jsonl` |
| Working | `data/profiles/{user}/working.json` |
| Profile | `data/profiles/{user}/profile.json` |
| Thoughts | `data/profiles/{user}/thoughts.jsonl` |
| Biology state | `data/profiles/{user}/biology_state.json` |
| Contacts | `data/profiles/{user}/contacts.json` |
| Access policy | `data/profiles/default/access_policy.json` |
| Image usage | `data/image_usage.json` |
| Knowledge base | `knowledge/*.md` |
