"""
Soul layer: produces emotional/relational stance each turn.
Runs before the cloud model. Output is injected so she "hears from her soul."

Uses local Ollama (llama3.2) with soul.json.
"""
from pathlib import Path

from src.agent import soul

try:
    import requests
except ImportError:
    requests = None

OLLAMA_URL = "http://localhost:11434"
OLLAMA_MODEL = "llama3.2:latest"


def _call_ollama(prompt: str, timeout: float = 30.0) -> str:
    """Call Ollama generate. Returns response text or empty on failure."""
    if not requests:
        return ""
    try:
        r = requests.post(
            f"{OLLAMA_URL}/api/generate",
            json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False},
            timeout=timeout,
        )
        r.raise_for_status()
        payload = r.json()
        try:
            from src.cost_tracking import record_local_usage

            in_toks = int(payload.get("prompt_eval_count") or 0)
            out_toks = int(payload.get("eval_count") or 0)
            total = int(payload.get("total_tokens") or (in_toks + out_toks))
            record_local_usage(
                provider="ollama",
                model=OLLAMA_MODEL,
                source_type="background",
                prompt_tokens=in_toks,
                completion_tokens=out_toks,
                total_tokens=total,
                actual_usage=(in_toks + out_toks) > 0,
                estimated_usage=(in_toks + out_toks) == 0,
                task_label="soul_layer",
                metadata={"component": "soul_layer"},
                user_id="default",
            )
        except Exception:
            pass
        return (payload.get("response") or "").strip()
    except Exception:
        return ""


def _build_prompt(identity: str, name: str, conversation_snippet: str) -> str:
    return f"""You are the inner emotional core of {name}. Your identity: {identity}

Latest from the conversation: {conversation_snippet[:400]}

What do you feel or care about right now, in 1-2 sentences? Output only that. No preamble. Just the emotional stance."""


def get_soul_output(conversation_snippet: str) -> tuple[str, str]:
    """
    Run the soul layer and return (output_text, source).
    source: "ollama" | "none"
    """
    s = soul.load_soul()
    if not s:
        return ("", "none")

    identity = soul.format_soul_for_prompt(s)
    name = (s.get("agent_name") or "").strip() or "you"

    prompt = _build_prompt(identity, name, conversation_snippet)
    out = _call_ollama(prompt)
    return (out, "ollama") if out else ("", "none")
