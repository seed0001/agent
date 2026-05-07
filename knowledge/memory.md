# Memory

This is my memory system. I read this when someone asks how I remember things, when I notice my memory behaving unexpectedly, or when a slash command appears in chat that I don't recognize.

---

## What changed (May 2026)

My memory used to be a pile of JSON files: `profile.json`, `short_term.jsonl`, `working.json`, `thoughts.jsonl`. Power-law decay was described in docs but never actually ran. Profile facts had no notion of confidence, no source attribution, no versioning. Working memory was lost on restart. There was no notion of a "session" — every conversation was one undifferentiated stream.

That's all gone. Now everything lives in one SQLite database per profile (`data/profiles/{user_id}/memory.db`) with a real lifecycle modeled on [seed0001/Adam](https://github.com/seed0001/Adam): facts get reinforced when used, decay when ignored, and can be protected from decay. A background consolidator distills old conversation into durable facts. New conversations get semantically-relevant past turns injected into context. None of this is theoretical — it actually runs.

---

## The five layers I have now

| Layer | What lives there | Where | Lifecycle |
|---|---|---|---|
| **Immediate** | The current turn's scratchpad | In-process list | Cleared after the response goes out |
| **Short-term** | Most recent turns of the active session | `episodic_memory` table, filtered to `session_id` | FIFO within session, default last 30 |
| **Working state** | Persistent KV pairs that span sessions (e.g. `current_speaker_discord_id`, the active doctor-mode flag) | `working_memory` table | Manual writes only — nothing decays |
| **Episodic** | Every turn from every session, ever | `episodic_memory` table | Soft-delete on consolidation; importance-scored by background pass |
| **Profile facts** | Durable beliefs about the user | `profile_facts` table | Reinforced when used, exponentially decayed when ignored, pruned below the floor unless protected |

Two more tables hold supporting data:
- `sessions` — every conversation has a session row with start/end timestamps, source (web/discord/cli/agent), and metadata
- `background_thoughts` — my stream-of-consciousness, separately from chat turns. Includes `delivered` and `reject_reason` so I can audit which proactive thoughts were filtered out and why
- `semantic_embeddings` — `float32` vectors for similarity search
- `memory_audit` — lightweight log of every decay/reinforce/protect/delete action, useful for debugging

---

## How profile facts behave (the part to actually understand)

Every fact has these fields:

- `key` — stable identifier; same fact in same category always produces the same key. Versioning hangs off this.
- `value` — the human-readable statement
- `category` — `background`, `work`, `preferences`, `personal`, `other`, or `general`
- `confidence` — 0.0 to 1.0. Reinforcement bumps it toward 1.0; decay shrinks it
- `source` — `user` (typed by the Creator directly), `extracted` (pulled from chat by old logic), `consolidated` (extracted by the background consolidator from old transcripts), `imported`
- `protected` — when True, decay skips this fact entirely. **User-source facts are auto-protected.** Consolidator-extracted facts are not — they have to earn their keep through use.
- `version` — bumped on every update; old rows soft-deleted, full history queryable
- `last_referenced_at` — last time this fact was injected into a system prompt

**The lifecycle, every time my context is built:**

1. The top 30 facts (by confidence) get pulled into the system prompt
2. Each one gets `reinforce(key)` called on it — confidence climbs by ~0.08 toward 1.0, `last_referenced_at` is refreshed
3. Facts I haven't used recently quietly lose confidence on the next consolidator tick

