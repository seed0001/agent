"""Tests for the MemoryConsolidator. Stubs the LLM so no network is required."""
from __future__ import annotations

import asyncio
import json
import shutil
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from config.settings import USER_PROFILES_DIR
from src.agent.memory_consolidator import (
    ConsolidatorConfig,
    MemoryConsolidator,
    WATERMARK_KEY,
    _build_importance_user_prompt,
    _fact_key,
    _format_transcript,
    _parse_extracted_facts,
    _parse_importance_scores,
)
from src.agent.memory_db import close_all, get_connection
from src.agent.memory_stores import (
    EpisodicStore,
    ProfileStore,
    SessionStore,
    WorkingState,
    _utcnow,
)


@pytest.fixture
def user_id() -> str:
    uid = f"consol_{uuid.uuid4().hex[:12]}"
    yield uid
    close_all()
    shutil.rmtree(USER_PROFILES_DIR / uid, ignore_errors=True)


# ---------- Parsing -----------------------------------------------------


def test_parse_extracted_facts_clean_array() -> None:
    raw = '[{"category": "personal", "fact": "Lives in Alabama"}]'
    out = _parse_extracted_facts(raw)
    assert out == [{"category": "personal", "fact": "Lives in Alabama"}]


def test_parse_extracted_facts_strips_code_fences() -> None:
    raw = '```json\n[{"category": "work", "fact": "Builds bots"}]\n```'
    out = _parse_extracted_facts(raw)
    assert out == [{"category": "work", "fact": "Builds bots"}]


def test_parse_extracted_facts_empty_array() -> None:
    assert _parse_extracted_facts("[]") == []


def test_parse_extracted_facts_garbage_returns_empty() -> None:
    assert _parse_extracted_facts("not json") == []
    assert _parse_extracted_facts("") == []


def test_parse_extracted_facts_normalizes_invalid_category() -> None:
    raw = '[{"category": "nonsense", "fact": "x"}]'
    assert _parse_extracted_facts(raw)[0]["category"] == "other"


def test_parse_extracted_facts_skips_empty_facts() -> None:
    raw = '[{"category": "personal", "fact": ""}, {"category": "work", "fact": "Real"}]'
    out = _parse_extracted_facts(raw)
    assert len(out) == 1
    assert out[0]["fact"] == "Real"


def test_parse_extracted_facts_finds_array_amid_prose() -> None:
    raw = 'Sure, here you go:\n[{"category": "personal", "fact": "X"}]\nThanks.'
    assert _parse_extracted_facts(raw) == [{"category": "personal", "fact": "X"}]


# ---------- Transcript formatting ---------------------------------------


def test_format_transcript_renders_roles_and_truncates() -> None:
    sess = SessionStore(f"_t{uuid.uuid4().hex[:8]}")
    s = sess.create("test")
    es = EpisodicStore(sess.user_id)
    es.insert(s.id, "user", "hello")
    es.insert(s.id, "assistant", "hi back")
    rows = es.get_by_session(s.id)
    transcript = _format_transcript(rows)
    assert "USER: hello" in transcript
    assert "ASSISTANT: hi back" in transcript
    # Cleanup
    close_all()
    shutil.rmtree(USER_PROFILES_DIR / sess.user_id, ignore_errors=True)


def test_format_transcript_respects_max_chars() -> None:
    sess = SessionStore(f"_t{uuid.uuid4().hex[:8]}")
    s = sess.create("test")
    es = EpisodicStore(sess.user_id)
    for i in range(10):
        es.insert(s.id, "user", "x" * 200)
    rows = es.get_by_session(s.id)
    out = _format_transcript(rows, max_chars=500)
    assert len(out) <= 600  # generous slop for prefix
    close_all()
    shutil.rmtree(USER_PROFILES_DIR / sess.user_id, ignore_errors=True)


# ---------- Decay pass --------------------------------------------------


def test_decay_pass_runs_through_profile_store(user_id: str) -> None:
    profile = ProfileStore(user_id)
    profile.set("k", "v", source="extracted", confidence=0.5)
    # Backdate so decay will fire
    conn = get_connection(user_id)
    backdated = (_utcnow() - timedelta(days=10)).isoformat(sep=" ", timespec="microseconds")
    conn.execute(
        "UPDATE profile_facts SET last_referenced_at = ?, updated_at = ? "
        "WHERE key = ? AND deleted_at IS NULL",
        (backdated, backdated, "k"),
    )
    cons = MemoryConsolidator(user_id, ConsolidatorConfig(half_life_days=2.0, min_confidence=0.01))
    stats = cons.decay_pass()
    assert stats.checked == 1
    assert stats.decayed == 1
    fact = profile.get("k")
    assert fact is not None
    assert fact.confidence < 0.5


# ---------- Consolidate pass --------------------------------------------


