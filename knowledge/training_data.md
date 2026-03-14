# Training Pipeline & Data Generation

Full picture of the training process, what’s in place, and where things stand.

---

## Pipeline Overview

1. **Research** – Web search for transformer/model options (cloud-free via ddgs).
2. **Data generation** – Local Ollama (llama3.2) generates instruction–response pairs. No cloud cost.
3. **Output** – JSONL in `data/training_data/` ready for fine-tuning.
4. **Fine-tuning** – User runs training with Hugging Face, LLaMA-Factory, etc. Nova can help via `run_command`, `write_file`, and `run_build`.

---

## Step 1: Research

**Script:** `scripts/transformer_research.py`  
**Output:** `data/research_output/transformer_research_latest.md`

- Web search for transformer architectures, Hugging Face models, fine-tuning methods.
- Compiles findings into a markdown report.
- Run via: `spawn_subagent("transformer research", "scripts/transformer_research.py")` or with topic: `["my topic"]`.
- Use `get_subagent_output(agent_id)` or `read_file("data/research_output/transformer_research_latest.md")` for results.

---

## Step 2: Data Generation (Local Model)

**Script:** `scripts/generate_training_data.py`  
**Output:** `data/training_data/*.jsonl`  
**Model:** Ollama `llama3.2:latest` — all generation is local, no API cost.

- Generates instruction–response pairs in the background.
- Uses local Ollama only. Requires Ollama running.

**spawn_subagent:**
```
spawn_subagent("training data", "scripts/generate_training_data.py", ["topic description", "--count", "50"])
```

- **Args:** `[topic, "--count", N]`. Topic = domain/style. Count = number of pairs (default 20).
- **Optional:** `["--model", "llama3.2:latest"]` to override model.

**After completion:**
- `subagent_status(agent_id)` – check status
- `get_subagent_output(agent_id)` – stdout (includes output path)
- `read_file("data/training_data/training_data_latest.jsonl")` – latest JSONL

---

## Step 3: Output Format

Each line is JSON:
```json
{"instruction": "user question or request", "response": "assistant response"}
```

Compatible with Hugging Face Datasets, LLaMA-Factory, Axolotl, and similar pipelines.

---

## Step 4: Fine-Tuning (User / External Tools)

- Nova can outline the framework and pipeline.
- Actual training uses tools like Hugging Face `transformers` / `datasets`, LLaMA-Factory, or Axolotl.
- Nova can:
  - `write_file` – training scripts, configs
  - `run_command` – `pip install transformers datasets`, run training
  - `run_build` – install deps
  - `spawn_subagent` – long runs in the background

No dedicated fine-tuning script yet; workflows are composed from these tools.

---

## Where We Stand

| Stage           | Status | Notes                                                |
|----------------|--------|------------------------------------------------------|
| Research       | Done   | `transformer_research.py`, web search, markdown      |
| Data generation| Done   | `generate_training_data.py`, local Ollama, JSONL     |
| Output format  | Done   | Standard instruction–response JSONL                  |
| Fine-tuning    | Manual | Via run_command, write_file; no bundled script yet   |

- **Nova’s role:** Define framework and flow; trigger research and data scripts; assist with scripts and commands for fine-tuning.
- **Local-only path:** Research (ddgs) + data generation (Ollama) avoid cloud costs; fine-tuning runs on the user’s hardware.
