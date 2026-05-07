"""Tests for the semantic embedding layer.

These tests stub the embedding model so they're fast and offline-safe. There's
one optional integration test (`test_real_model_smoke`) that exercises the real
sentence-transformers model — skipped by default to keep the suite quick. Run
with ``pytest -m slow`` to include it.
"""
from __future__ import annotations

import shutil
import uuid

import numpy as np
import pytest

from config.settings import USER_PROFILES_DIR
from src.agent.memory_consolidator import ConsolidatorConfig, MemoryConsolidator
from src.agent.memory_db import close_all
from src.agent.memory_embeddings import EmbeddingService, SemanticIndex
from src.agent.memory_stores import EpisodicStore, SessionStore


@pytest.fixture
def user_id() -> str:
    uid = f"emb_{uuid.uuid4().hex[:12]}"
    yield uid
    close_all()
    shutil.rmtree(USER_PROFILES_DIR / uid, ignore_errors=True)


# ---------- Stub embedder ------------------------------------------------


class _StubService(EmbeddingService):
    """Deterministic, content-aware stub. Maps each unique word to its own
    dimension so 'cat' and 'dog' have orthogonal vectors but 'cats' and 'cat'
    overlap heavily. Avoids loading any ML model."""

    DIM = 16
    VOCAB = ["alabama", "seattle", "house", "apartment", "ai", "bots", "python",
             "music", "dog", "cat", "weather", "rain", "code", "movie", "book",
             "car"]

    def __init__(self):
        super().__init__(model_name="stub")

    @property
    def is_available(self) -> bool:
        return True

    @property
    def dimensions(self) -> int:
        return self.DIM

    def _embed_one(self, text: str) -> np.ndarray:
        v = np.zeros(self.DIM, dtype="float32")
        words = text.lower().split()
        for w in words:
            for i, vocab_word in enumerate(self.VOCAB):
                if vocab_word in w:
                    v[i] += 1.0
        n = float(np.linalg.norm(v))
        if n == 0:
            v[0] = 1.0  # avoid all-zero vectors
            return v
        return v / n

    def embed(self, text: str):
        return self._embed_one(text)

    def embed_batch(self, texts: list[str]):
        return np.stack([self._embed_one(t) for t in texts])


# ---------- SemanticIndex ------------------------------------------------


def test_index_add_and_find_finds_related(user_id: str) -> None:
    idx = SemanticIndex(user_id, service=_StubService())
    sess = SessionStore(user_id).create("test")
    es = EpisodicStore(user_id)
    a = es.insert(sess.id, "user", "I bought a house in Alabama")
    b = es.insert(sess.id, "user", "Loving the weather, no rain today")
    c = es.insert(sess.id, "user", "Building AI bots in Python")
    for eid, content in [(a, "I bought a house in Alabama"),
                          (b, "Loving the weather, no rain today"),
                          (c, "Building AI bots in Python")]:
        idx.add("episodic_memory", eid, content)

    hits = idx.find_similar_text("My new house in Seattle", limit=2, min_score=0.0)
    assert hits
    assert hits[0].source_id == a  # 'house' in both


def test_index_excludes_listed_source_ids(user_id: str) -> None:
    idx = SemanticIndex(user_id, service=_StubService())
    sess = SessionStore(user_id).create("test")
    es = EpisodicStore(user_id)
    a = es.insert(sess.id, "user", "house in Alabama")
    b = es.insert(sess.id, "user", "another house statement")
    idx.add("episodic_memory", a, "house in Alabama")
    idx.add("episodic_memory", b, "another house statement")
    hits = idx.find_similar_text("house", limit=5, exclude_source_ids=[a], min_score=0.0)
    ids = [h.source_id for h in hits]
    assert a not in ids
    assert b in ids


def test_index_returns_empty_when_no_data(user_id: str) -> None:
    idx = SemanticIndex(user_id, service=_StubService())
    assert idx.find_similar_text("anything") == []


