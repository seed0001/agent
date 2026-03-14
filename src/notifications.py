"""
Notifications: Discord message alerts → desktop + web.
Event bus for real-time notifications.
"""
import os
import asyncio
from dataclasses import dataclass
from datetime import datetime

# In-memory queue for notification events (Discord message arrived, etc.)
_notification_queue: asyncio.Queue = asyncio.Queue()


@dataclass
class NotificationEvent:
    type: str  # "discord_message", "proactive", etc.
    title: str
    body: str
    meta: dict | None = None
    created_at: str = ""

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now().isoformat()


def emit_notification(n_type: str, title: str, body: str, meta: dict | None = None) -> None:
    """Emit a notification (Discord bot calls this)."""
    ev = NotificationEvent(type=n_type, title=title, body=body, meta=meta or {})
    try:
        _notification_queue.put_nowait(ev)
    except asyncio.QueueFull:
        pass


def get_notification_queue() -> asyncio.Queue:
    """Get the notification queue (for SSE consumer)."""
    return _notification_queue


def show_desktop_notification(title: str, message: str) -> None:
    """Show a desktop notification (Windows/macOS/Linux)."""
    try:
        from plyer import notification

        notification.notify(
            title=title,
            message=message[:256],  # truncate for system limits
            app_name=os.environ.get("AGENT_DISPLAY_NAME", "Software Lifeform"),
            timeout=8,
        )
    except Exception:
        pass  # plyer may fail on some platforms
