"""Unit tests for the SQLite memory stores.

Each test gets an isolated user profile so they can run in parallel and
never touch the default ``data/profiles/default/`` data.
"""
from __future__ import annotations

import shutil
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)

import pytest

from config.settings import USER_PROFILES_DIR
from src.agent.memory_db import close_all, db_path, get_connection, schema_version
from src.agent.memory_stores import (
    ContextWindow,
    EpisodicStore,
    ProfileStore,
    SessionStore,
    ThoughtStore,
    WorkingState,
)


@pytest.fixture
def user_id() -> str:
    """Unique profile per test. Cleaned up after."""
    uid = f"test_{uuid.uuid4().hex[:12]}"
    yield uid
    # Close any open connection to this profile, then nuke its directory.
    close_all()
    shutil.rmtree(USER_PROFILES_DIR / uid, ignore_errors=True)


# ---------- Schema -----------------------------------------------------------


def test_schema_applies_at_version_1(user_id: str) -> None:
    get_connection(user_id)
    assert schema_version(user_id) == 1


def test_schema_is_idempotent(user_id: str) -> None:
    get_connection(user_id)
    v1 = schema_version(user_id)
    get_connection(user_id)
    v2 = schema_version(user_id)
    assert v1 == v2 == 1


def test_db_path_is_per_profile(user_id: str) -> None:
    get_connection(user_id)
    assert db_path(user_id).exists()
    assert db_path(user_id).parent.name == user_id


# ---------- SessionStore -----------------------------------------------------


def test_session_create_and_get(user_id: str) -> None:
    s = SessionStore(user_id)
    sess = s.create("web", channel_id="tab-1", human_user_id="travis")
    assert sess.id
    assert sess.source == "web"
    assert sess.channel_id == "tab-1"
    assert sess.user_id == "travis"
    assert sess.ended_at is None

    fetched = s.get(sess.id)
    assert fetched is not None
    assert fetched.id == sess.id
    assert fetched.source == "web"


def test_session_touch_updates_activity(user_id: str) -> None:
    s = SessionStore(user_id)
    sess = s.create("discord")
    original = s.get(sess.id).last_activity_at
    time.sleep(1.1)
    s.touch(sess.id)
    updated = s.get(sess.id).last_activity_at
    assert updated > original


def test_session_end_sets_ended_at(user_id: str) -> None:
    s = SessionStore(user_id)
    sess = s.create("cli")
    assert s.get(sess.id).ended_at is None
    s.end(sess.id)
    assert s.get(sess.id).ended_at is not None


def test_session_list_recent_orders_by_activity(user_id: str) -> None:
    s = SessionStore(user_id)
    a = s.create("web")
    time.sleep(1.1)
    b = s.create("discord")
    recents = s.list_recent(limit=10)
    assert [r.id for r in recents[:2]] == [b.id, a.id]


def test_session_list_recent_filter_by_source(user_id: str) -> None:
    s = SessionStore(user_id)
    s.create("web")
    s.create("discord")
    s.create("web")
    only_web = s.list_recent(limit=10, source="web")
    assert all(r.source == "web" for r in only_web)
    assert len(only_web) == 2


# ---------- EpisodicStore ----------------------------------------------------


def test_episodic_insert_and_get_by_session(user_id: str) -> None:
    sess = SessionStore(user_id).create("web")
    e = EpisodicStore(user_id)
    eid = e.insert(sess.id, "user", "hello", source="web", importance=0.7)
    assert eid

    rows = e.get_by_session(sess.id)
    assert len(rows) == 1
    assert rows[0].content == "hello"
    assert rows[0].role == "user"
    assert rows[0].importance == 0.7


def test_episodic_get_recent_in_session_returns_chronological(user_id: str) -> None:
    sess = SessionStore(user_id).create("web")
    e = EpisodicStore(user_id)
    e.insert(sess.id, "user", "first")
    time.sleep(1.1)
    e.insert(sess.id, "assistant", "second")
    time.sleep(1.1)
    e.insert(sess.id, "user", "third")
    recent = e.get_recent_in_session(sess.id, limit=2)
    assert [r.content for r in recent] == ["second", "third"]


