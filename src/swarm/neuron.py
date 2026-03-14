"""Neuron: lightweight (threshold) or LLM output."""
from src.swarm.signal import Signal
from src.swarm.synapse import forward

from .config import HIDDEN_THRESHOLD, OLLAMA_BASE_URL, OLLAMA_MODEL


def lightweight_fire(signals: list[Signal]) -> tuple[bool, float]:
    """
    Hidden neuron: aggregate weighted inputs, fire if sum >= threshold.
    Returns (did_fire, aggregate_sum).
    """
    total = sum(s.strength for s in signals)
    return (total >= HIDDEN_THRESHOLD, total)


async def llm_output(signals: list[Signal], prompt_prefix: str = "") -> Signal:
    """
    Output neuron: given activations from hidden layer, call LLM to produce response.
    Uses Ollama (llama3.2:latest).
    """
    activations = [
        f"- {s.content or f'activation {s.strength:.2f}'}"
        for s in signals
        if s.strength > 0
    ]
    context = "\n".join(activations) if activations else "(no activations)"
    prompt = f"""You are the output neuron of a neural swarm. The hidden layer produced these activations:

{context}

Given these signals, produce a concise response (1-3 sentences). Be direct.
"""
    if prompt_prefix:
        prompt = f"{prompt_prefix}\n\n{prompt}"

    try:
        import httpx

        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post(
                f"{OLLAMA_BASE_URL}/api/generate",
                json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False},
            )
            r.raise_for_status()
            data = r.json()
            text = (data.get("response") or "").strip()
    except Exception as e:
        text = f"[Swarm output error: {e}]"

    return Signal(type="response", content=text, strength=1.0, metadata={"source": "llm_output"})