def test_index_writes_back_embedding_id_on_episodic(user_id: str) -> None:
    idx = SemanticIndex(user_id, service=_StubService())
    sess = SessionStore(user_id).create("test")
    es = EpisodicStore(user_id)
    eid = es.insert(sess.id, "user", "hello world")
    new_emb_id = idx.add("episodic_memory", eid, "hello world")
    # Re-fetch and check the FK column was updated
    row = es.get_by_session(sess.id)[0]
    assert row.embedding_id == new_emb_id


def test_index_get_unembedded_episodic_ids(user_id: str) -> None:
    idx = SemanticIndex(user_id, service=_StubService())
    sess = SessionStore(user_id).create("test")
    es = EpisodicStore(user_id)
    a = es.insert(sess.id, "user", "first")
    b = es.insert(sess.id, "user", "second")
    candidates = idx.get_unembedded_episodic_ids(limit=10)
    assert {c[0] for c in candidates} == {a, b}
    idx.add("episodic_memory", a, "first")
    candidates = idx.get_unembedded_episodic_ids(limit=10)
    assert [c[0] for c in candidates] == [b]


def test_index_add_many_bulk_inserts(user_id: str) -> None:
    idx = SemanticIndex(user_id, service=_StubService())
    sess = SessionStore(user_id).create("test")
    es = EpisodicStore(user_id)
    a = es.insert(sess.id, "user", "rain")
    b = es.insert(sess.id, "user", "code")
    out = idx.add_many([
        ("episodic_memory", a, "rain"),
        ("episodic_memory", b, "code"),
    ])
    assert set(out) == {a, b}
    assert idx.count() == 2


# ---------- Consolidator's embed_pass -----------------------------------


def test_embed_pass_processes_unembedded_entries(user_id: str, monkeypatch) -> None:
    sess = SessionStore(user_id).create("test")
    es = EpisodicStore(user_id)
    for content in ("hi", "I built an AI bot", "I like cats"):
        es.insert(sess.id, "user", content)

    cons = MemoryConsolidator(user_id, ConsolidatorConfig(embed_batch_size=10), llm=None)

    # Inject the stub semantic index instead of building the real one
    from src.agent.memory_embeddings import SemanticIndex
    cons._semantic = SemanticIndex(user_id, service=_StubService())

    stats = cons.embed_pass()
    assert stats.examined == 3
    assert stats.embedded == 3

    # Second pass should have nothing left
    stats2 = cons.embed_pass()
    assert stats2.examined == 0


def test_embed_pass_skips_when_disabled(user_id: str) -> None:
    cons = MemoryConsolidator(
        user_id,
        ConsolidatorConfig(enable_embeddings=False),
        llm=None,
    )
    stats = cons.embed_pass()
    assert stats.embedded == 0
    assert stats.available is False


def test_embed_pass_skips_when_service_unavailable(user_id: str, monkeypatch) -> None:
    cons = MemoryConsolidator(user_id, ConsolidatorConfig(), llm=None)

    class _UnavailableSvc(EmbeddingService):
        def __init__(self): super().__init__(model_name="none")
        @property
        def is_available(self) -> bool: return False
    from src.agent.memory_embeddings import SemanticIndex
    cons._semantic = SemanticIndex(user_id, service=_UnavailableSvc())
    stats = cons.embed_pass()
    assert stats.embedded == 0
    assert stats.available is False


# ---------- Optional real-model smoke test ------------------------------


@pytest.mark.slow
def test_real_model_smoke(user_id: str) -> None:
    """Loads the actual sentence-transformers model — slow on first run because
    it downloads ~80MB. Run with ``pytest -m slow``."""
    svc = EmbeddingService()
    if not svc.is_available:
        pytest.skip("sentence-transformers model unavailable")
    idx = SemanticIndex(user_id, service=svc)
    sess = SessionStore(user_id).create("test")
    es = EpisodicStore(user_id)
    eid = es.insert(sess.id, "user", "I just bought a house in Seattle")
    idx.add("episodic_memory", eid, "I just bought a house in Seattle")
    hits = idx.find_similar_text("real estate purchase in the Pacific Northwest", limit=3, min_score=0.1)
    assert any(h.source_id == eid for h in hits)
