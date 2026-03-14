"""
CrewAI-based cloud swarm: multi-agent analysis with Grok.

When mode=cloud, swarm_on_problem uses this instead of the raw run_cloud.
Local mode (Ollama) stays unchanged.
"""
from src.swarm.signal import Signal

from config.settings import XAI_API_KEY, XAI_BASE_URL, XAI_MODEL


def _grok_llm():
    """LLM for xAI Grok (OpenAI-compatible)."""
    from crewai import LLM
    return LLM(
        model=XAI_MODEL,
        base_url=XAI_BASE_URL,
        api_key=XAI_API_KEY,
        temperature=0.5,
    )


async def run_crew_cloud(problem: str, context: str, prompt_prefix: str = "") -> Signal:
    """
    Run the CrewAI swarm on the cloud (Grok).

    problem: The problem to solve
    context: Optional background/constraints
    prompt_prefix: Prepended to synthesis task for format guidance

    Returns Signal with structured solution.
    """
    if not problem.strip():
        return Signal(
            type="response",
            content="[Crew swarm] No problem specified.",
            strength=1.0,
            metadata={"source": "crew_swarm"},
        )

    if not XAI_API_KEY:
        return Signal(
            type="response",
            content="[Crew swarm] XAI_API_KEY not set. Cloud swarm requires xAI API.",
            strength=1.0,
            metadata={"source": "crew_swarm"},
        )

    try:
        crew = _create_crew_with_inputs(problem, context, prompt_prefix)
        result = await crew.akickoff(inputs={"problem": problem, "context": context or ""})
        text = getattr(result, "raw", None) or str(result)
        text = (text or "").strip()

        return Signal(
            type="response",
            content=text,
            strength=1.0,
            metadata={"source": "crew_swarm"},
        )
    except Exception as e:
        return Signal(
            type="response",
            content=f"[Crew swarm error]: {e}",
            strength=1.0,
            metadata={"source": "crew_swarm"},
        )


def _create_crew_with_inputs(problem: str, context: str, prompt_prefix: str = ""):
    """Build crew with problem/context injected into task descriptions."""
    from crewai import Agent, Crew, Process, Task

    llm = _grok_llm()

    technical_analyst = Agent(
        role="Technical Analyst",
        goal="Identify implementation details, technical steps, and feasibility",
        backstory="You are a senior engineer who breaks down problems into concrete technical actions.",
        llm=llm,
        verbose=False,
    )

    practical_advisor = Agent(
        role="Practical Advisor",
        goal="Assess resources, constraints, and realistic timelines",
        backstory="You are a pragmatic project lead who considers time, cost, and human factors.",
        llm=llm,
        verbose=False,
    )

    risk_evaluator = Agent(
        role="Risk Evaluator",
        goal="Identify risks, failure modes, and mitigation strategies",
        backstory="You are a risk analyst who asks 'what could go wrong?' and proposes safeguards.",
        llm=llm,
        verbose=False,
    )

    synthesis_lead = Agent(
        role="Synthesis Lead",
        goal="Combine analyses into a clear, actionable plan",
        backstory="You synthesize inputs into Summary, Steps, and Recommendations. Be concise and direct.",
        llm=llm,
        verbose=False,
    )

    ctx = context or "General context."

    tech_task = Task(
        description=f"Analyze this problem from a technical/implementation angle.\n\nProblem: {problem}\nContext: {ctx}\n\nWhat are the key technical steps? What's feasible? Respond in 2-4 sentences.",
        expected_output="Brief technical analysis (2-4 sentences)",
        agent=technical_analyst,
    )

    practical_task = Task(
        description=f"Analyze from a practical/resource angle.\n\nProblem: {problem}\nContext: {ctx}\n\nWhat's realistic? What are constraints? Respond in 2-4 sentences.",
        expected_output="Brief practical analysis (2-4 sentences)",
        agent=practical_advisor,
        context=[tech_task],
    )

    risk_task = Task(
        description=f"Analyze from a risk/mitigation angle.\n\nProblem: {problem}\nContext: {ctx}\n\nWhat could go wrong? How to handle it? Respond in 2-4 sentences.",
        expected_output="Brief risk analysis (2-4 sentences)",
        agent=risk_evaluator,
        context=[tech_task, practical_task],
    )

    synth_desc = "Synthesize the three analyses above into a structured solution.\n\n"
    if prompt_prefix:
        synth_desc += f"{prompt_prefix}\n\n"
    synth_desc += """Output format:
1) **Summary** – High-level overview
2) **Steps** – Step-by-step approach
3) **Recommendations** – Key takeaways and actions

Be direct and actionable."""

    synthesis_task = Task(
        description=synth_desc,
        expected_output="Structured solution: Summary, Steps, Recommendations",
        agent=synthesis_lead,
        context=[tech_task, practical_task, risk_task],
    )

    return Crew(
        agents=[technical_analyst, practical_advisor, risk_evaluator, synthesis_lead],
        tasks=[tech_task, practical_task, risk_task, synthesis_task],
        process=Process.sequential,
        verbose=False,
    )

