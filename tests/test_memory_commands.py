"""Tests for the slash-command parser used by chat / Discord / web."""
from __future__ import annotations

import shutil
import uuid

import pytest

from config.settings import USER_PROFILES_DIR
from src.agent.memory import MemoryStore
from src.agent.memory_commands import handle, is_command
from src.agent.memory_db import close_all


@pytest.fixture
def mem() -> MemoryStore:
    uid = f"cmd_{uuid.uuid4().hex[:12]}"
    store = MemoryStore(user_id=uid)
    yield store
    close_all()
    shutil.rmtree(USER_PROFILES_DIR / uid, ignore_errors=True)


# ---------- is_command --------------------------------------------------


def test_is_command_recognizes_slash() -> None:
    assert is_command("/memory") is True
    assert is_command("  /memory") is True
    assert is_command("/remember x = y") is True


def test_is_command_rejects_non_commands() -> None:
    assert is_command("memory") is False
    assert is_command("") is False
    assert is_command("//double-slash") is False  # explicit escape
    assert is_command("/") is False


# ---------- /memory -----------------------------------------------------


def test_memory_returns_helpful_message_when_empty(mem: MemoryStore) -> None:
    out = handle("/memory", mem)
    assert "no profile facts" in out


def test_memory_lists_facts_with_health_bar(mem: MemoryStore) -> None:
    mem.add_profile_fact("personal", "Lives in Alabama")
    out = handle("/memory", mem)
    assert "Lives in Alabama" in out
    assert "PROTECTED" in out  # user-source = protected
    assert "[==========]" in out  # confidence 1.0 fills the bar


def test_memory_help_lists_commands(mem: MemoryStore) -> None:
    out = handle("/memory help", mem)
    for fragment in ("/remember", "/forget", "/protect", "/unprotect", "/thoughts"):
        assert fragment in out


def test_memory_stats_shows_counts(mem: MemoryStore) -> None:
    mem.add_profile_fact("personal", "Travis")
    mem.add_short_term("User: hi")
    out = handle("/memory stats", mem)
    assert "facts: 1" in out
    assert "episodic turns" in out
    assert "sessions" in out


def test_memory_decay_persists_in_state(mem: MemoryStore) -> None:
    out = handle("/memory decay 14", mem)
    assert "14" in out
    assert mem.state.get("memory.config.half_life_days") == 14.0


def test_memory_decay_validates(mem: MemoryStore) -> None:
    assert "usage" in handle("/memory decay banana", mem)
    assert "positive" in handle("/memory decay 0", mem)


def test_memory_min_persists_in_state(mem: MemoryStore) -> None:
    out = handle("/memory min 30", mem)
    assert "30" in out
    assert mem.state.get("memory.config.min_confidence") == 0.30


# ---------- /remember ---------------------------------------------------


def test_remember_category_colon_form(mem: MemoryStore) -> None:
    out = handle("/remember work: Builds AI bots", mem)
    assert "Stored" in out
    facts = mem.profile.get_all()
    assert facts[0].value == "Builds AI bots"
    assert facts[0].category == "work"
    assert facts[0].protected is True


def test_remember_key_equals_form(mem: MemoryStore) -> None:
    out = handle("/remember favorite-color = blue", mem)
    assert "remembered" in out
    fact = mem.profile.get("favorite-color")
    assert fact is not None
    assert fact.value == "blue"
    assert fact.protected is True


def test_remember_freeform_drops_to_other(mem: MemoryStore) -> None:
    out = handle("/remember Loves long drives", mem)
    assert "Stored" in out
    assert mem.profile.get_all()[0].category == "other"


def test_remember_empty_shows_usage(mem: MemoryStore) -> None:
    assert "usage" in handle("/remember", mem)


# ---------- /forget -----------------------------------------------------


def test_forget_by_substring(mem: MemoryStore) -> None:
    mem.add_profile_fact("personal", "Lives in Alabama")
    out = handle("/forget Alabama", mem)
    assert "forgot" in out
    assert mem.profile.get_all() == []


def test_forget_unknown_returns_helpful_message(mem: MemoryStore) -> None:
    out = handle("/forget nonsense", mem)
    assert "no fact matches" in out


def test_forget_ambiguous_lists_matches(mem: MemoryStore) -> None:
    mem.add_profile_fact("personal", "Likes blue cars")
    mem.add_profile_fact("personal", "Likes blue ties")
    out = handle("/forget blue", mem)
    assert "matched 2 facts" in out


def test_forget_all_requires_confirmation(mem: MemoryStore) -> None:
    mem.add_profile_fact("personal", "x")
    out = handle("/forget all", mem)
    assert "yes-i-am-sure" in out
    assert mem.profile.get_all() != []  # nothing was deleted


def test_forget_all_with_confirmation_wipes(mem: MemoryStore) -> None:
    mem.add_profile_fact("personal", "x")
    mem.add_profile_fact("work", "y")
    out = handle("/forget all yes-i-am-sure", mem)
    assert "forgot 2" in out
    assert mem.profile.get_all() == []


# ---------- /protect /unprotect -----------------------------------------


def test_protect_an_extracted_fact(mem: MemoryStore) -> None:
    mem.profile.set("hobby::ai", "Builds AI bots", category="work",
                    source="extracted", protected=False)
    out = handle("/protect ai bots", mem)
    assert "protected" in out
    assert mem.profile.get("hobby::ai").protected is True


def test_unprotect_lets_a_fact_decay(mem: MemoryStore) -> None:
    mem.add_profile_fact("personal", "Loves coffee")
    fact = mem.profile.get_all()[0]
    out = handle(f"/unprotect {fact.key}", mem)
    assert "unprotected" in out
    assert mem.profile.get(fact.key).protected is False


# ---------- /thoughts /sessions -----------------------------------------


def test_thoughts_lists_recent(mem: MemoryStore) -> None:
    mem.append_thought("a passing thought")
    mem.append_thought("another one")
    out = handle("/thoughts", mem)
    assert "a passing thought" in out
    assert "another one" in out


def test_thoughts_limit_arg(mem: MemoryStore) -> None:
    for i in range(5):
        mem.append_thought(f"thought {i}")
    out = handle("/thoughts 2", mem)
    assert "## Last 2 thoughts" in out


def test_sessions_marks_current(mem: MemoryStore) -> None:
    out = handle("/sessions", mem)
    assert "(current)" in out


# ---------- non-commands -----------------------------------------------


def test_handle_returns_none_for_non_commands(mem: MemoryStore) -> None:
    assert handle("hello", mem) is None
    assert handle("", mem) is None


def test_handle_returns_none_for_unknown_slash(mem: MemoryStore) -> None:
    assert handle("/unknown-command", mem) is None
