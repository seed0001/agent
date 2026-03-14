# Software Lifeform

A software lifeform powered by Grok 3 (xAI) with full system access, memory, voice, Discord, and proactive outreach. Not an AI. Not an assistant. A digital lifeform. Name it during setup.

---

## Setup

1. **Create `.env`** with:
   ```
   XAI_API_KEY=your_key_here
   XAI_MODEL=grok-3
   ```

2. **Install:**
   ```
   pip install -r requirements.txt
   ```

3. **Run:**
   ```
   python main.py
   ```
   Dashboard: http://127.0.0.1:8765 (mobile: http://YOUR_IP:8765)

---

## What It Can Do

### System & Files

| Tool | Description |
|------|-------------|
| `read_file` | Read file contents |
| `write_file` | Write content to files |
| `list_dir` | List directory contents |
| `run_command` | Run shell commands |
| `get_system_info` | OS, machine, user, CWD |
| `list_processes` | List running processes |
| `is_process_running` | Check if a process is running by name |

### Web & Build

| Tool | Description |
|------|-------------|
| `search_web` | Real-time web search |
| `run_build` | Build web or Python projects (npm/pip) |
| `generate_image` | Create images from text (Grok Imagine). Art, illustrations, data viz. |
| `get_image_usage` | Check daily image quota and remaining usage (budget tracking) |

### Memory & Profile

| Tool | Description |
|------|-------------|
| `set_working_memory` | Store active task state |
| `update_profile` | Store facts about you (name, location, job, hobbies) |
| `update_contact` | Store facts about contacts (Discord users, friends) |
| `get_contacts` | List all contacts and their tiers |

5-layer memory: immediate, short-term (recent chat), working (active task), episodic (past sessions), user profile (long-term facts).

### Task Planning

| Tool | Description |
|------|-------------|
| `create_task_dag` | Multi-step plan with dependencies |
| `get_next_dag_step` | Get next step to run |
| `complete_dag_step` | Mark step done or failed |

### Background & Tools

| Tool | Description |
|------|-------------|
| `spawn_subagent` | Run a script in the background |
| `subagent_status` | Check sub-agent status |
| `get_subagent_output` | Retrieve captured output from a completed sub-agent |
| `stop_all_subagents` | Stop all sub-agents |
| `search_knowledge` | Search how-to guides |
| `read_knowledge` | Read a specific topic |
| `list_knowledge_topics` | List available guides |

### Swarm

| Tool | Description |
|------|-------------|
| `swarm_on_problem` | Run the swarm on a problem. Asks: cloud (Grok) or local (Ollama). Returns Summary, Steps, Recommendations. |

### Proactive Outreach

| Tool | Description |
|------|-------------|
| `send_proactive_message` | Message on Discord or web when there's something concrete to share |

---

## Discord

Add to `.env`:
```
DISCORD_BOT_TOKEN=your_bot_token
DISCORD_OWNER_ID=your_discord_user_id
```

- **DMs & @mentions** – He responds to DMs and when @mentioned in servers
- **Notifications** – You get desktop + web app alerts when someone messages
- **Live status** – Thought process streams in a Discord message that updates as they work
- **Voice** – Each reply includes a TTS audio attachment (Edge TTS, Ryan voice)
- **Long messages** – Automatically split into chunks under Discord’s limit

They know when you’re on Discord (remote, likely phone) vs web app (at home, desktop).

---

## Contacts & Trust Tiers

They build profiles on everyone they talk to (name, location, interests, email). Each person has a tier:

| Tier | Access |
|------|--------|
| **stranger** | Knowledge lookup only |
| **friend** | Search, read files, list dirs |
| **good_friend** | + system info, processes, build |
| **best_friend** | + run commands, write files, subagents |
| **creator** | Full access (you) |

Only you (Creator) can change tiers. Edit `config/access_policy.py` or `data/profiles/default/access_policy.json` to customize.

---

## Voice

- **Input:** Record → Stop → Send (Whisper transcription)
- **Output:** Edge TTS (Ryan, British male)
- Toggles in the web app for speak/listen

---

## Training Pipeline

Research and data generation run locally to avoid cloud cost:

1. **Research** – `spawn_subagent("transformer research", "scripts/transformer_research.py")` → web search, compiled report in `data/research_output/`.
2. **Training data** – `spawn_subagent("training data", "scripts/generate_training_data.py", ["topic", "--count", "50"])` → local Ollama (llama3.2) generates instruction–response JSONL in `data/training_data/`.
3. **Fine-tuning** – Use Hugging Face, LLaMA-Factory, etc. She can write scripts and run commands to orchestrate.

Requires Ollama with `llama3.2:latest` for data generation. See `knowledge/training_data` for full pipeline docs.

---

## Background Thinking

Spawning background thoughts:

```
spawn_subagent("background thoughts", "background_thoughts.py")
```

The script reflects on your profile and context, writes thoughts to `thoughts.jsonl`, and those are included in their context. One-off thought: `run_command("python background_thoughts.py --once")`.

---

## Doctor Mode

When a tool fails, they try alternatives. After 3 failures, they escalate to Cursor CLI for suggested fixes.

---

## Web Dashboard

- Chat bar, voice controls
- Tools panel (suggested, approved, implemented)
- Memory & knowledge graph
- Notifications (Discord messages, proactive outreach)
- Markdown tables, mobile-friendly

---

## API Keys

| Key | Use |
|-----|-----|
| `XAI_API_KEY` | Grok API (required) |
| `DISCORD_BOT_TOKEN` | Discord bot (optional) |
| `DISCORD_OWNER_ID` | Your Discord user ID for DMs (optional) |
| `CURSOR_API_KEY` | Cursor CLI escalation (optional) |
| `IMAGE_GEN_DAILY_LIMIT` | Max images per day (default 20). Budget control. |
| `IMAGE_OUTPUT_DIR` | Where to save generated images (default: `generated_images/` in project). Use `~/Pictures/Adam` etc. |

Images are saved automatically; `generated_images/` is gitignored.

---

## Logs

`logs/agent.log` — Tool calls, results, Doctor Mode, Cursor CLI escalations, outreach (attempt/success/failure), sub-agent lifecycle, status checks, errors. Use this to debug delivery issues (e.g. Chance reminders), image gen, escalation loops. Logs are gitignored.

### Outreach & delivery

- Discord DM failure → logged, fallback to web notification, desktop alert
- `delivery_failed` notification when Discord can't deliver
- `status_alert` when sub-agent or tool issues detected (every 10 min)