def _make_stub_llm(facts: list[dict]):
    """Returns an async LLM that always answers with the provided facts."""
    payload = json.dumps(facts)

    async def _call(_system: str, _user: str) -> str:
        return payload

    return _call


def _backdate_episodic(user_id: str, days: float) -> None:
    """Move every episodic row's created_at into the past so consolidation
    sees them as eligible."""
    conn = get_connection(user_id)
    backdated = (_utcnow() - timedelta(days=days)).isoformat(sep=" ", timespec="microseconds")
    conn.execute("UPDATE episodic_memory SET created_at = ?", (backdated,))


@pytest.mark.asyncio
async def test_consolidate_pass_extracts_and_inserts_facts(user_id: str) -> None:
    sess = SessionStore(user_id).create("test")
    es = EpisodicStore(user_id)
    for i in range(5):
        es.insert(sess.id, "user", f"USER msg {i}")
        es.insert(sess.id, "assistant", f"reply {i}")
    _backdate_episodic(user_id, days=2.0)

    facts = [
        {"category": "personal", "fact": "Lives in Alabama"},
        {"category": "work", "fact": "Builds AI bots as a hobby"},
    ]
    cons = MemoryConsolidator(
        user_id,
        ConsolidatorConfig(consolidate_after_days=1.0, min_session_turns=4),
        llm=_make_stub_llm(facts),
    )
    stats = await cons.consolidate_pass()
    assert stats.sessions_examined == 1
    assert stats.facts_inserted == 2

    profile = ProfileStore(user_id)
    keys = [_fact_key(f["category"], f["fact"]) for f in facts]
    for k in keys:
        f = profile.get(k)
        assert f is not None
        assert f.source == "consolidated"
        assert f.protected is False
        assert 0.0 < f.confidence < 1.0


@pytest.mark.asyncio
async def test_consolidate_pass_skips_short_sessions(user_id: str) -> None:
    sess = SessionStore(user_id).create("test")
    es = EpisodicStore(user_id)
    es.insert(sess.id, "user", "tiny")
    _backdate_episodic(user_id, days=2.0)

    cons = MemoryConsolidator(
        user_id,
        ConsolidatorConfig(consolidate_after_days=1.0, min_session_turns=4),
        llm=_make_stub_llm([{"category": "personal", "fact": "X"}]),
    )
    stats = await cons.consolidate_pass()
    assert stats.sessions_examined == 1
    assert stats.sessions_skipped == 1
    assert stats.facts_inserted == 0


@pytest.mark.asyncio
async def test_consolidate_pass_does_not_overwrite_protected_user_facts(user_id: str) -> None:
    profile = ProfileStore(user_id)
    key = _fact_key("personal", "Lives in Alabama")
    profile.set(key, "Lives in Alabama", category="personal", source="user", protected=True)
    sess = SessionStore(user_id).create("test")
    es = EpisodicStore(user_id)
    for i in range(5):
        es.insert(sess.id, "user", f"chat {i}")
        es.insert(sess.id, "assistant", f"reply {i}")
    _backdate_episodic(user_id, days=2.0)

    cons = MemoryConsolidator(
        user_id,
        ConsolidatorConfig(consolidate_after_days=1.0, min_session_turns=4),
        llm=_make_stub_llm([{"category": "personal", "fact": "Lives in Alabama"}]),
    )
    await cons.consolidate_pass()
    fact = profile.get(key)
    assert fact.protected is True
    assert fact.source == "user"
    # Reinforced — last_referenced_at should now be set
    assert fact.last_referenced_at is not None


@pytest.mark.asyncio
async def test_consolidate_pass_advances_watermark(user_id: str) -> None:
    state = WorkingState(user_id)
    assert state.get(WATERMARK_KEY) is None

    sess = SessionStore(user_id).create("test")
    es = EpisodicStore(user_id)
    for i in range(5):
        es.insert(sess.id, "user", f"hi {i}")
        es.insert(sess.id, "assistant", f"yo {i}")
    _backdate_episodic(user_id, days=2.0)

    cons = MemoryConsolidator(
        user_id,
        ConsolidatorConfig(consolidate_after_days=1.0, min_session_turns=4),
        llm=_make_stub_llm([]),
    )
    await cons.consolidate_pass()
    wm = state.get(WATERMARK_KEY)
    assert wm is not None


@pytest.mark.asyncio
async def test_consolidate_pass_noop_without_llm(user_id: str) -> None:
    sess = SessionStore(user_id).create("test")
    es = EpisodicStore(user_id)
    for i in range(5):
        es.insert(sess.id, "user", f"hi {i}")
        es.insert(sess.id, "assistant", f"yo {i}")
    _backdate_episodic(user_id, days=2.0)

    cons = MemoryConsolidator(
        user_id,
        ConsolidatorConfig(consolidate_after_days=1.0, min_session_turns=4),
        llm=None,
    )
    stats = await cons.consolidate_pass()
    # Sessions still examined (LLM call happens but returns nothing) so no facts inserted
    assert stats.facts_inserted == 0


