"""Tests for the public MemoryStore facade — the surface used by ``core.py``,
``discord_bot.py`` and ``web/app.py``. Covers backward compatibility AND the
new lifecycle behavior (reinforcement on injection, session tagging, etc.).
"""
from __future__ import annotations

import shutil
import uuid

import pytest

from config.settings import USER_PROFILES_DIR
from src.agent.memory import MemoryStore, _fact_key, _infer_role
from src.agent.memory_db import close_all


@pytest.fixture
def mem() -> MemoryStore:
    uid = f"facade_{uuid.uuid4().hex[:12]}"
    store = MemoryStore(user_id=uid)
    yield store
    close_all()
    shutil.rmtree(USER_PROFILES_DIR / uid, ignore_errors=True)


# ---------- Construction / public attributes --------------------------------


def test_user_dir_exists_and_is_per_profile(mem: MemoryStore) -> None:
    assert mem.user_dir.exists()
    assert mem.user_dir.name == mem.user_id


def test_session_is_created_at_init(mem: MemoryStore) -> None:
    assert mem.session_id
    assert mem.sessions.get(mem.session_id) is not None


# ---------- Role inference --------------------------------------------------


def test_infer_role_handles_all_legacy_prefixes() -> None:
    assert _infer_role("User: hi") == "user"
    assert _infer_role("[Discord – jane said]: hi") == "user"
    assert _infer_role("[Travis is messaging from web]") == "user"
    assert _infer_role("Reply: hello there") == "assistant"
    assert _infer_role("Andrew: at your service, sir") == "assistant"
    assert _infer_role("Jarvis: indeed") == "assistant"
    assert _infer_role("[Status alert I sent you]: x") == "system"
    assert _infer_role("[Proactive message I sent you via Discord]: x") == "system"


# ---------- Short-term writes -----------------------------------------------


def test_add_short_term_writes_to_episodic_with_session_tag(mem: MemoryStore) -> None:
    mem.add_short_term("User: hello")
    mem.add_short_term("Andrew: hi sir")
    rows = mem.episodic.get_by_session(mem.session_id)
    assert len(rows) == 2
    assert rows[0].role == "user"
    assert rows[1].role == "assistant"


def test_add_short_term_skips_blank(mem: MemoryStore) -> None:
    mem.add_short_term("User:")
    mem.add_short_term("   ")
    assert mem.episodic.count() == 0


def test_short_term_property_returns_recent_legacy_entries(mem: MemoryStore) -> None:
    for i in range(5):
        mem.add_short_term(f"User: msg {i}")
    legacy = mem.short_term
    assert len(legacy) == 5
    assert legacy[-1].content == "User: msg 4"
    assert hasattr(legacy[0], "to_dict")


# ---------- Profile facts ---------------------------------------------------


def test_add_profile_fact_creates_protected_user_fact(mem: MemoryStore) -> None:
    msg = mem.add_profile_fact("personal", "Travis lives in Alabama")
    assert "Stored in profile" in msg
    facts = mem.profile.get_all()
    assert len(facts) == 1
    f = facts[0]
    assert f.value == "Travis lives in Alabama"
    assert f.source == "user"
    assert f.protected is True


def test_add_profile_fact_dedupe_via_stable_key(mem: MemoryStore) -> None:
    """Same fact text + category yields the same key, so it versions instead
    of duplicating."""
    mem.add_profile_fact("work", "Builds AI bots")
    msg = mem.add_profile_fact("work", "Builds AI bots")  # exact duplicate
    assert "Updated" in msg
    assert len(mem.profile.get_all()) == 1


def test_add_profile_fact_invalid_category_falls_back(mem: MemoryStore) -> None:
    mem.add_profile_fact("nonsense", "x")
    f = mem.profile.get_all()[0]
    assert f.category == "other"


def test_add_profile_fact_empty_returns_message(mem: MemoryStore) -> None:
    msg = mem.add_profile_fact("personal", "   ")
    assert "Empty" in msg
    assert mem.profile.get_all() == []


def test_fact_key_is_stable_and_collapses_whitespace() -> None:
    a = _fact_key("personal", "  Travis  is   from Alabama  ")
    b = _fact_key("personal", "Travis is from Alabama")
    assert a == b


# ---------- Reinforcement on prompt injection -------------------------------


def test_get_context_for_agent_reinforces_injected_facts(mem: MemoryStore) -> None:
    # Lower confidence so reinforcement is observable
    key = _fact_key("personal", "User's name is Travis")
    mem.profile.set(key, "User's name is Travis", category="personal",
                    source="extracted", confidence=0.5)
    before = mem.profile.get(key)
    assert before.confidence == 0.5
    assert before.last_referenced_at is None

    block = mem.get_context_for_agent()
    assert "Travis" in block

    after = mem.profile.get(key)
    assert after.confidence > 0.5
    assert after.last_referenced_at is not None


