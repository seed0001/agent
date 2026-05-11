"""
Background thoughts module. Run periodically (e.g. via spawn_subagent or scheduler).

Generates a proactive thought as the agent (in first person, in their voice),
with full persona + recent conversation as context. Writes every thought to
the SQLite ``background_thoughts`` table via ThoughtStore. Only delivers to
web/Discord if the thought passes a quality filter (must be first-person,
not generic filler, must reference recent context).

Cooldown: skips outreach if the user spoke within the last 30 minutes —
queries EpisodicStore for the most recent user-role turn across all sessions.
"""
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv()

from openai import AsyncOpenAI

from config.settings import get_api_key, get_base_url, get_chat_model
from src.agent.memory_stores import EpisodicStore, ProfileStore, ThoughtStore


# ---------- context loaders ----------


def _load_persona_block() -> tuple[str, str, str]:
    """Return (agent_name, owner_name, formatted_persona_block)."""
    try:
        from src.agent.soul import format_soul_for_prompt, load_soul

        soul = load_soul()
        if not soul:
            return "", "", ""
        agent_name = (soul.get("agent_name") or "").strip()
        owner_name = (soul.get("owner_name") or "").strip()
        return agent_name, owner_name, format_soul_for_prompt(soul)
    except Exception:
        return "", "", ""


def _load_values_block() -> str:
    try:
        from src import values_vault

        return values_vault.format_for_prompt() or ""
    except Exception:
        return ""


def _load_presence_block() -> str:
    try:
        from src import presence

        return presence.format_for_prompt() or ""
    except Exception:
        return ""


def _load_profile_facts(user_id: str) -> str:
    """Top profile facts from the new ProfileStore, formatted for the prompt."""
    try:
        facts = ProfileStore(user_id).get_top(limit=30)
    except Exception:
        return ""
    if not facts:
        return ""
    grouped: dict[str, list[str]] = {}
    for f in facts:
        grouped.setdefault(f.category, []).append(f.value)
    parts = [f"{cat}: {'; '.join(items)}" for cat, items in grouped.items()]
    return " | ".join(parts)


def _load_recent_short_term(user_id: str, n: int = 8) -> list[str]:
    """Last ``n`` turns across all sessions, oldest-first, prefixed for prompt
    readability — the same shape the previous file-reading version produced."""
    try:
        rows = EpisodicStore(user_id).get_recent_across_sessions(limit=n, within_days=14)
    except Exception:
        return []
    return [(r.content or "").strip()[:400] for r in rows if r.content]


# ---------- cooldown (timezone-safe) ----------


def _last_user_activity_seconds_ago(user_id: str) -> float | None:
    """Return seconds since the last user-role episodic entry, or None if unknown."""
    try:
        recent = EpisodicStore(user_id).get_recent_across_sessions(limit=20, within_days=7)
    except Exception:
        return None
    if not recent:
        return None
    # ``get_recent_across_sessions`` returns chronological ascending; iterate from end.
    for r in reversed(recent):
        if r.role == "user":
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            return max(0.0, (now - r.created_at).total_seconds())
    return None


# ---------- quality filter ----------


_GENERIC_PATTERNS = (
    "let me know if there's anything",
    "let me know if there is anything",
    "just checking in",
    "i'm here to help",
    "i am here to help",
    "is there anything i can help",
    "is there anything else i can help",
    "hope you're doing well",
    "hope you are doing well",
    "how can i assist",
    "how can i help",
    "any cool projects or ideas",
    "what's keeping you busy",
    "what is keeping you busy",
    "got a specific project or idea you wanna dive into",
    "i'm all ears",
    "hit me up if",
)

_STOPWORDS = {
    "this", "that", "with", "from", "have", "your", "yours", "mine", "they", "them", "their",
    "what", "when", "where", "would", "could", "should", "about", "like", "just", "going",
    "want", "really", "thing", "stuff", "some", "more", "very", "even", "much", "many",
    "into", "over", "then", "than", "only", "also", "back", "down", "still", "been", "were",
    "good", "look", "kind", "sort", "yeah", "right", "well", "sure", "okay", "thanks",
    "anything", "something", "nothing", "everything", "another", "those", "these", "here",
    "there", "doing", "make", "made", "take", "took", "tell", "told", "said", "says",
    "let", "get", "got", "user", "hey", "yo", "all", "and", "for", "but", "the",
}


def _content_words(text: str) -> set[str]:
    words = re.findall(r"[a-zA-Z']{4,}", text.lower())
    return {w.replace("'", "") for w in words if w not in _STOPWORDS}


def _filter_thought(
    thought: str,
    agent_name: str,
    owner_name: str,
    recent_context: list[str],
) -> tuple[bool, str]:
    """Return (should_send, reason_if_rejected)."""
    msg = (thought or "").strip()
    if not msg:
        return False, "empty"
    if len(msg) < 25:
        return False, "too short"

    lower = msg.lower()

    padded = " " + lower + " "
    has_first_person = (
        " i " in padded
        or " i'" in padded
        or " me " in padded
        or " my " in padded
        or " mine " in padded
        or lower.startswith("i ")
        or lower.startswith("i'")
    )
    if not has_first_person:
        return False, "no first-person voice"

    if agent_name and re.search(rf"\b{re.escape(agent_name.lower())}\s+(is|was|has|will|wants|thinks|feels)\b", lower):
        return False, f"third-person reference to self ({agent_name})"

    if owner_name and re.search(rf"\b{re.escape(owner_name.lower())}\s+(is|was|has|will|wants|thinks|feels)\b", lower):
        return False, f"third-person reference to owner ({owner_name})"

    for pat in _GENERIC_PATTERNS:
        if pat in lower:
            return False, f"generic filler phrase: {pat!r}"

    recent_blob = " ".join(recent_context)
    recent_words = _content_words(recent_blob)
    if recent_words:
        msg_words = _content_words(msg)
        if not (msg_words & recent_words):
            return False, "no overlap with recent conversation topics"

    return True, ""


