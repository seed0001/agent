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
    target_channel_id: str | None = None  # Discord channel ID for channel messages
    is_direct: bool = False  # True = Creator-directed (bypasses caps), False = autonomous proactive
    created_at: str = ""

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now().isoformat()


def queue_outreach(channel: str, content: str, target_user_id: str | None = None, target_channel_id: str | None = None, is_direct: bool = False) -> str:
    """Add a message to the queue. Returns confirmation.
    
    Args:
        channel: "discord" or "web"
        content: message content
        target_user_id: Discord user ID for DMs
        target_channel_id: Discord channel ID for channel messages
        is_direct: True if Creator-directed (bypasses proactive caps)
    """
    try:
        from src.logging_config import log_outreach_attempt
        target = target_channel_id or target_user_id or "owner"
        log_outreach_attempt("queue", target, content[:80])
    except Exception:
        pass
    msg = OutreachMessage(
        channel=channel,
        content=content,
        target_user_id=target_user_id,
        target_channel_id=target_channel_id,
        is_direct=is_direct,
    )
    # Persist to file for durability
    OUTREACH_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTREACH_PATH, "a", encoding="utf-8") as f:
        f.write(
            json.dumps(
                {
                    "channel": msg.channel,
                    "content": msg.content,
                    "target_user_id": msg.target_user_id,
                    "target_channel_id": msg.target_channel_id,
                    "is_direct": msg.is_direct,
                    "created_at": msg.created_at,
                },
                ensure_ascii=False,
            )
            + "\n"
        )
    # Push to in-memory queue for live delivery
    try:
        _outreach_queue.put_nowait(msg)
    except asyncio.QueueFull:
        pass
    target_desc = f"channel {target_channel_id}" if target_channel_id else (target_user_id or "owner")
    return f"Message queued for {channel} ({target_desc}): {content[:80]}{'...' if len(content) > 80 else ''}"


def get_outreach_queue() -> asyncio.Queue:
    """Get the in-memory outreach queue (for SSE consumer)."""
    return _outreach_queue