def test_episodic_cross_session_excludes_current(user_id: str) -> None:
    ss = SessionStore(user_id)
    e = EpisodicStore(user_id)
    s1 = ss.create("web")
    e.insert(s1.id, "user", "from session 1")
    s2 = ss.create("web")
    e.insert(s2.id, "user", "from session 2")

    cross = e.get_recent_across_sessions(limit=10, exclude_session=s2.id)
    assert all(c.session_id == s1.id for c in cross)
    assert len(cross) == 1


def test_episodic_importance_clamped_to_unit(user_id: str) -> None:
    sess = SessionStore(user_id).create("web")
    e = EpisodicStore(user_id)
    eid_high = e.insert(sess.id, "user", "x", importance=99.0)
    eid_low = e.insert(sess.id, "user", "y", importance=-2.0)
    rows = {r.id: r for r in e.get_by_session(sess.id)}
    assert rows[eid_high].importance == 1.0
    assert rows[eid_low].importance == 0.0


def test_episodic_soft_delete_hides_from_queries(user_id: str) -> None:
    sess = SessionStore(user_id).create("web")
    e = EpisodicStore(user_id)
    eid = e.insert(sess.id, "user", "doomed")
    assert e.count() == 1
    e.soft_delete(eid)
    assert e.count() == 0
    assert e.get_by_session(sess.id) == []


def test_episodic_session_cascade_delete(user_id: str) -> None:
    sess = SessionStore(user_id).create("web")
    e = EpisodicStore(user_id)
    e.insert(sess.id, "user", "a")
    e.insert(sess.id, "assistant", "b")
    conn = get_connection(user_id)
    conn.execute("DELETE FROM sessions WHERE id = ?", (sess.id,))
    assert e.count() == 0


# ---------- ProfileStore -----------------------------------------------------


def test_profile_set_creates_version_1(user_id: str) -> None:
    p = ProfileStore(user_id)
    p.set("name", "Travis", source="user")
    fact = p.get("name")
    assert fact is not None
    assert fact.value == "Travis"
    assert fact.version == 1
    assert fact.protected is True  # user-source = auto-protected
    assert fact.source == "user"


def test_profile_set_again_versions_and_soft_deletes_previous(user_id: str) -> None:
    p = ProfileStore(user_id)
    p.set("name", "Travis", source="user")
    p.set("name", "Sir Travis", source="user")
    fact = p.get("name")
    assert fact.value == "Sir Travis"
    assert fact.version == 2
    history = p.version_history("name")
    assert len(history) == 2
    assert history[0].version == 2
    assert history[1].version == 1


def test_profile_extracted_facts_not_protected_by_default(user_id: str) -> None:
    p = ProfileStore(user_id)
    p.set("hobby", "AI bots", source="extracted")
    fact = p.get("hobby")
    assert fact.source == "extracted"
    assert fact.protected is False


def test_profile_get_top_orders_by_confidence(user_id: str) -> None:
    p = ProfileStore(user_id)
    p.set("a", "1", source="extracted", confidence=0.3)
    p.set("b", "2", source="extracted", confidence=0.9)
    p.set("c", "3", source="extracted", confidence=0.6)
    top = p.get_top(limit=3)
    assert [f.key for f in top] == ["b", "c", "a"]


def test_profile_get_top_min_confidence_filter(user_id: str) -> None:
    p = ProfileStore(user_id)
    p.set("low", "x", source="extracted", confidence=0.1)
    p.set("high", "y", source="extracted", confidence=0.8)
    top = p.get_top(limit=10, min_confidence=0.5)
    assert len(top) == 1
    assert top[0].key == "high"


def test_profile_reinforce_bumps_confidence_and_refreshes_timestamp(user_id: str) -> None:
    p = ProfileStore(user_id)
    p.set("hobby", "AI bots", source="extracted", confidence=0.5)
    before = p.get("hobby")
    assert before.last_referenced_at is None
    assert p.reinforce("hobby") is True
    after = p.get("hobby")
    assert after.confidence > before.confidence
    assert after.last_referenced_at is not None


