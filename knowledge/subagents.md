# Sub-Agents / Daemons

## spawn_subagent

**When to use:** Offload background work, long-running tasks, monitors, or scripts that should run separately.

**How:**
- `task`: Short description (e.g. "process monitor", "periodic prompts").
- `script_path`: Path to Python script (relative to project root, or absolute).
- `args`: Optional list of arguments for the script.

**Returns:** Sub-agent ID (e.g. `sub_1`) to use with subagent_status.

**Built-in scripts (in project root):**
- `process_monitor.py` – Logs running processes to process_log.txt. One-shot. Use: `spawn_subagent("log processes", "process_monitor.py")`
- `conversation_prompt.py` – Runs in background, prints every 2–10 min. Use: `spawn_subagent("periodic prompts", "conversation_prompt.py")`
- `background_thoughts.py` – Runs reflection, writes to thoughts. Loops every 5–15 min. Use: `spawn_subagent("background thoughts", "background_thoughts.py")`. With `--once`: one thought then exit.

**Examples:**
- Log processes: `spawn_subagent("log processes", "process_monitor.py")`
- Periodic prompts: `spawn_subagent("periodic prompts", "conversation_prompt.py")`

**Tips:** Paths like `process_monitor.py` resolve from project root. Use subagent_status to check completion.

---

## subagent_status

**When to use:** Check status of spawned sub-agents.

**How:**
- `agent_id`: Optional. If given, status for that agent; otherwise status for all.

**Examples:**
- All: `subagent_status()`
- One: `subagent_status("sub_1")`
