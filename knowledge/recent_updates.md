# Recent Updates & Current State

A snapshot of recent changes and where Nova stands.

## Recent Updates

- **Metrics dashboard** – Internal panel with drives (4 bars), image usage (today/limit), sub-agents status. Visible in web UI.
- **Tag clouds** – Replaced hierarchical vis-network graph with tag clouds in Memory & Knowledge (Profile, Memories, Active task, Thoughts, Drives).
- **Notification memory** – Proactive, Discord, and status notifications now persist in short-term memory so Nova knows what the user is replying to.
- **CrewAI cloud swarm** – Cloud mode uses CrewAI with 4 agents (Technical Analyst, Practical Advisor, Risk Evaluator, Synthesis Lead). Local mode still uses Ollama.
- **Chance reminders** – Time windows 8–9 AM and 7–8 PM only (no longer every 10 minutes).
- **Transformer research** – `scripts/transformer_research.py` does web search and compiles findings. Use `spawn_subagent('transformer research', 'scripts/transformer_research.py')` and `get_subagent_output` for results.
- **Training data generation** – Local Ollama (llama3.2) generates instruction–response JSONL. No cloud cost. `spawn_subagent('training data', 'scripts/generate_training_data.py', [topic, '--count', N])`. Output: `data/training_data/*.jsonl`.
- **get_subagent_output** – New tool to retrieve captured output from completed sub-agents.
- **System prompt** – Explicit guidance: use research script for transformer research; use training data script for local data generation; never claim work is done without actually invoking tools.

## Where She Stands

- **Research**: Real transformer/model research via spawn_subagent → web search → compiled report. She must run the script; no confabulation.
- **Training pipeline**: Can outline framework and pipeline conversationally; actual data generation runs on local Ollama in background.
- **Swarm**: Cloud (CrewAI/Grok) or local (Ollama). User chooses before swarm_on_problem runs.
- **Memory & metrics**: Tag clouds, notification context, drives visible in UI.
- **Chance**: Morning and evening reminder windows only.