def test_profile_reinforce_caps_at_one(user_id: str) -> None:
    p = ProfileStore(user_id)
    p.set("k", "v", source="extracted", confidence=0.95)
    for _ in range(20):
        p.reinforce("k")
    assert p.get("k").confidence == 1.0


def test_profile_reinforce_missing_returns_false(user_id: str) -> None:
    p = ProfileStore(user_id)
    assert p.reinforce("nonexistent") is False


def test_profile_protect_makes_immortal(user_id: str) -> None:
    p = ProfileStore(user_id)
    p.set("hobby", "x", source="extracted", confidence=0.5)
    assert p.get("hobby").protected is False
    assert p.protect("hobby") is True
    assert p.get("hobby").protected is True
    assert p.protect("hobby", protected=False) is True
    assert p.get("hobby").protected is False


def test_profile_delete_soft_deletes(user_id: str) -> None:
    p = ProfileStore(user_id)
    p.set("k", "v", source="extracted")
    assert p.delete("k") is True
    assert p.get("k") is None
    assert p.delete("k") is False
    assert len(p.version_history("k")) == 1


def test_profile_decay_skips_protected_and_user_facts(user_id: str) -> None:
    p = ProfileStore(user_id)
    p.set("name", "Travis", source="user")
    p.set("locked", "always", source="extracted")
    p.protect("locked")
    stats = p.decay(half_life_days=0.001, min_confidence=0.99)
    assert p.get("name") is not None
    assert p.get("locked") is not None
    assert stats.reinforced == 2
    assert stats.removed == 0


def test_profile_decay_grace_period_first_day(user_id: str) -> None:
    """A fact updated less than 24h ago should never decay on this pass."""
    p = ProfileStore(user_id)
    p.set("fresh", "v", source="extracted", confidence=0.5)
    stats = p.decay(half_life_days=1.0, min_confidence=0.4)
    assert p.get("fresh").confidence == 0.5
    assert stats.decayed == 0
    assert stats.removed == 0


def test_profile_decay_shrinks_old_unprotected_facts(user_id: str) -> None:
    p = ProfileStore(user_id)
    p.set("old", "v", source="extracted", confidence=0.9)
    # Manually backdate the row so decay sees it as ancient
    conn = get_connection(user_id)
    backdated = (_utc_now() - timedelta(days=5)).isoformat(sep=" ", timespec="microseconds")
    conn.execute(
        "UPDATE profile_facts SET last_referenced_at = ?, updated_at = ? "
        "WHERE key = ? AND deleted_at IS NULL",
        (backdated, backdated, "old"),
    )
    stats = p.decay(half_life_days=2.0, min_confidence=0.01)
    fact = p.get("old")
    assert fact is not None
    assert fact.confidence < 0.9
    assert stats.decayed == 1


def test_profile_decay_removes_below_floor(user_id: str) -> None:
    p = ProfileStore(user_id)
    p.set("dying", "v", source="extracted", confidence=0.3)
    conn = get_connection(user_id)
    backdated = (_utc_now() - timedelta(days=100)).isoformat(sep=" ", timespec="microseconds")
    conn.execute(
        "UPDATE profile_facts SET last_referenced_at = ?, updated_at = ? "
        "WHERE key = ? AND deleted_at IS NULL",
        (backdated, backdated, "dying"),
    )
    stats = p.decay(half_life_days=1.0, min_confidence=0.25)
    assert p.get("dying") is None
    assert stats.removed == 1


def test_profile_reinforce_many_returns_count(user_id: str) -> None:
    p = ProfileStore(user_id)
    p.set("a", "1", source="extracted", confidence=0.5)
    p.set("b", "2", source="extracted", confidence=0.5)
    n = p.reinforce_many(["a", "b", "c"])
    assert n == 2


def test_profile_invalid_inputs_raise(user_id: str) -> None:
    p = ProfileStore(user_id)
    with pytest.raises(ValueError):
        p.set("", "value")
    with pytest.raises(ValueError):
        p.set("key", "")


def test_profile_get_all_filters_by_category(user_id: str) -> None:
    p = ProfileStore(user_id)
    p.set("name", "Travis", category="personal", source="user")
    p.set("job", "AI dev", category="work", source="user")
    p.set("color", "blue", category="preferences", source="user")
    work = p.get_all(category="work")
    assert len(work) == 1
    assert work[0].key == "job"


