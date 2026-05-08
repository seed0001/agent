from src import contacts
from src.agent import core


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(contacts, "CONTACTS_PATH", tmp_path / "contacts.json")


def test_record_discord_interaction_creates_stranger_contact(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    contact = contacts.record_discord_interaction(
        discord_id="123",
        display_name="Solonaras",
        content="hello",
    )

    assert contact["id"] == "123"
    assert contact["discord_id"] == "123"
    assert contact["name"] == "Solonaras"
    assert contact["display_name"] == "Solonaras"
    assert contact["tier"] == "stranger"
    assert contact["preferred_channel"] == "discord"
    assert contact["inbound_count"] == 1
    assert contact["last_message_preview"] == "hello"
    assert contacts.get_contact("", discord_id="123")["name"] == "Solonaras"


def test_record_discord_interaction_increments_and_suggests_tier(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    for i in range(3):
        contact = contacts.record_discord_interaction(
            discord_id="123",
            display_name="Solonaras",
            content=f"msg {i}",
        )

    assert contact["tier"] == "stranger"
    assert contact["inbound_count"] == 3
    assert contact["suggested_tier"] == "friend"
    assert contact["tier_reason"] == "3+ inbound messages"


def test_creator_set_tier_beats_suggested_tier(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    contacts.update_contact(
        "",
        discord_id="123",
        name="Solonaras",
        tier="good_friend",
    )
    for i in range(3):
        contact = contacts.record_discord_interaction(
            discord_id="123",
            display_name="Solonaras",
            content=f"msg {i}",
        )

    assert contact["tier"] == "good_friend"
    assert contact["suggested_tier"] == "good_friend"


def test_update_contact_persists_new_fields(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    contacts.update_contact(
        "",
        name="Brandon",
        display_name="ILGAV3NG3R",
        interests="Builds AI, has Luna",
        preferred_channel="discord",
        do_not_contact=True,
    )

    saved = contacts.get_contact("brandon")
    assert saved["id"] == "web-brandon"
    assert saved["display_name"] == "ILGAV3NG3R"
    assert saved["aliases"] == ["ILGAV3NG3R"]
    assert saved["preferred_channel"] == "discord"
    assert saved["do_not_contact"] is True


async def _run_contact_tool_as_good_friend(monkeypatch):
    captured = {}

    def fake_update_contact(identifier, **kwargs):
        captured.update(kwargs)
        return "Updated contact"

    monkeypatch.setattr(core.contacts, "update_contact", fake_update_contact)
    agent = object.__new__(core.AssistiveAgent)
    agent._get_current_speaker_tier = lambda: "good_friend"
    return await agent._run_tool(
        "update_contact",
        {
            "identifier": "",
            "discord_id": "123",
            "name": "Someone",
            "tier": "best_friend",
        },
    ), captured


def test_non_creator_update_contact_cannot_set_tier(monkeypatch):
    import asyncio

    result, captured = asyncio.run(_run_contact_tool_as_good_friend(monkeypatch))

    assert result == "Updated contact"
    assert captured["discord_id"] == "123"
    assert captured["tier"] is None
