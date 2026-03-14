"""Graph: build and run propagation."""
from collections import defaultdict

from src.swarm.signal import Signal
from src.swarm.synapse import forward
from src.swarm.neuron import lightweight_fire, llm_output
from src.swarm.config import (
    INPUT_NEURONS,
    HIDDEN_NEURONS,
    OUTPUT_NEURONS,
    SYNAPSES,
)


def _build_adjacency() -> tuple[dict[str, list[tuple[str, float]]], dict[str, list[tuple[str, float]]]]:
    """Returns (incoming[neuron] = [(from_neuron, weight), ...], outgoing[neuron] = [(to_neuron, weight), ...])."""
    incoming: dict[str, list[tuple[str, float]]] = defaultdict(list)
    outgoing: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for fr, to, w in SYNAPSES:
        incoming[to].append((fr, w))
        outgoing[fr].append((to, w))
    return dict(incoming), dict(outgoing)


async def run(
    inputs: list[Signal] | list[str],
    prompt_prefix: str = "",
) -> Signal:
    """
    Run one propagation through the swarm.
    inputs: list of Signal or str (str becomes Signal(content=s)).
    Returns the output neuron's response.
    """
    incoming, outgoing = _build_adjacency()

    # Normalize inputs to Signal
    if inputs and isinstance(inputs[0], str):
        signals_in = [Signal(content=s, strength=1.0) for s in inputs]
    else:
        signals_in = list(inputs)

    # Layer 0: input neurons (just pass through, one input per neuron)
    layer0_outputs: dict[str, list[Signal]] = {}
    for i, nid in enumerate(INPUT_NEURONS):
        s = signals_in[i] if i < len(signals_in) else Signal(content="", strength=0.0)
        layer0_outputs[nid] = [s]

    # Propagate input -> hidden
    layer1_inputs: dict[str, list[Signal]] = defaultdict(list)
    for fr, conns in outgoing.items():
        if fr not in layer0_outputs:
            continue
        for to, w in conns:
            if to not in HIDDEN_NEURONS:
                continue
            for sig in layer0_outputs[fr]:
                layer1_inputs[to].append(forward(sig, w))

    # Layer 1: hidden neurons (lightweight fire)
    layer1_outputs: dict[str, list[Signal]] = {}
    for nid in HIDDEN_NEURONS:
        sigs = layer1_inputs.get(nid, [])
        fired, agg = lightweight_fire(sigs)
        if fired:
            content = " ".join(s.content for s in sigs if s.content).strip() or f"activation_{agg:.2f}"
            layer1_outputs[nid] = [Signal(content=content[:200], strength=agg)]
        else:
            layer1_outputs[nid] = []

    # Propagate hidden -> output
    output_inputs: list[Signal] = []
    for nid in OUTPUT_NEURONS:
        for fr, w in incoming.get(nid, []):
            if fr not in layer1_outputs:
                continue
            for sig in layer1_outputs[fr]:
                output_inputs.append(forward(sig, w))

    # Output neuron: LLM
    result = await llm_output(output_inputs, prompt_prefix=prompt_prefix)
    return result


async def run_cloud(
    inputs: list[Signal] | list[str],
    prompt_prefix: str = "",
    client=None,
    model: str = "grok-3",
) -> Signal:
    """
    Cloud swarm: Grok simulates multiple neurons (3 parallel perspectives, then synthesize).
    client: AsyncOpenAI, model: model name.
    """
    if not client:
        raise ValueError("Cloud swarm requires client")
    problem = inputs[0] if inputs else ""
    context = inputs[1] if len(inputs) > 1 else ""
    if isinstance(problem, Signal):
        problem = problem.content
    if isinstance(context, Signal):
        context = context.content

    perspectives = [
        "Analyze from a technical/implementation angle. What are the key technical steps?",
        "Analyze from a practical/resource angle. What's realistic, what are constraints?",
        "Analyze from a risk/mitigation angle. What could go wrong, how to handle it?",
    ]

    async def one_perspective(prompt: str) -> str:
        r = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "You are one neuron in a swarm. Brief analysis. 2-4 sentences."},
                {"role": "user", "content": f"Problem: {problem}\nContext: {context or 'None'}\n\n{prompt}\n\nRespond in 2-4 sentences."},
            ],
        )
        return (r.choices[0].message.content or "").strip()

    import asyncio
    results = await asyncio.gather(*[one_perspective(p) for p in perspectives])

    synthesis_prompt = f"""Three neurons analyzed this problem from different angles:

1. Technical: {results[0]}
2. Practical: {results[1]}
3. Risk: {results[2]}

Synthesize into: 1) Summary 2) Step-by-step approach 3) Recommendations. Be direct.
"""
    if prompt_prefix:
        synthesis_prompt = f"{prompt_prefix}\n\n{synthesis_prompt}"

    r = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": "Synthesize into structured solution: Summary, Steps, Recommendations."},
            {"role": "user", "content": synthesis_prompt},
        ],
    )
    text = (r.choices[0].message.content or "").strip()
    return Signal(type="response", content=text, strength=1.0, metadata={"source": "cloud_swarm"})


def run_sync(inputs: list[Signal] | list[str], prompt_prefix: str = "") -> Signal:
    """Sync wrapper for run()."""
    import asyncio
    return asyncio.run(run(inputs, prompt_prefix))
