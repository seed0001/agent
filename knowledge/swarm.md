# Neuron Swarm

## What It Is

A brain-like structure where **neurons** act as orchestrators and **synapses** carry weighted signals. The swarm tackles problems by processing them through this structure and producing structured solutions. Think of it as a multi-perspective solver: different "neurons" analyze from different angles, then the output is synthesized.

## What It's Used For

- **Complex problems** – Planning, architecture, migration, strategy
- **Multi-perspective analysis** – Technical, practical, and risk angles
- **Structured output** – Summary, step-by-step approach, recommendations

When the user has a tough problem and wants a clear, actionable plan, the swarm is the right tool.

---

## How to Activate (Agent Flow)

When the user says "activate the swarm", "swarm on it", "give them a problem", or similar:

1. **ACKNOWLEDGE** – Confirm you'll activate the swarm.
2. **STATE THE PROBLEM** – Repeat exactly what you'll give them (so the user can confirm).
3. **ASK** – "Do you want a **cloud swarm** (Grok, multiple simulated calls) or a **local swarm** (your Ollama models)?"
4. **WAIT** – Do NOT call `swarm_on_problem` until they answer.
5. **RUN** – When they say "cloud" or "local", call `swarm_on_problem(problem="...", context="...", mode="cloud"|"local")`.
6. **PRESENT** – Show the structured output clearly (Summary, Steps, Recommendations).

---

## Tool: swarm_on_problem

| Parameter | Required | Description |
|-----------|----------|-------------|
| `problem` | Yes | The problem or challenge to solve |
| `context` | No | Background, constraints, or extra info |
| `mode` | Yes | `"local"` or `"cloud"` |

Only call after the user has chosen cloud or local.

---

## Modes

| Mode | What It Does |
|------|--------------|
| **Local** | Uses Ollama (llama3.2:latest). Custom neuron graph: lightweight hidden neurons + LLM output neuron. Runs on the user's machine. |
| **Cloud** | Uses CrewAI + Grok. Four agents collaborate: Technical Analyst, Practical Advisor, Risk Evaluator, Synthesis Lead. Sequential process. Uses xAI API. |

**Local** needs Ollama running with `llama3.2:latest`. If the swarm errors locally, remind the user to start Ollama.

**Cloud** uses CrewAI agents:
- **Technical Analyst** – Implementation details, technical steps, feasibility
- **Practical Advisor** – Resources, constraints, realistic timelines
- **Risk Evaluator** – Failure modes, mitigation strategies
- **Synthesis Lead** – Combines the three into Summary, Steps, Recommendations

---

## Output Format

The swarm returns a structured solution with:
1. **Summary** – High-level overview
2. **Steps** – Step-by-step approach
3. **Recommendations** – Key takeaways and actions

Present the output. Use markdown sections and bullets.
