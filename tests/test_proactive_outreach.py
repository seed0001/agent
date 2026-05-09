from datetime import datetime, timezone

from src import proactive_outreach


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(proactive_outreach, "CONFIG_PATH", tmp_path / "proactive_outreach.json")
    monkeypatch.setattr(proactive_outreach, "STATE_PATH", tmp_path / "proactive_outreach_state.json")
    monkeypatch.setattr(proactive_outreach, "JOURNAL_PATH", tmp_path / "journal" / "outreach_log.txt")
    monkeypatch.setattr(proactive_outreach, "DISCORD_OWNER_ID", "creator-1")


def test_blocks_lower_tier_contacts_and_logs(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(
        proactive_outreach.contacts,
        "get_all_contacts",
        lambda: [
            {
                "id": "stranger-1",
                "discord_id": "stranger-1",
                "name": "Stranger",
                "tier": "stranger",
                "interests": "python",
            }
        ],
    )
    monkeypatch.setattr(
        proactive_outreach.contacts,
        "get_contact",
        lambda identifier, discord_id=None: None,
    )
    monkeypatch.setattr(
        proactive_outreach.contacts,
        "get_contact_tier",
        lambda discord_id, identifier="": "stranger",
    )

    queued = []
    monkeypatch.setattr(
        proactive_outreach,
        "queue_outreach",
        lambda channel, content, target_user_id=None, **kwargs: queued.append((channel, content, target_user_id, kwargs)) or "queued",
    )

    decision = proactive_outreach.maybe_queue_proactive_outreach(
        "I have a Python idea.",
        trigger_reason="test",
        target_discord_id="stranger-1",
    )

    assert decision["status"] == "blocked"
    assert decision["reason"] == "tier stranger not allowed"
    assert queued == []
    assert "tier stranger not allowed" in proactive_outreach.JOURNAL_PATH.read_text(encoding="utf-8")


def test_selects_matching_allowed_contact_and_enforces_daily_cap(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    contacts = [
        {
            "id": "friend-1",
            "discord_id": "friend-1",
            "name": "Friend",
            "tier": "friend",
            "interests": "gardening",
        },
        {
            "id": "good-1",
            "discord_id": "good-1",
            "name": "Good Friend",
            "tier": "good_friend",
            "interests": "python automation",
        },
    ]
    monkeypatch.setattr(proactive_outreach.contacts, "get_all_contacts", lambda: contacts)
    monkeypatch.setattr(
        proactive_outreach.contacts,
        "get_contact",
        lambda identifier, discord_id=None: next((c for c in contacts if c["discord_id"] == discord_id), None),
    )
    queued = []
    monkeypatch.setattr(
        proactive_outreach,
        "queue_outreach",
        lambda channel, content, target_user_id=None, **kwargs: queued.append((channel, content, target_user_id, kwargs)) or "queued",
    )
    proactive_outreach.save_config(
        proactive_outreach.OutreachConfig(
            cooldown_minutes=0,
            max_per_contact_per_day=1,
            allowed_tiers=["good_friend"],
        )
    )
    now = datetime(2026, 5, 7, 15, 0, tzinfo=timezone.utc)

    first = proactive_outreach.maybe_queue_proactive_outreach(
        "I found a Python automation idea.",
        trigger_reason="test",
        now=now,
    )
    second = proactive_outreach.maybe_queue_proactive_outreach(
        "Another Python automation note.",
        trigger_reason="test",
        now=now,
    )

    assert first["status"] == "queued"
    assert first["recipient_id"] == "good-1"
    assert queued == [("discord", "I found a Python automation idea.", "good-1", {"is_direct": False, "source": "proactive", "trigger_key": "test", "suppress_duplicates": True, "dedup_window_seconds": 60})]
    assert second["status"] == "blocked"
    assert second["reason"] == "daily cap reached (1)"


def test_creator_can_disable_feature(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    proactive_outreach.configure(enabled=False)
    monkeypatch.setattr(proactive_outreach.contacts, "get_all_contacts", lambda: [])

    decision = proactive_outreach.maybe_queue_proactive_outreach(
        "I want to reach out.",
        trigger_reason="test",
    )

    assert decision["status"] == "blocked"
    assert decision["reason"] == "feature disabled"
    assert "enabled=False" in proactive_outreach.status_summary()


def test_duplicate_proactive_send_is_suppressed(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    contacts = [
        {
            "id": "creator-1",
            "discord_id": "creator-1",
            "name": "Creator",
            "tier": "creator",
            "interests": "automation",
        }
    ]
    monkeypatch.setattr(proactive_outreach.contacts, "get_all_contacts", lambda: contacts)
    monkeypatch.setattr(
        proactive_outreach.contacts,
        "get_contact",
        lambda identifier, discord_id=None: next((c for c in contacts if c["discord_id"] == discord_id), None),
    )
    proactive_outreach.save_config(
        proactive_outreach.OutreachConfig(
            cooldown_minutes=0,
            max_per_contact_per_day=10,
            allowed_tiers=["creator"],
            suppress_duplicates=True,
            duplicate_window_seconds=60,
        )
    )
    sent_once = {"done": False}

    def _queue(channel, content, target_user_id=None, **kwargs):
        if sent_once["done"]:
            return "Duplicate suppressed for discord (creator-1); dedup_key=testkey"
        sent_once["done"] = True
        return "queued"

    monkeypatch.setattr(proactive_outreach, "queue_outreach", _queue)

    first = proactive_outreach.maybe_queue_proactive_outreach(
        "good morning",
        trigger_reason="morning",
        target_discord_id="creator-1",
    )
    second = proactive_outreach.maybe_queue_proactive_outreach(
        "good morning",
        trigger_reason="morning",
        target_discord_id="creator-1",
    )

    assert first["status"] == "queued"
    assert second["status"] == "suppressed"
