import asyncio

import pytest

from src.agent import core


class _MemoryStub:
    def __init__(self, working_value: str | None = None):
        self.working_value = working_value

    def get_working(self, key: str):
        if key == "current_speaker_discord_id":
            return self.working_value
        return None


def _agent() -> core.AssistiveAgent:
    agent = object.__new__(core.AssistiveAgent)
    agent.memory = _MemoryStub("stale-discord-id")
    return agent


def test_current_speaker_defaults_to_creator_even_with_stale_working_memory():
    agent = _agent()

    assert agent._get_current_speaker_tier() == "creator"


@pytest.mark.asyncio
async def test_speaker_context_isolated_between_concurrent_chat_tasks(monkeypatch):
    monkeypatch.setattr(core.contacts, "get_contact_tier", lambda discord_id: "friend")

    ready = asyncio.Event()
    release = asyncio.Event()

    async def fake_chat(self, user_input="", **kwargs):
        ready.set()
        await release.wait()
        return self._get_current_speaker_tier()

    monkeypatch.setattr(core.AssistiveAgent, "chat", fake_chat)
    agent = _agent()

    web_task = asyncio.create_task(
        agent.chat_for_speaker("creator turn", speaker_discord_id=None)
    )
    await ready.wait()

    discord_task = asyncio.create_task(
        agent.chat_for_speaker("discord turn", speaker_discord_id="solonaras-id")
    )
    await asyncio.sleep(0)
    release.set()

    web_tier, discord_tier = await asyncio.gather(web_task, discord_task)

    assert web_tier == "creator"
    assert discord_tier == "friend"
    assert agent._get_current_speaker_tier() == "creator"
