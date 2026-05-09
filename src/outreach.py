"""
Proactive outreach: queue for messages the agent wants to send (Discord or web).
Other modules push here; Discord bot and web app consume.
"""
import asyncio
import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from config.settings import USER_PROFILES_DIR

OUTREACH_PATH = USER_PROFILES_DIR / "default" / "outreach.jsonl"
OUTREACH_DEDUP_PATH = USER_PROFILES_DIR / "default" / "outreach_dedup.json"
DEFAULT_DEDUP_WINDOW_SECONDS = 60

# In-memory queue for real-time delivery (web app SSE)
_outreach_queue: asyncio.Queue = asyncio.Queue()


@dataclass
class OutreachMessage:
    channel: str  # "discord" | "web"
    content: str
    target_user_id: str | None = None  # Discord user ID for DMs, or None for primary owner
    target_channel_id: str | None = None  # Discord channel ID for channel messages
    attachment_paths: list[str] | None = None  # Files to attach (Discord only)
    source: str = "unknown"  # proactive | direct | background_completion | etc.
    trigger_key: str = ""  # shared event key for cross-path dedup
    dedup_key: str = ""  # normalized target+content hash
    event_id: str = ""  # stable id persisted for journal/replay suppression
    is_direct: bool = False  # True = Creator-directed (bypasses caps), False = autonomous proactive
    created_at: str = ""

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now().isoformat()
        if self.attachment_paths is None:
            self.attachment_paths = []
        if not self.event_id:
            self.event_id = f"evt_{uuid.uuid4().hex[:12]}"


def _load_dedup_state() -> dict[str, str]:
    if not OUTREACH_DEDUP_PATH.exists():
        return {}
    try:
        data = json.loads(OUTREACH_DEDUP_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items()}
    except (OSError, json.JSONDecodeError):
        pass
    return {}


def _save_dedup_state(data: dict[str, str]) -> None:
    OUTREACH_DEDUP_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTREACH_DEDUP_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _dedup_key(
    *,
    channel: str,
    content: str,
    target_user_id: str | None,
    target_channel_id: str | None,
    attachment_paths: list[str] | None,
    trigger_key: str,
) -> str:
    target = f"channel:{target_channel_id}" if target_channel_id else f"user:{target_user_id or 'owner'}"
    parts = [
        channel.strip().lower(),
        target.strip().lower(),
        (content or "").strip(),
        "|".join(sorted(str(p) for p in (attachment_paths or []))),
    ]
    payload = "||".join(parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]


def _is_duplicate(key: str, dedup_window_seconds: int) -> bool:
    now = datetime.now()
    state = _load_dedup_state()
    ts = state.get(key)
    if ts:
        try:
            prev = datetime.fromisoformat(ts)
            if now - prev < timedelta(seconds=max(1, dedup_window_seconds)):
                return True
        except ValueError:
            pass
    # Keep file small by pruning stale entries during every write path.
    cutoff = now - timedelta(seconds=max(1, dedup_window_seconds) * 10)
    pruned: dict[str, str] = {}
    for k, v in state.items():
        try:
            if datetime.fromisoformat(v) >= cutoff:
                pruned[k] = v
        except ValueError:
            continue
    pruned[key] = now.isoformat()
    _save_dedup_state(pruned)
    return False


def queue_outreach(
    channel: str,
    content: str,
    target_user_id: str | None = None,
    target_channel_id: str | None = None,
    attachment_paths: list[str] | None = None,
    source: str = "unknown",
    trigger_key: str = "",
    event_id: str | None = None,
    suppress_duplicates: bool = True,
    dedup_window_seconds: int = DEFAULT_DEDUP_WINDOW_SECONDS,
    is_direct: bool = False,
) -> str:
    """Add a message to the queue. Returns confirmation.
    
    Args:
        channel: "discord" or "web"
        content: message content
        target_user_id: Discord user ID for DMs
        target_channel_id: Discord channel ID for channel messages
        attachment_paths: Optional file paths for Discord attachments
        source: Where message came from (proactive/direct/background, etc.)
        trigger_key: Shared event key used to suppress cross-path duplicates
        event_id: Optional caller-supplied event id
        suppress_duplicates: If true, block repeated sends in dedup window
        dedup_window_seconds: Suppression window size in seconds
        is_direct: True if Creator-directed (bypasses proactive caps)
    """
    try:
        from src.logging_config import log_outreach_attempt
        target = target_channel_id or target_user_id or "owner"
        log_outreach_attempt("queue", target, content[:80])
    except Exception:
        pass
    dedup_key = _dedup_key(
        channel=channel,
        content=content,
        target_user_id=target_user_id,
        target_channel_id=target_channel_id,
        attachment_paths=attachment_paths,
        trigger_key=trigger_key,
    )
    if suppress_duplicates and channel == "discord" and _is_duplicate(dedup_key, dedup_window_seconds):
        target_desc = f"channel {target_channel_id}" if target_channel_id else (target_user_id or "owner")
        return f"Duplicate suppressed for {channel} ({target_desc}); dedup_key={dedup_key}"

    msg = OutreachMessage(
        channel=channel,
        content=content,
        target_user_id=target_user_id,
        target_channel_id=target_channel_id,
        attachment_paths=attachment_paths or [],
        source=source,
        trigger_key=trigger_key,
        dedup_key=dedup_key,
        event_id=event_id or "",
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
                    "attachment_paths": msg.attachment_paths,
                    "source": msg.source,
                    "trigger_key": msg.trigger_key,
                    "dedup_key": msg.dedup_key,
                    "event_id": msg.event_id,
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
    attachment_note = f" with {len(msg.attachment_paths)} attachment(s)" if msg.attachment_paths else ""
    return (
        f"Message queued for {channel} ({target_desc}{attachment_note}) "
        f"[event_id={msg.event_id}, dedup_key={msg.dedup_key}]: "
        f"{content[:80]}{'...' if len(content) > 80 else ''}"
    )


def get_outreach_queue() -> asyncio.Queue:
    """Get the in-memory outreach queue (for SSE consumer)."""
    return _outreach_queue
