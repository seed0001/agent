"""Semantic embeddings for episodic + profile memory.

Two cooperating pieces:

- ``EmbeddingService``: wraps a ``sentence-transformers`` model. Lazy load,
  process-wide singleton, thread-safe. Defaults to ``all-MiniLM-L6-v2`` —
  384-dim, ~80 MB, fast on CPU. Override with the ``MEMORY_EMBED_MODEL`` env
  var if you want something bigger.
- ``SemanticIndex``: persists vectors as ``float32`` BLOBs in the
  ``semantic_embeddings`` table that already exists in the schema. Search is
  brute-force cosine — fine up to ~50k rows on CPU. We can swap in
  ``sqlite-vec`` later without changing the consumer API.

The whole module degrades gracefully: if ``sentence-transformers`` or
``numpy`` won't import for any reason (model download blocked, torch absent),
``EmbeddingService.is_available`` returns False and the consolidator's
``embed_pass`` becomes a no-op. The agent keeps working without similarity
search.
"""
from __future__ import annotations

import logging
import os
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from typing import Iterable

from src.agent.memory_db import get_connection, transaction

logger = logging.getLogger(__name__)

DEFAULT_MODEL = os.environ.get("MEMORY_EMBED_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
DEFAULT_DEVICE = os.environ.get("MEMORY_EMBED_DEVICE", "")  # "" = auto-detect


def _pick_device() -> str:
    """Pick a device string for SentenceTransformer.

    Defaults to CPU and never probes torch.cuda — some torch builds (notably
    ROCm wheels on Windows) crash with ACCESS_VIOLATION when CUDA is
    probed without a real GPU runtime. Explicitly opt into GPU with the
    ``MEMORY_EMBED_DEVICE`` env var (e.g. ``cuda``, ``mps``, ``hip``).

    Required because some torch builds also ship without
    ``torch.distributed.is_initialized`` and SentenceTransformer's
    ``get_device_name`` calls it unconditionally during auto-detect — passing
    an explicit device sidesteps that path entirely.
    """
    if DEFAULT_DEVICE:
        return DEFAULT_DEVICE
    return "cpu"


def _shim_torch_distributed() -> None:
    """Some torch wheels (ROCm, CPU-only) omit ``torch.distributed.is_initialized``.
    Sentence-transformers calls it indirectly. Patch a no-op so we don't
    crash on a missing attribute."""
    try:
        import torch
        td = getattr(torch, "distributed", None)
        if td is not None and not hasattr(td, "is_initialized"):
            td.is_initialized = lambda: False  # type: ignore[attr-defined]
    except Exception:
        pass


def _hide_gpus() -> None:
    """Hide every GPU runtime from torch *before* it's imported.

    ROCm wheels probe HIP/CUDA on import; on a Windows host without a real
    ROCm runtime that probe can crash the process with ACCESS_VIOLATION.
    Setting these env vars to '-1' disables enumeration entirely. Skipped if
    the caller explicitly opted into a GPU device via ``MEMORY_EMBED_DEVICE``.
    """
    if DEFAULT_DEVICE and DEFAULT_DEVICE.lower() not in ("cpu", ""):
        return
    for var in ("CUDA_VISIBLE_DEVICES", "HIP_VISIBLE_DEVICES",
                "ROCR_VISIBLE_DEVICES", "GPU_DEVICE_ORDINAL"):
        os.environ.setdefault(var, "-1")


# ---------- Embedding service ----------


class EmbeddingService:
    """Lazy, process-wide embedding model. Safe to instantiate per-profile —
    they all share the loaded model under the hood."""

    _model_lock = threading.Lock()
    _model = None
    _model_name: str | None = None
    _import_failed = False

    def __init__(self, model_name: str = DEFAULT_MODEL):
        self.model_name = model_name

    @classmethod
    def _try_load(cls, model_name: str):
        if cls._import_failed:
            return None
        with cls._model_lock:
            if cls._model is not None and cls._model_name == model_name:
                return cls._model
            # Order matters: hide GPUs and shim torch.distributed BEFORE
            # sentence-transformers imports trigger torch CUDA/HIP probes.
            _hide_gpus()
            _shim_torch_distributed()
            try:
                from sentence_transformers import SentenceTransformer
            except Exception as e:
                logger.warning("sentence-transformers unavailable: %s", e)
                cls._import_failed = True
                return None
            device = _pick_device()
            try:
                cls._model = SentenceTransformer(model_name, device=device)
                cls._model_name = model_name
                logger.info("loaded embedding model: %s on device=%s", model_name, device)
            except Exception as e:
                logger.exception(
                    "failed to load embedding model %s on %s: %s", model_name, device, e
                )
                cls._import_failed = True
                cls._model = None
                return None
        return cls._model

    @property
    def is_available(self) -> bool:
        return self._try_load(self.model_name) is not None

    @property
    def dimensions(self) -> int:
        m = self._try_load(self.model_name)
        if m is None:
            return 0
        try:
            return int(m.get_sentence_embedding_dimension())
        except Exception:
            return 0

    def embed(self, text: str):
        m = self._try_load(self.model_name)
        if m is None:
            return None
        import numpy as np

        v = m.encode(text, normalize_embeddings=True)
        return np.asarray(v, dtype="float32")

    def embed_batch(self, texts: list[str]):
        m = self._try_load(self.model_name)
        if m is None:
            return None
        import numpy as np

        v = m.encode(texts, normalize_embeddings=True, batch_size=32, show_progress_bar=False)
        return np.asarray(v, dtype="float32")


# ---------- SemanticIndex ----------


@dataclass
class SimilarHit:
    score: float
    embedding_id: str
    source_table: str
    source_id: str
    content: str


class SemanticIndex:
    """Persistence + search over the ``semantic_embeddings`` table."""

    def __init__(self, user_id: str = "default", service: EmbeddingService | None = None):
        self.user_id = user_id
        self.service = service or EmbeddingService()

    def _conn(self) -> sqlite3.Connection:
        return get_connection(self.user_id)

    # ---- writes ----

    def add(self, source_table: str, source_id: str, content: str) -> str | None:
        """Embed ``content`` and persist. Returns embedding id or None if the
        embedding service is unavailable."""
        if not self.service.is_available:
            return None
        vec = self.service.embed(content)
        if vec is None:
            return None
        return self._insert_vector(source_table, source_id, content, vec)

    def add_many(self, batch: list[tuple[str, str, str]]) -> dict[str, str]:
        """Bulk-embed a list of ``(source_table, source_id, content)`` tuples.
        Returns ``{source_id: embedding_id}`` for everything inserted."""
        if not batch or not self.service.is_available:
            return {}
        texts = [c for (_, _, c) in batch]
        vecs = self.service.embed_batch(texts)
        if vecs is None:
            return {}
        out: dict[str, str] = {}
        with transaction(self.user_id) as conn:
            for (st, sid, content), vec in zip(batch, vecs):
                eid = self._insert_vector(st, sid, content, vec, conn=conn)
                if eid:
                    out[sid] = eid
        return out

    def _insert_vector(self, source_table: str, source_id: str, content: str,
                       vec, conn: sqlite3.Connection | None = None) -> str:
        c = conn or self._conn()
        eid = uuid.uuid4().hex
        dims = int(vec.shape[0])
        c.execute(
            "INSERT INTO semantic_embeddings "
            "(id, source_table, source_id, content, vector, dimensions, model) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (eid, source_table, source_id, content, vec.tobytes(), dims,
             self.service.model_name),
        )
        # Backref on the originating row, when the schema supports it
        if source_table == "episodic_memory":
            c.execute(
                "UPDATE episodic_memory SET embedding_id = ? WHERE id = ?",
                (eid, source_id),
            )
        return eid

    # ---- reads ----

    def find_similar_text(self, query: str, limit: int = 5,
                          source_table: str | None = "episodic_memory",
                          min_score: float = 0.35,
                          exclude_source_ids: Iterable[str] | None = None) -> list[SimilarHit]:
        if not self.service.is_available or not query.strip():
            return []
        qvec = self.service.embed(query)
        if qvec is None:
            return []
        return self.find_similar_vector(qvec, limit, source_table, min_score, exclude_source_ids)

    def find_similar_vector(self, qvec, limit: int = 5,
                            source_table: str | None = "episodic_memory",
                            min_score: float = 0.35,
                            exclude_source_ids: Iterable[str] | None = None) -> list[SimilarHit]:
        import numpy as np

        excl = set(exclude_source_ids or [])
        if source_table:
            rows = self._conn().execute(
                "SELECT id, source_table, source_id, content, vector, dimensions "
                "FROM semantic_embeddings WHERE source_table = ?",
                (source_table,),
            ).fetchall()
        else:
            rows = self._conn().execute(
                "SELECT id, source_table, source_id, content, vector, dimensions "
                "FROM semantic_embeddings"
            ).fetchall()
        if not rows:
            return []
        # Build a (N, D) matrix once and do a single dot product. Vectors are
        # stored normalized, so dot == cosine.
        vectors = []
        meta: list[sqlite3.Row] = []
        for r in rows:
            if r["source_id"] in excl:
                continue
            arr = np.frombuffer(r["vector"], dtype="float32")
            if arr.shape[0] != int(r["dimensions"]):
                continue
            vectors.append(arr)
            meta.append(r)
        if not vectors:
            return []
        mat = np.stack(vectors)
        q = np.asarray(qvec, dtype="float32")
        n = float(np.linalg.norm(q))
        if n == 0:
            return []
        q = q / n
        scores = mat @ q
        idx_sorted = np.argsort(-scores)
        out: list[SimilarHit] = []
        for i in idx_sorted[: limit * 2]:
            score = float(scores[int(i)])
            if score < min_score:
                continue
            r = meta[int(i)]
            out.append(SimilarHit(
                score=score,
                embedding_id=r["id"],
                source_table=r["source_table"],
                source_id=r["source_id"],
                content=r["content"],
            ))
            if len(out) >= limit:
                break
        return out

    # ---- maintenance ----

    def count(self) -> int:
        row = self._conn().execute(
            "SELECT COUNT(*) AS n FROM semantic_embeddings"
        ).fetchone()
        return int(row["n"]) if row else 0

    def get_unembedded_episodic_ids(self, limit: int = 50) -> list[tuple[str, str]]:
        """Return ``[(episodic_id, content), ...]`` for episodic rows missing
        an embedding. Used by the consolidator's ``embed_pass``."""
        rows = self._conn().execute(
            "SELECT id, content FROM episodic_memory "
            "WHERE deleted_at IS NULL AND embedding_id IS NULL "
            "ORDER BY rowid ASC LIMIT ?",
            (limit,),
        ).fetchall()
        return [(r["id"], r["content"]) for r in rows]
