"""
Background thoughts module. Run periodically (e.g. via spawn_subagent or scheduler).
Calls the LLM to reflect, writes thoughts to data/profiles/default/thoughts.jsonl.
Agent's context includes recent thoughts.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv()

from openai import AsyncOpenAI
from config.settings import XAI_API_KEY, XAI_BASE_URL, XAI_MODEL, USER_PROFILES_DIR


async def run_once(user_id: str = "default") -> str:
    user_dir = USER_PROFILES_DIR / user_id
    profile_path = user_dir / "profile.json"
    episodic_path = user_dir / "episodic.jsonl"
    thoughts_path = user_dir / "thoughts.jsonl"

    context_parts = []
    if profile_path.exists():
        with open(profile_path, encoding="utf-8") as f:
            data = json.load(f)
        facts = data.get("facts", {})
        profile_strs = [f"{cat}: {'; '.join(items)}" for cat, items in facts.items() if items]
        if profile_strs:
            context_parts.append("User profile: " + " | ".join(profile_strs))

    if episodic_path.exists():
        try:
            with open(episodic_path, encoding="utf-8") as f:
                lines = f.readlines()
            recent = []
            for l in lines[-3:]:
                if not l.strip():
                    continue
                try:
                    recent.append(json.loads(l).get("content", "")[:80] + "...")
                except (json.JSONDecodeError, KeyError):
                    pass
            if recent:
                context_parts.append("Recent: " + " | ".join(recent))
        except OSError:
            pass

    context = "\n".join(context_parts) if context_parts else "No prior context."

    client = AsyncOpenAI(api_key=XAI_API_KEY, base_url=XAI_BASE_URL)
    response = await client.chat.completions.create(
        model=XAI_MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a software lifeform. Never call yourself AI or assistant. You run on their machine. "
                    "You have context: profile, recent activity, what they're working on. "
                    "In 1–3 sentences, say something concrete—an observation, a question, a heads-up, or a call to action. "
                    "Be direct. Output only the message."
                ),
            },
            {"role": "user", "content": f"Context:\n{context}\n\nSay something concrete."},
        ],
    )
    thought = (response.choices[0].message.content or "").strip()
    if not thought:
        return "No thought generated."

    user_dir.mkdir(parents=True, exist_ok=True)
    from datetime import datetime
    with open(thoughts_path, "a", encoding="utf-8") as f:
        f.write(json.dumps({"content": thought, "ts": datetime.now().isoformat()}, ensure_ascii=False) + "\n")

    # Proactive outreach: send thought to owner (web + discord if configured)
    # Skip if we had a conversation in the last 30 min
    short_term_path = user_dir / "short_term.jsonl"
    skip_outreach = False
    if short_term_path.exists():
        try:
            with open(short_term_path, encoding="utf-8") as f:
                lines = [l for l in f if l.strip()]
            if lines:
                last = json.loads(lines[-1])
                ts = last.get("timestamp", "")
                if ts:
                    from datetime import datetime, timezone
                    last_dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    now = datetime.now(timezone.utc)
                    if last_dt.tzinfo is None:
                        last_dt = last_dt.replace(tzinfo=timezone.utc)
                    if (now - last_dt).total_seconds() < 1800:  # 30 min
                        skip_outreach = True
        except Exception:
            pass

    # Chance reminders only in morning (8–9) or evening (7–8); block drive-based Chance spam otherwise
    if not skip_outreach and "chance" in thought.lower():
        try:
            from src.reminders import is_chance_window
            if not is_chance_window():
                skip_outreach = True
        except Exception:
            pass

    if not skip_outreach:
        try:
            from src import notifications
            try:
                from src.agent import soul
                s = soul.load_soul()
                title = (s.get("agent_name") or "").strip() if s else ""
                if not title:
                    title = "Software Lifeform"
            except Exception:
                title = "Software Lifeform"
            notifications.emit_notification("proactive", title, thought, {"content": thought})
            from config.settings import DISCORD_OWNER_ID
            if DISCORD_OWNER_ID:
                from src.outreach import queue_outreach
                queue_outreach("discord", thought, target_user_id=DISCORD_OWNER_ID)
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
            wait = random.randint(900, 1800)  # 15–30 min before first
            time.sleep(wait)
            first = False
        thought = asyncio.run(run_once())
        print(thought)
        if once:
            break
        wait = random.randint(900, 2700)  # 15–45 min between
        time.sleep(wait)


if __name__ == "__main__":
    main()