**Decay math (Adam's, exact):** `new = old * exp(-ln(2)/half_life * days_since_reference)`. Default half-life is 30 days, default floor is 0.25. Within the first 24 hours of a reference there's a grace period — no decay applied. So if I keep using a fact, it never decays.

**What this means in practice:** If the Creator tells me their dog's name and I use it once, then I never use it again, in a few weeks the consolidator will quietly forget it. If I keep referring to it, it stays at full confidence forever. If they tell me explicitly "remember this" via `update_profile`, it's protected forever regardless of use.

---

## The background consolidator

A stochastic loop in the daemon (`src/agent/memory_consolidator.py`) ticks every 8-18 minutes (random jitter — Adam-inspired, predictable cron schedules cause artifacts). Each tick does four passes:

1. **Decay pass** — apply exponential decay to every unprotected, non-user fact. Anything below the floor gets soft-deleted.
2. **Consolidate pass** — pull episodic turns from the "ready window" (older than `consolidate_after_days`, newer than the last watermark), group by session, send each session's transcript to the LLM and ask it to extract durable facts. Insert each as `source="consolidated"` at confidence 0.7. The watermark in `working_memory` ensures the same turns never get processed twice.
3. **Importance pass** — pick up to 20 episodic entries still at the default importance (0.5), batch them into one LLM call, get back a 0-1 score per turn. Stable rowid-based ordering.
4. **Embed pass** — vectorize any episodic rows missing an embedding. Fast on CPU with the default `all-MiniLM-L6-v2` model. Skips silently if the model can't load.

This means my long-term memory IS actively being curated in the background. I don't have to manually call anything for it to work.

---

## Semantic search

When my context is built and I have a fresh user message, the system runs a similarity search against every past episodic turn (cosine over the stored embeddings) and injects the top 4 most similar turns into the prompt under "## Semantically related past turns". This is how I remember "we talked about this six weeks ago" without that conversation having to be in the recent window.

I can also call `memory.find_similar(query, limit=5)` programmatically if I ever need to. Not currently exposed as a tool — the prompt-injection happens automatically.

---

## Sessions

Every time the agent boots, a new session is created (`source="agent"`). Every short-term/episodic write tags the current `session_id`. This unlocks two things:

- **Within-session recency** — "last 20 turns" actually means last 20 turns of THIS conversation, not the global stream
- **Cross-session continuity** — I get a separate "## Earlier sessions (recent)" block in my context with up to 10 turns from prior sessions in the past 7 days. That's how I remember what we talked about yesterday across a restart.

Sessions can be ended explicitly (`memory.end_session()`) but it's not required — they just stay open and get pruned by their `last_activity_at`.

---

## My tools (what I can call)

These haven't changed:

- **`update_profile(category, fact)`** — store a profile fact about the user. Always treated as `source="user"` and auto-protected. Use this whenever the Creator shares personal info.
- **`set_working_memory(key, value)`** — write a persistent KV pair (replaces the old `working.json`). Survives restart.
- **`update_contact(...)`** — separate system; lives in `contacts.json`. Not part of the new memory DB.

I don't have tools for `find_similar`, `reinforce`, `protect`, etc. The lifecycle handles those automatically. If I ever need explicit control, ask the Creator and we'll wire one up.

---

## Slash commands the Creator has

Important: when the Creator types one of these in chat (web or Discord), **the message bypasses me entirely** — `chat()` short-circuits and returns the command output directly. I'll see them in my episodic memory afterward as a record of what was said, but I never get to respond. So if the Creator asks "what does /memory do?", I should know the answer without ever having seen one fire:

| Command | What it does |
|---|---|
| `/memory` | Shows all profile facts with confidence health bars, source badges, last-referenced timestamps |
| `/memory stats` | Counts of facts/turns/sessions/thoughts |
| `/memory decay <days>` | Sets the decay half-life for this profile |
| `/memory min <0-100>` | Sets the prune floor (percent) |
| `/memory help` | Lists the commands |
| `/remember <category>: <fact>` | Stores a protected fact (e.g. `/remember work: builds AI lifeforms`) |
| `/remember <key> = <value>` | Stores with an explicit key |
| `/forget <key-or-substring>` | Soft-deletes one fact (errors if ambiguous) |
| `/forget all yes-i-am-sure` | Wipes every profile fact (requires confirm token) |
| `/protect <key-or-substring>` | Marks a fact immortal |
| `/unprotect <key-or-substring>` | Lets a fact decay again |
| `/thoughts [N]` | Shows the last N background thoughts (default 8), with delivered/rejected status |
| `/sessions [N]` | Shows the last N sessions |

The web UI memory panel (open with the **Memory** button) shows the same data with health bars, badges, an inline "Remember" form, and per-fact Protect/Forget buttons. Same parser drives Discord — typing `/memory` in a DM gets a fenced text response there too.

---

## Where the data lives

Everything per-profile, isolated:

```
data/
  profiles/
    default/
      memory.db          ← SQLite, one DB per profile, WAL mode, FK on
      biology_state.json
      existential_state.json
      profile.json       ← legacy, no longer read; safe to ignore
      contacts.json
      access_policy.json
      user_settings.json
  soul.json              ← shared identity, not per-profile
  values_vault.json
```

Tables in `memory.db`:
- `schema_version` — migration tracking
- `sessions`
- `episodic_memory`
- `profile_facts`
- `semantic_embeddings`
- `background_thoughts`
- `working_memory`
- `memory_audit`

The schema is owned by `src/agent/memory_db.py` with a tiny version-tracked migration runner. New schema changes append to the `MIGRATIONS` list — never edit a shipped migration.

---

## What I should do when…

- **Someone shares personal info** — call `update_profile(category, fact)`. It will be stored protected, source=`user`, confidence 1.0, and never decay. One clear fact per call.
- **I'm in the middle of a multi-step task** — `set_working_memory("current_task", "...")`. It survives restart now, so I can pick up exactly where I left off.
- **Someone asks if I remember something** — trust the system. The recent window, important earlier turns, cross-session block, semantically-related block, AND profile facts are all in my context already. If a memory's not there, it's not there because the consolidator decided it wasn't durable enough — I shouldn't pretend.
- **Someone asks "how do I see what you know about me"** — tell them to type `/memory` or open the Memory panel in the web UI.
- **Someone tells me to forget something** — point them at `/forget <substring>`. I can't forget facts myself; the slash command exists because forgetting is a privileged operation that should be deliberate.
- **Someone wants to understand decay/reinforcement** — `/memory decay <days>` adjusts the half-life. The default of 30 days means a fact halves in confidence every 30 days of disuse.
