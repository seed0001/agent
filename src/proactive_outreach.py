"""Policy and logging for autonomous proactive outreach.

This module sits above ``src.outreach.queue_outreach``. It decides whether a
message is allowed to leave the system, selects an appropriate contact, enforces
frequency limits, and writes a Creator-readable journal entry for every allow or
block decision.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from config.settings import DISCORD_OWNER_ID, PROJECT_ROOT, USER_PROFILES_DIR
from src import contacts, notifications
from src.outreach import queue_outreach

CONFIG_PATH = USER_PROFILES_DIR / "default" / "proactive_outreach.json"
STATE_PATH = USER_PROFILES_DIR / "default" / "proactive_outreach_state.json"
JOURNAL_PATH = PROJECT_ROOT / "andrew's projects" / "journal" / "outreach_log.txt"

CONTACT_TIERS = ("stranger", "friend", "good_friend", "best_friend", "creator")
DEFAULT_ALLOWED_TIERS = ("good_friend", "best_friend", "creator")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


@dataclass
class OutreachConfig:
    enabled: bool = True
    max_per_contact_per_day: int = 2
    cooldown_minutes: int = 180
    allowed_tiers: list[str] = field(default_factory=lambda: list(DEFAULT_ALLOWED_TIERS))
    blocked_contact_ids: list[str] = field(default_factory=list)
    preferred_channel: str = "discord"
    fallback_to_creator_web: bool = True
    suppress_duplicates: bool = True
    duplicate_window_seconds: int = 60

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "OutreachConfig":
        cfg = cls()
        if "enabled" in data:
            cfg.enabled = bool(data["enabled"])
        if "max_per_contact_per_day" in data:
            cfg.max_per_contact_per_day = max(0, int(data["max_per_contact_per_day"]))
        if "cooldown_minutes" in data:
            cfg.cooldown_minutes = max(0, int(data["cooldown_minutes"]))
        if isinstance(data.get("allowed_tiers"), list):
            cfg.allowed_tiers = [
                t for t in data["allowed_tiers"] if t in CONTACT_TIERS
            ] or list(DEFAULT_ALLOWED_TIERS)
        if isinstance(data.get("blocked_contact_ids"), list):
            cfg.blocked_contact_ids = [str(x) for x in data["blocked_contact_ids"]]
        if data.get("preferred_channel") in ("discord", "web"):
            cfg.preferred_channel = data["preferred_channel"]
        if "fallback_to_creator_web" in data:
            cfg.fallback_to_creator_web = bool(data["fallback_to_creator_web"])
        if "suppress_duplicates" in data:
            cfg.suppress_duplicates = bool(data["suppress_duplicates"])
        if "duplicate_window_seconds" in data:
            cfg.duplicate_window_seconds = max(1, int(data["duplicate_window_seconds"]))
        return cfg


def load_config() -> OutreachConfig:
    if not CONFIG_PATH.exists():
        return OutreachConfig()
    try:
        return OutreachConfig.from_dict(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, ValueError):
        return OutreachConfig()


def save_config(config: OutreachConfig) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        json.dumps(asdict(config), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def configure(**updates: Any) -> OutreachConfig:
    config = load_config()
    data = asdict(config)
    for key, value in updates.items():
        if value is not None and key in data:
            data[key] = value
    config = OutreachConfig.from_dict(data)
    save_config(config)
    return config


def _load_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {"contacts": {}}
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {"contacts": {}}
    except (OSError, json.JSONDecodeError):
        return {"contacts": {}}


def _save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def _contact_id(contact: dict[str, Any]) -> str:
    return str(contact.get("discord_id") or contact.get("id") or contact.get("name") or "")


def _is_do_not_contact(contact: dict[str, Any]) -> bool:
    if bool(contact.get("do_not_contact")):
        return True
    notes = str(contact.get("notes") or "").lower()
    return "do not contact" in notes or "do-not-contact" in notes


def _day_key(now: datetime) -> str:
    return now.astimezone(timezone.utc).strftime("%Y-%m-%d")


def _content_words(text: str) -> set[str]:
    stop = {
        "this", "that", "with", "from", "have", "your", "they", "them", "their",
        "what", "when", "where", "would", "could", "should", "about", "like",
        "just", "want", "really", "thing", "stuff", "some", "more", "very",
        "into", "over", "then", "than", "also", "back", "still", "been", "were",
        "good", "look", "kind", "sort", "here", "there", "doing", "make", "made",
        "take", "tell", "told", "said", "says", "user", "andrew", "travis",
    }
    words = re.findall(r"[a-zA-Z']{4,}", text.lower())
    return {w.replace("'", "") for w in words if w not in stop}


def _score_contact(contact: dict[str, Any], message: str) -> int:
    score = 0
    tier = contact.get("tier", "stranger")
    if tier == "creator":
        score += 100
    elif tier == "best_friend":
        score += 50
    elif tier == "good_friend":
        score += 30

    msg_words = _content_words(message)
    profile_words = _content_words(
        " ".join(
            str(contact.get(k) or "")
            for k in ("name", "interests", "notes", "location", "email")
        )
    )
    score += len(msg_words & profile_words) * 10
    return score


def _eligible_contacts(config: OutreachConfig, message: str) -> list[dict[str, Any]]:
    blocked = set(config.blocked_contact_ids)
    eligible: list[dict[str, Any]] = []
    for contact in contacts.get_all_contacts():
        cid = _contact_id(contact)
        if not cid or cid in blocked:
            continue
        tier = contact.get("tier", "stranger")
        if tier not in config.allowed_tiers:
            continue
        if _is_do_not_contact(contact):
            continue
        if config.preferred_channel == "discord" and not contact.get("discord_id"):
            continue
        eligible.append(contact)
    eligible.sort(key=lambda c: (_score_contact(c, message), str(c.get("updated") or "")), reverse=True)
    return eligible


def _state_for_contact(state: dict[str, Any], contact_id: str) -> dict[str, Any]:
    contacts_state = state.setdefault("contacts", {})
    record = contacts_state.setdefault(contact_id, {})
    if not isinstance(record, dict):
        record = {}
        contacts_state[contact_id] = record
    return record


def _limit_reason(
    state: dict[str, Any],
    contact_id: str,
    config: OutreachConfig,
    now: datetime,
) -> str | None:
    record = _state_for_contact(state, contact_id)
    last_sent = _parse_ts(record.get("last_sent_at"))
    if last_sent and now - last_sent < timedelta(minutes=config.cooldown_minutes):
        remaining = timedelta(minutes=config.cooldown_minutes) - (now - last_sent)
        return f"cooldown active ({int(remaining.total_seconds() // 60)}m remaining)"

    day = _day_key(now)
    if record.get("day") != day:
        return None
    if int(record.get("sent_today", 0)) >= config.max_per_contact_per_day:
        return f"daily cap reached ({config.max_per_contact_per_day})"
    return None


def _record_success(state: dict[str, Any], contact_id: str, now: datetime) -> None:
    record = _state_for_contact(state, contact_id)
    day = _day_key(now)
    if record.get("day") != day:
        record["day"] = day
        record["sent_today"] = 0
    record["sent_today"] = int(record.get("sent_today", 0)) + 1
    record["last_sent_at"] = now.isoformat()
    record["total_sent"] = int(record.get("total_sent", 0)) + 1


def _journal(entry: dict[str, Any]) -> None:
    JOURNAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    with JOURNAL_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")


def _status_entry(
    *,
    status: str,
    recipient: dict[str, Any] | None,
    message: str,
    trigger_reason: str,
    reason: str = "",
    channel: str = "",
) -> dict[str, Any]:
    return {
        "timestamp": _utcnow().isoformat(),
        "status": status,
        "recipient_id": _contact_id(recipient or {}) if recipient else "",
        "recipient_name": (recipient or {}).get("name", ""),
        "recipient_tier": (recipient or {}).get("tier", ""),
        "channel": channel,
        "trigger_reason": trigger_reason,
        "message": message,
        "reason": reason,
    }


def maybe_queue_proactive_outreach(
    message: str,
    *,
    trigger_reason: str,
    channel: str | None = None,
    target_discord_id: str | None = None,
    source: str = "proactive",
    now: datetime | None = None,
) -> dict[str, Any]:
    """Evaluate policy and queue a proactive message if allowed.

    Returns a structured decision. Every block/allow is appended to the Creator
    journal at ``andrew's projects/journal/outreach_log.txt``.
    """
    msg = (message or "").strip()
    now = now or _utcnow()
    config = load_config()
    selected_channel = channel or config.preferred_channel

    if not msg:
        entry = _status_entry(status="blocked", recipient=None, message=msg, trigger_reason=trigger_reason, reason="empty message", channel=selected_channel)
        _journal(entry)
        return entry
    if not config.enabled:
        entry = _status_entry(status="blocked", recipient=None, message=msg, trigger_reason=trigger_reason, reason="feature disabled", channel=selected_channel)
        _journal(entry)
        return entry

    state = _load_state()
    recipient: dict[str, Any] | None = None
    if target_discord_id:
        recipient = contacts.get_contact("", discord_id=str(target_discord_id)) or {
            "id": str(target_discord_id),
            "discord_id": str(target_discord_id),
            "tier": contacts.get_contact_tier(str(target_discord_id)),
        }
    else:
        eligible = _eligible_contacts(config, msg)
        if eligible:
            recipient = eligible[0]
        elif DISCORD_OWNER_ID and "creator" in config.allowed_tiers:
            recipient = contacts.get_contact("", discord_id=DISCORD_OWNER_ID) or {
                "id": DISCORD_OWNER_ID,
                "discord_id": DISCORD_OWNER_ID,
                "name": "Creator",
                "tier": "creator",
            }

    if not recipient:
        if config.fallback_to_creator_web:
            notifications.emit_notification("proactive", "Proactive outreach", msg, {"content": msg, "trigger_reason": trigger_reason})
            entry = _status_entry(status="queued", recipient=None, message=msg, trigger_reason=trigger_reason, channel="web")
            _journal(entry)
            return entry
        entry = _status_entry(status="blocked", recipient=None, message=msg, trigger_reason=trigger_reason, reason="no eligible recipient", channel=selected_channel)
        _journal(entry)
        return entry

    tier = recipient.get("tier", "stranger")
    if str(recipient.get("discord_id") or recipient.get("id") or "") == str(DISCORD_OWNER_ID or ""):
        tier = "creator"
        recipient["tier"] = "creator"
    cid = _contact_id(recipient)
    if cid in set(config.blocked_contact_ids):
        entry = _status_entry(status="blocked", recipient=recipient, message=msg, trigger_reason=trigger_reason, reason="blocked contact", channel=selected_channel)
        _journal(entry)
        return entry
    if tier not in config.allowed_tiers:
        entry = _status_entry(status="blocked", recipient=recipient, message=msg, trigger_reason=trigger_reason, reason=f"tier {tier} not allowed", channel=selected_channel)
        _journal(entry)
        return entry
    if _is_do_not_contact(recipient):
        entry = _status_entry(status="blocked", recipient=recipient, message=msg, trigger_reason=trigger_reason, reason="do not contact", channel=selected_channel)
        _journal(entry)
        return entry

    limit = _limit_reason(state, cid, config, now)
    if limit:
        entry = _status_entry(status="blocked", recipient=recipient, message=msg, trigger_reason=trigger_reason, reason=limit, channel=selected_channel)
        _journal(entry)
        return entry

    direct_override = source == "response_to_inbound" and contacts.can_send_direct(str(recipient.get("discord_id") or ""))
    if selected_channel == "discord" and recipient.get("discord_id"):
        result = queue_outreach(
            "discord",
            msg,
            target_user_id=str(recipient["discord_id"]),
            is_direct=direct_override,
            source=source,
            trigger_key=trigger_reason,
            suppress_duplicates=config.suppress_duplicates,
            dedup_window_seconds=config.duplicate_window_seconds,
        )
        if result.startswith("Duplicate suppressed"):
            entry = _status_entry(
                status="suppressed",
                recipient=recipient,
                message=msg,
                trigger_reason=trigger_reason,
                reason=result,
                channel="discord",
            )
            _journal(entry)
            return entry
        try:
            contacts.record_outbound(discord_id=str(recipient["discord_id"]))
        except Exception:
            pass
        channel_used = "discord"
    else:
        notifications.emit_notification("proactive", "Proactive outreach", msg, {"content": msg, "trigger_reason": trigger_reason, "recipient_id": cid})
        result = "queued for web"
        channel_used = "web"

    _record_success(state, cid, now)
    _save_state(state)
    entry = _status_entry(status="queued", recipient=recipient, message=msg, trigger_reason=trigger_reason, reason=result, channel=channel_used)
    _journal(entry)
    return entry


def status_summary() -> str:
    config = load_config()
    state = _load_state()
    lines = [
        f"enabled={config.enabled}",
        f"allowed_tiers={', '.join(config.allowed_tiers)}",
        f"max_per_contact_per_day={config.max_per_contact_per_day}",
        f"cooldown_minutes={config.cooldown_minutes}",
        f"preferred_channel={config.preferred_channel}",
        f"suppress_duplicates={config.suppress_duplicates}",
        f"duplicate_window_seconds={config.duplicate_window_seconds}",
        f"journal={JOURNAL_PATH}",
    ]
    contacts_state = state.get("contacts", {})
    if contacts_state:
        lines.append("recent contacts:")
        for cid, record in sorted(contacts_state.items()):
            lines.append(
                f"- {cid}: sent_today={record.get('sent_today', 0)} "
                f"day={record.get('day', '')} last_sent_at={record.get('last_sent_at', '')}"
            )
    if JOURNAL_PATH.exists():
        try:
            recent = JOURNAL_PATH.read_text(encoding="utf-8").splitlines()[-5:]
        except OSError:
            recent = []
        if recent:
            lines.append("recent journal entries:")
            lines.extend(f"- {line}" for line in recent)
    return "\n".join(lines)