# ---------- ThoughtStore -----------------------------------------------------


def test_thought_insert_and_get_recent(user_id: str) -> None:
    t = ThoughtStore(user_id)
    t.insert("a passing thought")
    t.insert("another", delivered=True)
    recent = t.get_recent(limit=10)
    assert [th.content for th in recent] == ["a passing thought", "another"]
    assert recent[1].delivered is True


def test_thought_delivered_only_filter(user_id: str) -> None:
    t = ThoughtStore(user_id)
    t.insert("rejected", reject_reason="too generic")
    tid = t.insert("good one")
    t.mark_delivered(tid)
    delivered = t.get_recent(delivered_only=True)
    assert len(delivered) == 1
    assert delivered[0].content == "good one"


def test_thought_mark_delivered_clears_reject_reason(user_id: str) -> None:
    t = ThoughtStore(user_id)
    tid = t.insert("borderline", reject_reason="short")
    t.mark_delivered(tid)
    recent = t.get_recent(limit=5)
    target = next(th for th in recent if th.id == tid)
    assert target.delivered is True
    assert target.reject_reason is None


# ---------- WorkingState -----------------------------------------------------


def test_working_state_set_and_get_string(user_id: str) -> None:
    w = WorkingState(user_id)
    w.set("speaker", "travis")
    assert w.get("speaker") == "travis"


def test_working_state_set_and_get_complex(user_id: str) -> None:
    w = WorkingState(user_id)
    payload = {"a": 1, "b": [1, 2, 3], "c": {"nested": True}}
    w.set("session", payload)
    assert w.get("session") == payload


def test_working_state_default_for_missing(user_id: str) -> None:
    w = WorkingState(user_id)
    assert w.get("nope") is None
    assert w.get("nope", default="fallback") == "fallback"


def test_working_state_delete(user_id: str) -> None:
    w = WorkingState(user_id)
    w.set("k", "v")
    w.delete("k")
    assert w.get("k") is None


def test_working_state_set_none_deletes(user_id: str) -> None:
    w = WorkingState(user_id)
    w.set("k", "v")
    w.set("k", None)
    assert w.get("k") is None


def test_working_state_all(user_id: str) -> None:
    w = WorkingState(user_id)
    w.set("a", 1)
    w.set("b", "two")
    assert w.all() == {"a": 1, "b": "two"}


# ---------- ContextWindow ----------------------------------------------------


def test_context_window_pushes_within_budget(user_id: str) -> None:
    cw = ContextWindow(max_tokens=1000)
    cw.set_system_prompt("system")
    cw.push("user", "hi", importance=0.5)
    cw.push("assistant", "hello", importance=0.6)
    assert len(cw.messages()) == 2


def test_context_window_evicts_lowest_importance_when_over_budget(user_id: str) -> None:
    # max_tokens=20 -> ~80-char budget. The 200-char message alone exceeds it,
    # forcing the trimmer to evict it once a second message arrives.
    cw = ContextWindow(max_tokens=20)
    cw.push("user", "x" * 200, importance=0.1)              # heavy + low importance
    cw.push("assistant", "important reply", importance=0.95)
    cw.push("user", "second important", importance=0.9)
    contents = [m.content for m in cw.messages()]
    assert "x" * 200 not in contents
    assert "second important" in contents


def test_context_window_never_evicts_most_recent(user_id: str) -> None:
    cw = ContextWindow(max_tokens=20)  # tiny
    cw.push("user", "old low", importance=0.99)
    cw.push("user", "new very low", importance=0.0)
    contents = [m.content for m in cw.messages()]
    assert "new very low" in contents


def test_context_window_total_includes_system(user_id: str) -> None:
    cw = ContextWindow(max_tokens=10_000)
    cw.set_system_prompt("a" * 100)
    cw.push("user", "b" * 100)
    assert cw.total_tokens() >= 50  # both contribute


def test_context_window_clear(user_id: str) -> None:
    cw = ContextWindow(max_tokens=1000)
    cw.push("user", "a")
    cw.clear()
    assert cw.messages() == []