def test_get_context_for_agent_includes_recent_turns(mem: MemoryStore) -> None:
    mem.add_short_term("User: hello world")
    block = mem.get_context_for_agent()
    assert "hello world" in block
    assert "Recent conversation" in block


def test_get_context_for_agent_includes_cross_session_history(mem: MemoryStore) -> None:
    """A prior session's last turns should be visible in the new session's prompt."""
    # Simulate a prior session
    prior = mem.sessions.create(source="agent", title="prior")
    mem.episodic.insert(prior.id, "user", "User: yesterday I told you about my dog")

    # Current session should see it under "Earlier sessions"
    block = mem.get_context_for_agent()
    assert "Earlier sessions" in block
    assert "dog" in block


def test_auto_retrieve_episodic_surfaces_named_entity_context(mem: MemoryStore) -> None:
    prior = mem.sessions.create(source="agent", title="friend outline")
    mem.episodic.insert(
        prior.id,
        "user",
        "Brandon prefers dream analysis and AI streamer co-host planning.",
        importance=0.8,
    )

    result = mem.auto_retrieve_episodic("Can you remind me what Brandon likes?", days_back=7, limit=3)

    assert result["hits"]
    assert any("Brandon" in h["content"] for h in result["hits"])
    summary = result["summary"]
    assert "Auto-retrieved episodic context" in summary
    assert "Brandon" in summary


def test_warm_load_recent_episodic_context_sets_working_state(mem: MemoryStore) -> None:
    prior = mem.sessions.create(source="agent", title="recent planning")
    mem.episodic.insert(
        prior.id,
        "user",
        "Brandon wants continuity in friend/project memory.",
        importance=0.9,
    )

    block = mem.load_recent_episodic_context(hours_back=72, limit=3)

    assert "Warm-loaded episodic context" in block
    assert "Brandon" in block
    assert "Warm-loaded episodic context" in str(mem.get_working("episodic_warm_start", ""))


# ---------- Working state KV ------------------------------------------------


def test_set_and_get_working_roundtrip(mem: MemoryStore) -> None:
    mem.set_working("speaker", "travis")
    assert mem.get_working("speaker") == "travis"
    assert mem.get_working("missing", default="x") == "x"


def test_set_working_none_deletes(mem: MemoryStore) -> None:
    mem.set_working("k", "v")
    mem.set_working("k", None)
    assert mem.get_working("k") is None


def test_working_view_matches_state_all(mem: MemoryStore) -> None:
    mem.set_working("a", 1)
    mem.set_working("b", "x")
    assert mem.get_working_view() == {"a": 1, "b": "x"}


# ---------- Background thoughts ---------------------------------------------


def test_append_thought_persists_to_thought_store(mem: MemoryStore) -> None:
    mem.append_thought("a fleeting thought")
    rows = mem.thoughts.get_recent(limit=10)
    assert len(rows) == 1
    assert rows[0].content == "a fleeting thought"
    assert rows[0].session_id == mem.session_id


def test_append_thought_skips_blank(mem: MemoryStore) -> None:
    mem.append_thought("  ")
    assert mem.thoughts.get_recent(limit=10) == []


# ---------- Views (web UI compatibility) ------------------------------------


def test_get_profile_view_legacy_shape(mem: MemoryStore) -> None:
    mem.add_profile_fact("personal", "Name is Travis")
    mem.add_profile_fact("work", "Builds AI bots")
    view = mem.get_profile_view()
    assert "facts" in view
    assert "summary" in view
    assert "updated" in view
    assert "entries" in view  # new rich block
    assert "Name is Travis" in view["facts"]["personal"]
    assert "Builds AI bots" in view["facts"]["work"]
    rich = view["entries"]
    assert all("confidence" in e and "source" in e and "protected" in e for e in rich)


def test_get_episodic_view_returns_dicts_with_ts(mem: MemoryStore) -> None:
    mem.add_short_term("User: hello")
    view = mem.get_episodic_view(max_items=10)
    assert len(view) == 1
    assert view[0]["content"] == "User: hello"
    assert "ts" in view[0]
    assert view[0]["role"] == "user"


def test_get_thoughts_view_returns_dicts(mem: MemoryStore) -> None:
    mem.append_thought("thought one")
    mem.append_thought("thought two")
    view = mem.get_thoughts_view()
    assert len(view) == 2
    assert all("content" in t and "ts" in t for t in view)


# ---------- Immediate context ----------------------------------------------


def test_immediate_context_is_in_process_only(mem: MemoryStore) -> None:
    mem.add_immediate("scratch note")
    assert len(mem.immediate) == 1
    assert mem.episodic.count() == 0  # not persisted


def test_clear_immediate_empties_buffer(mem: MemoryStore) -> None:
    mem.add_immediate("a")
    mem.add_immediate("b")
    mem.clear_immediate()
    assert mem.immediate == []
