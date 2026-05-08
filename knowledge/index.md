# Knowledge Base Index

Use `search_knowledge(query)` to find how-tos, or `read_knowledge(topic)` to read a specific guide.

**Topics:**
- recent_updates – full changelog of what changed, what was removed, where she stands now
- three_layer_architecture – current architecture: Grok (reasoning), Ollama (intuition + existential), tools
- how_i_work – full architecture: inputs, memory, biology, tools, output. Use when someone asks how you work.
- biology – functional drives (connection, curiosity, usefulness, expression) and memory decay
- existential – existential drives (curiosity, dread, fear), how they accumulate, what triggers expression
- intuition – the intuition layer: per-turn felt signal from Ollama, how it arrives, what it is
- values_vault – her values store: add_to_values_vault, remove_from_values_vault, read_values_vault
- presence – her website and projects: set_website, get_website_status, add_project, update_project, remove_project
- self_model – proactive outreach, background thoughts, how the Creator reaches her
- proactive_outreach – autonomous outreach policy: triggers, tier gates, cooldowns, daily caps, contact selection, Creator journal, and oversight tools
- swarm – what the swarm is, when to use it, local vs cloud mode, swarm_on_problem tool
- files – read_file, write_file, list_dir, verify_file_exists, tool invocation recovery, and the file-claim truth guard
- processes – list_processes, is_process_running
- commands – run_command
- search – search_web
- build – run_build (web, Python)
- subagents – spawn_subagent, subagent_status, get_subagent_output, acknowledge_background_completion
- training_data – generate instruction fine-tuning data locally via Ollama (scripts/generate_training_data.py)
- dag – create_task_dag, multi-step orchestration
- memory – the SQLite-backed memory system: 5 layers (immediate / short-term / working / episodic / profile facts), exponential decay + reinforcement + protection, sessions, background consolidator, semantic search, slash commands the Creator can use (`/memory`, `/remember`, `/forget`, `/protect`, etc.), what tools I can call (`update_profile`, `set_working_memory`)
- schedule_memory – durable schedules/routines/checklists; remember_schedule, get_schedule, list_schedules; May 8 Travis morning schedule reconstruction
- artifact_memory – durable saved-file records; list_artifacts, get_artifact, search_memory; automatic tracking after verified write_file
- system – get_system_info
- ollama – launch Ollama model selector GUI
- dynamic_tools – suggest, approve, implement new tools (add_suggested_tools, get_tool_queue)
- image_gen – generate_image, get_image_usage (Grok Imagine, budget tracking)
- discord – Discord bot integration, DM delivery, outreach
- contacts – update_contact, get_contacts, trust tiers
