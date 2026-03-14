"""
Proactive outreach: queue for messages the agent wants to send (Discord or web).
Other modules push here; Discord bot and web app consume.
"""
import asyncio
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from config.settings import USER_PROFILES_DIR

OUTREACH_PATH = USER_PROFILES_DIR / "default" / "outreach.jsonl"

# In-memory queue for real-time delivery (web app SSE)
_outreach_queue: asyncio.Queue = asyncio.Queue()


@dataclass
class OutreachMessage:
    channel: str  # "discord" | "web"
    content: str
    target_user_id: str | None = None  # Discord user ID for DMs, or None for primary owner
    created_at: str = ""

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now().isoformat()


def queue_outreach(channel: str, content: str, target_user_id: str | None = None) -> str:
    """Add a proactive message to the queue. Returns confirmation."""
    try:
        from src.logging_config import log_outreach_attempt
        log_outreach_attempt("queue", target_user_id or "owner", content[:80])
    except Exception:
        pass
    msg = OutreachMessage(channel=channel, content=content, target_user_id=target_user_id)
    # Persist to file for durability
    OUTREACH_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTREACH_PATH, "a", encoding="utf-8") as f:
        f.write(
            json.dumps(
                {
                    "channel": msg.channel,
                    "content": msg.content,
                    "target_user_id": msg.target_user_id,
                    "created_at": msg.created_at,
                },
                ensure_ascii=False,
            )
            + "\n"
        )
    # Push to in-memory queue for live web delivery
    try:
        _outreach_queue.put_nowait(msg)
    except asyncio.QueueFull:
        pass
    return f"Outreach queued for {channel}: {content[:80]}{'...' if len(content) > 80 else ''}"


def get_outreach_queue() -> asyncio.Queue:
    """Get the in-memory outreach queue (for SSE consumer)."""
    return _outreach_queue
