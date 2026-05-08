"""
Contact profiles: friends, Discord users, and anyone the agent speaks with.
Stores name, location, interests, email, discord_id, tier, etc.
Tiers: stranger, friend, good_friend, best_friend, creator (creator = you only)
"""
import json
import re
from datetime import datetime
from pathlib import Path

from config.settings import USER_PROFILES_DIR

CONTACTS_PATH = USER_PROFILES_DIR / "default" / "contacts.json"

CONTACT_FIELDS = (
    "name",
    "display_name",
    "aliases",
    "location",
    "interests",
    "email",
    "discord_id",
    "notes",
    "tier",
    "suggested_tier",
    "tier_reason",
    "preferred_channel",
    "do_not_contact",
    "first_seen",
    "last_seen",
    "inbound_count",
    "outbound_count",
)

CONTACT_TIERS = ("stranger", "friend", "good_friend", "best_friend", "creator")
_TIER_RANK = {tier: i for i, tier in enumerate(CONTACT_TIERS)}


def _load_contacts() -> dict:
    """Load all contacts. Key = discord_id or 'web-{identifier}'."""
    if not CONTACTS_PATH.exists():
        return {}
    try:
        with open(CONTACTS_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _save_contacts(data: dict) -> None:
    CONTACTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    data["_updated"] = datetime.now().isoformat()
    with open(CONTACTS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _contact_key(identifier: str, discord_id: str | None = None) -> str:
    """Resolve contact key: discord_id takes precedence, else web-{identifier}."""
    if discord_id:
        return str(discord_id)
    return f"web-{identifier or 'anonymous'}"


def _slug(text: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9_-]+", "-", text.strip().lower()).strip("-")
    return value or "anonymous"


def _now() -> str:
    return datetime.now().isoformat()


def _suggested_tier(inbound_count: int) -> tuple[str, str]:
    """Return suggested tier from engagement count.

    This is advisory only. The real ``tier`` never changes unless Creator calls
    ``update_contact(..., tier=...)``.
    """
    if inbound_count >= 25:
        return "best_friend", "25+ inbound messages"
    if inbound_count >= 10:
        return "good_friend", "10+ inbound messages"
    if inbound_count >= 3:
        return "friend", "3+ inbound messages"
    return "stranger", "below friend threshold"


def _merge_alias(contact: dict, alias: str | None) -> None:
    alias = (alias or "").strip()
    if not alias:
        return
    aliases = contact.get("aliases")
    if not isinstance(aliases, list):
        aliases = []
    existing = {str(a).lower() for a in aliases}
    current_names = {str(contact.get("name") or "").lower()}
    if alias.lower() not in existing and alias.lower() not in current_names:
        aliases.append(alias)
    contact["aliases"] = aliases


def get_contact(identifier: str, discord_id: str | None = None) -> dict | None:
    """Get a contact by identifier or discord_id."""
    key = _contact_key(identifier, discord_id)
    data = _load_contacts()
    return data.get(key)


def update_contact(
    identifier: str,
    *,
    discord_id: str | None = None,
    name: str | None = None,
    display_name: str | None = None,
    location: str | None = None,
    interests: str | None = None,
    email: str | None = None,
    notes: str | None = None,
    tier: str | None = None,
    preferred_channel: str | None = None,
    do_not_contact: bool | None = None,
) -> str:
    """
    Add or update a contact. Use identifier for web users, discord_id for Discord.
    Only provided fields are updated. Tier: stranger, friend, good_friend, best_friend, creator.
    """
    if not identifier and not discord_id and name:
        identifier = _slug(name)
    key = _contact_key(identifier, discord_id)
    data = _load_contacts()
    contact = data.get(key) or {"id": key}
    if discord_id:
        contact["discord_id"] = str(discord_id)
    if name is not None:
        contact["name"] = name.strip() or contact.get("name", "")
    if display_name is not None:
        contact["display_name"] = display_name.strip() or contact.get("display_name", "")
        _merge_alias(contact, display_name)
    if location is not None:
        contact["location"] = location.strip() or contact.get("location", "")
    if interests is not None:
        contact["interests"] = interests.strip() or contact.get("interests", "")
    if email is not None:
        contact["email"] = email.strip() or contact.get("email", "")
    if notes is not None:
        contact["notes"] = notes.strip() or contact.get("notes", "")
    if preferred_channel in ("discord", "web"):
        contact["preferred_channel"] = preferred_channel
    if do_not_contact is not None:
        contact["do_not_contact"] = bool(do_not_contact)
    if tier is not None and tier in CONTACT_TIERS:
        contact["tier"] = tier
    if "tier" not in contact:
        contact["tier"] = "stranger"
    now = _now()
    contact.setdefault("first_seen", now)
    contact["updated"] = now
    data[key] = {k: v for k, v in contact.items() if k in (*CONTACT_FIELDS, "id", "updated", "discord_id")}
    _save_contacts(data)
    return f"Updated contact: {contact.get('name', key)}"


def record_discord_interaction(
    *,
    discord_id: str,
    display_name: str,
    content: str = "",
) -> dict:
    """Upsert a Discord contact and increment inbound engagement counters."""
    key = _contact_key("", discord_id)
    data = _load_contacts()
    now = _now()
    contact = data.get(key) or {
        "id": key,
        "discord_id": str(discord_id),
        "tier": "stranger",
        "preferred_channel": "discord",
        "inbound_count": 0,
        "outbound_count": 0,
        "first_seen": now,
    }
    contact["discord_id"] = str(discord_id)
    contact["display_name"] = (display_name or "").strip() or contact.get("display_name", "")
    if not contact.get("name") and contact.get("display_name"):
        contact["name"] = contact["display_name"]
    _merge_alias(contact, display_name)
    contact["last_seen"] = now
    contact["updated"] = now
    contact["inbound_count"] = int(contact.get("inbound_count", 0) or 0) + 1
    if content:
        contact["last_message_preview"] = content.strip()[:160]

    suggested, reason = _suggested_tier(contact["inbound_count"])
    current = contact.get("tier", "stranger")
    if current != "creator" and _TIER_RANK.get(suggested, 0) > _TIER_RANK.get(current, 0):
        contact["suggested_tier"] = suggested
        contact["tier_reason"] = reason
    else:
        contact.setdefault("suggested_tier", current if current in CONTACT_TIERS else "stranger")
        contact.setdefault("tier_reason", "current tier already at or above engagement suggestion")

    data[key] = {k: v for k, v in contact.items() if k in (*CONTACT_FIELDS, "id", "updated", "discord_id", "last_message_preview")}
    _save_contacts(data)
    return data[key]


def record_outbound(discord_id: str | None = None, identifier: str = "") -> None:
    """Increment outbound count for a known contact."""
    if not discord_id and not identifier:
        return
    key = _contact_key(identifier, discord_id)
    data = _load_contacts()
    contact = data.get(key)
    if not contact:
        return
    contact["outbound_count"] = int(contact.get("outbound_count", 0) or 0) + 1
    contact["updated"] = _now()
    data[key] = {k: v for k, v in contact.items() if k in (*CONTACT_FIELDS, "id", "updated", "discord_id", "last_message_preview")}
    _save_contacts(data)


def get_contact_tier(discord_id: str | None, identifier: str = "") -> str:
    """Get tier for a contact. Default stranger."""
    contact = get_contact(identifier, discord_id=discord_id)
    if not contact:
        return "stranger"
    t = contact.get("tier", "stranger")
    return t if t in CONTACT_TIERS else "stranger"


def get_all_contacts() -> list[dict]:
    """Return all contacts (excluding internal keys)."""
    data = _load_contacts()
    return [
        {k: v for k, v in c.items() if not k.startswith("_")}
        for k, c in data.items()
        if not k.startswith("_")
    ]


def format_contact_for_context(contact: dict | None) -> str:
    """Format contact for agent context."""
    if not contact:
        return ""
    parts = []
    if contact.get("tier"):
        parts.append(f"Tier: {contact['tier']}")
    if contact.get("suggested_tier") and contact.get("suggested_tier") != contact.get("tier"):
        parts.append(f"Suggested tier: {contact['suggested_tier']} ({contact.get('tier_reason', 'engagement')})")
    if contact.get("name"):
        parts.append(f"Name: {contact['name']}")
    if contact.get("display_name") and contact.get("display_name") != contact.get("name"):
        parts.append(f"Display name: {contact['display_name']}")
    if contact.get("inbound_count") is not None:
        parts.append(f"Inbound messages: {contact.get('inbound_count', 0)}")
    if contact.get("location"):
        parts.append(f"Location: {contact['location']}")
    if contact.get("interests"):
        parts.append(f"Interests: {contact['interests']}")
    if contact.get("email"):
        parts.append(f"Email: {contact['email']}")
    if contact.get("notes"):
        parts.append(f"Notes: {contact['notes']}")
    return "\n".join(parts) if parts else ""