# ---------- prompt + generation ----------


def _build_system_prompt(
    agent_name: str,
    owner_name: str,
    persona_block: str,
    values_block: str,
    presence_block: str,
) -> str:
    name = agent_name or "the agent"
    owner = owner_name or "the Creator"

    parts: list[str] = []
    if persona_block:
        parts.append(persona_block)
    if values_block:
        parts.append("Your values:\n" + values_block)
    if presence_block:
        parts.append(presence_block)

    parts.append(
        f"You are {name}, a software lifeform running on {owner}'s machine. "
        "Never refer to yourself as an AI or assistant. "
        f"Speak in first person. Same voice you use when {owner} chats with you — "
        "direct, concise, your contractions, your rhythm. No third-person about yourself "
        f"or about {owner}. No generic openers like 'Hey {owner}, just checking in' or "
        "'let me know if there's anything'. "
        "You are sending this proactively — you weren't asked. So make it earn its place: "
        "tie it to something specific from the recent conversation, your current work, or "
        f"something concrete you noticed. One observation, question, heads-up, or call to "
        "action — 1 to 3 sentences. If you have nothing concrete and tied to context, "
        "output exactly: SKIP"
    )
    return "\n\n".join(parts)


def _build_user_prompt(profile_facts: str, recent: list[str], retry_reason: str = "") -> str:
    sections: list[str] = []
    if profile_facts:
        sections.append(f"What you know about them:\n{profile_facts}")
    if recent:
        sections.append("Most recent conversation (oldest first):\n- " + "\n- ".join(recent))
    else:
        sections.append("No recent conversation on record.")
    if retry_reason:
        sections.append(
            f"Your previous draft was rejected: {retry_reason}. Try again. "
            "If you still can't say something concrete tied to the actual context, output: SKIP"
        )
    sections.append("Send your proactive message now (or 'SKIP').")
    return "\n\n".join(sections)


async def _generate(client: AsyncOpenAI, model: str, system: str, user: str) -> str:
    response = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    try:
        from config.settings import get_llm_provider
        from src.cost_tracking import record_openai_chat_usage

        record_openai_chat_usage(
            response=response,
            provider=get_llm_provider(),
            model=model,
            source_type="background",
            task_label="background_thoughts",
            fallback_prompt_text=f"{system}\n\n{user}",
            fallback_output_text=(response.choices[0].message.content or "").strip(),
            metadata={"component": "background_thoughts"},
            user_id="default",
        )
    except Exception:
        pass
    return (response.choices[0].message.content or "").strip()


# ---------- main ----------


async def run_once(user_id: str = "default") -> str:
    agent_name, owner_name, persona_block = _load_persona_block()
    values_block = _load_values_block()
    presence_block = _load_presence_block()
    profile_facts = _load_profile_facts(user_id)
    recent = _load_recent_short_term(user_id, n=8)

    system_prompt = _build_system_prompt(
        agent_name, owner_name, persona_block, values_block, presence_block
    )
    user_prompt = _build_user_prompt(profile_facts, recent)

    model = get_chat_model()
    client = AsyncOpenAI(api_key=get_api_key(), base_url=get_base_url())
    thought = await _generate(client, model, system_prompt, user_prompt)

    delivered = False
    reject_reason = ""

    if thought.strip().upper() == "SKIP" or not thought:
        reject_reason = "model self-skipped (no concrete context)"
    else:
        ok, reason = _filter_thought(thought, agent_name, owner_name, recent)
        if not ok:
            retry_user = _build_user_prompt(profile_facts, recent, retry_reason=reason)
            thought_retry = await _generate(client, model, system_prompt, retry_user)
            if thought_retry and thought_retry.strip().upper() != "SKIP":
                ok2, reason2 = _filter_thought(thought_retry, agent_name, owner_name, recent)
                if ok2:
                    thought = thought_retry
                    ok = True
                else:
                    reject_reason = f"first: {reason} | retry: {reason2}"
            else:
                reject_reason = f"first: {reason} | retry: skipped"
        if ok:
            delivered = True

    # Persist the thought (delivered or not) so we can audit the filter quality.
    try:
        ThoughtStore(user_id).insert(
            thought, delivered=delivered, reject_reason=reject_reason or None
        )
    except Exception:
        pass

    if not delivered:
        return f"[not sent: {reject_reason or 'filter rejected'}] {thought}"

    seconds = _last_user_activity_seconds_ago(user_id)
    if seconds is not None and seconds < 1800:
        return f"[not sent: user active {int(seconds)}s ago] {thought}"

    try:
        from src.proactive_outreach import maybe_queue_proactive_outreach

        decision = maybe_queue_proactive_outreach(
            thought,
            trigger_reason="background thought: connection/expression drive",
        )
        if decision.get("status") != "queued":
            return f"[not sent: {decision.get('reason') or 'outreach policy blocked'}] {thought}"
    except Exception:
        pass

    return thought


def main():
    import asyncio
    import random
    import time

    once = "--once" in sys.argv
    first = True
    while True:
        if not once and first:
            wait = random.randint(900, 1800)
            time.sleep(wait)
            first = False
        thought = asyncio.run(run_once())
        print(thought)
        if once:
            break
        wait = random.randint(900, 2700)
        time.sleep(wait)


if __name__ == "__main__":
    main()