# ---------- tick + loop -------------------------------------------------


@pytest.mark.asyncio
async def test_tick_runs_decay_and_consolidate(user_id: str) -> None:
    profile = ProfileStore(user_id)
    profile.set("decayme", "x", source="extracted", confidence=0.5)
    conn = get_connection(user_id)
    backdated = (_utcnow() - timedelta(days=10)).isoformat(sep=" ", timespec="microseconds")
    conn.execute(
        "UPDATE profile_facts SET last_referenced_at = ?, updated_at = ?",
        (backdated, backdated),
    )
    cons = MemoryConsolidator(
        user_id,
        ConsolidatorConfig(half_life_days=2.0, min_confidence=0.01),
        llm=_make_stub_llm([]),
    )
    stats = await cons.tick()
    assert stats.decay.checked == 1
    assert stats.finished_at is not None


# ---------- Importance scoring -----------------------------------------


def test_parse_importance_scores_clean_object() -> None:
    raw = '{"abc": 0.7, "def": 0.2}'
    out = _parse_importance_scores(raw)
    assert out == {"abc": 0.7, "def": 0.2}


def test_parse_importance_scores_strips_fences_and_clamps() -> None:
    raw = '```json\n{"a": 1.5, "b": -0.4, "c": "0.3"}\n```'
    out = _parse_importance_scores(raw)
    assert out == {"a": 1.0, "b": 0.0, "c": 0.3}


def test_parse_importance_scores_garbage_returns_empty() -> None:
    assert _parse_importance_scores("nope") == {}
    assert _parse_importance_scores("") == {}


@pytest.mark.asyncio
async def test_importance_pass_scores_unscored_entries(user_id: str) -> None:
    sess = SessionStore(user_id).create("test")
    es = EpisodicStore(user_id)
    a = es.insert(sess.id, "user", "Hi")
    b = es.insert(sess.id, "user", "I just bought a house in Seattle")
    # Force them to be old enough for the scorer (>5min default)
    conn = get_connection(user_id)
    backdated = (_utcnow() - timedelta(minutes=10)).isoformat(sep=" ", timespec="microseconds")
    conn.execute("UPDATE episodic_memory SET created_at = ?", (backdated,))

    async def stub_llm(_system: str, _user: str) -> str:
        return json.dumps({a: 0.1, b: 0.85})

    cons = MemoryConsolidator(user_id, ConsolidatorConfig(), llm=stub_llm)
    stats = await cons.importance_pass()
    assert stats.examined == 2
    assert stats.scored == 2
    rows = es.get_by_session(sess.id)
    by_id = {r.id: r for r in rows}
    assert by_id[a].importance == 0.1
    assert abs(by_id[b].importance - 0.85) < 1e-6


@pytest.mark.asyncio
async def test_importance_pass_skips_fresh_entries(user_id: str) -> None:
    """Default importance_min_age_minutes=5 should leave brand-new turns alone."""
    sess = SessionStore(user_id).create("test")
    es = EpisodicStore(user_id)
    es.insert(sess.id, "user", "fresh")

    called = False

    async def stub_llm(_s: str, _u: str) -> str:
        nonlocal called
        called = True
        return "{}"

    cons = MemoryConsolidator(user_id, ConsolidatorConfig(), llm=stub_llm)
    stats = await cons.importance_pass()
    assert stats.examined == 0
    assert called is False


@pytest.mark.asyncio
async def test_importance_pass_no_op_without_llm(user_id: str) -> None:
    cons = MemoryConsolidator(user_id, ConsolidatorConfig(), llm=None)
    stats = await cons.importance_pass()
    assert stats.examined == 0
    assert stats.scored == 0


def test_get_top_importance_in_session_excludes_recent_ids(user_id: str) -> None:
    sess = SessionStore(user_id).create("test")
    es = EpisodicStore(user_id)
    a = es.insert(sess.id, "user", "important", importance=0.9)
    b = es.insert(sess.id, "user", "noise", importance=0.1)
    c = es.insert(sess.id, "user", "huge", importance=0.95)
    rows = es.get_top_importance_in_session(sess.id, limit=5, exclude_ids=[c])
    contents = [r.content for r in rows]
    assert "important" in contents
    assert "noise" not in contents       # below default threshold
    assert "huge" not in contents        # excluded


@pytest.mark.asyncio
async def test_run_loop_respects_stop_event(user_id: str) -> None:
    cons = MemoryConsolidator(
        user_id,
        ConsolidatorConfig(sleep_min_seconds=10, sleep_max_seconds=10),
        llm=_make_stub_llm([]),
    )
    stop = asyncio.Event()
    task = asyncio.create_task(cons.run_loop(stop))
    # Let it tick once, then signal stop
    await asyncio.sleep(0.5)
    stop.set()
    await asyncio.wait_for(task, timeout=2.0)
    assert task.done()
