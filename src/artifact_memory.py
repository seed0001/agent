"""Durable memory for files and documents Andrew creates or verifies."""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from config.settings import USER_PROFILES_DIR

ARTIFACTS_PATH = USER_PROFILES_DIR / "default" / "artifacts.json"


def _now() -> str:
    return datetime.now().isoformat()


def _slug(text: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9]+", "-", text.strip().lower()).strip("-")
    return value or "artifact"


def infer_category(path: str, title: str = "") -> str:
    blob = f"{path} {title}".lower()
    if "schedule" in blob:
        return "schedule"
    if "journal" in blob:
        return "journal"
    if "build_prompt" in blob or "build-prompt" in blob or "prompt" in blob:
        return "build_prompt"
    if "idea" in blob:
        return "ideas"
    if "contact" in blob:
        return "contacts"
    if "journey" in blob or "genesis" in blob:
        return "story"
    return "document"


@dataclass
class Artifact:
    id: str
    path: str
    title: str
    category: str = "document"
    summary: str = ""
    exists: bool = True
    size_bytes: int = 0
    source: str = "agent"
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    verified_at: str = field(default_factory=_now)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Artifact":
        return cls(
            id=str(data.get("id") or ""),
            path=str(data.get("path") or ""),
            title=str(data.get("title") or ""),
            category=str(data.get("category") or "document"),
            summary=str(data.get("summary") or ""),
            exists=bool(data.get("exists", True)),
            size_bytes=int(data.get("size_bytes") or 0),
            source=str(data.get("source") or "agent"),
            created_at=str(data.get("created_at") or _now()),
            updated_at=str(data.get("updated_at") or _now()),
            verified_at=str(data.get("verified_at") or _now()),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _load() -> dict[str, Any]:
    if not ARTIFACTS_PATH.exists():
        return {"artifacts": {}}
    try:
        data = json.loads(ARTIFACTS_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("artifacts"), dict):
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return {"artifacts": {}}


def _save(data: dict[str, Any]) -> None:
    ARTIFACTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    data["_updated"] = _now()
    ARTIFACTS_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _artifact_id(path: str) -> str:
    return _slug(str(Path(path).expanduser().resolve(strict=False)))


def record_artifact(
    path: str,
    *,
    title: str = "",
    category: str = "",
    summary: str = "",
    source: str = "agent",
) -> Artifact:
    abs_path = Path(path).expanduser().resolve(strict=False)
    exists = abs_path.is_file()
    size = abs_path.stat().st_size if exists else 0
    title = title or abs_path.name
    category = category or infer_category(str(abs_path), title)
    aid = _artifact_id(str(abs_path))
    now = _now()
    data = _load()
    existing = data["artifacts"].get(aid, {})
    artifact = Artifact(
        id=aid,
        path=str(abs_path),
        title=title,
        category=category,
        summary=summary or existing.get("summary", ""),
        exists=exists,
        size_bytes=size,
        source=source,
        created_at=existing.get("created_at") or now,
        updated_at=now,
        verified_at=now if exists else existing.get("verified_at") or "",
    )
    data["artifacts"][aid] = artifact.to_dict()
    _save(data)
    return artifact


def get_artifact(identifier: str) -> Artifact | None:
    ident = (identifier or "").strip()
    if not ident:
        return None
    data = _load().get("artifacts", {})
    if ident in data:
        return Artifact.from_dict(data[ident])
    ident_low = ident.lower()
    for raw in data.values():
        art = Artifact.from_dict(raw)
        if ident_low in art.path.lower() or ident_low in art.title.lower():
            return art
    return None


def list_artifacts(category: str = "", include_missing: bool = False) -> list[Artifact]:
    rows = [Artifact.from_dict(a) for a in _load().get("artifacts", {}).values()]
    if category:
        rows = [a for a in rows if a.category == category]
    if not include_missing:
        rows = [a for a in rows if a.exists]
    rows.sort(key=lambda a: a.updated_at, reverse=True)
    return rows


def search_artifacts(query: str, limit: int = 10) -> list[Artifact]:
    q_words = {w for w in re.findall(r"[a-zA-Z0-9']{3,}", (query or "").lower())}
    rows = list_artifacts(include_missing=True)
    if not q_words:
        return rows[:limit]

    scored: list[tuple[int, Artifact]] = []
    for art in rows:
        blob = f"{art.title} {art.category} {art.summary} {art.path}".lower()
        score = sum(1 for w in q_words if w in blob)
        if score:
            scored.append((score, art))
    scored.sort(key=lambda x: (x[0], x[1].updated_at), reverse=True)
    return [a for _, a in scored[:limit]]


def format_artifact(artifact: Artifact) -> str:
    status = "exists" if artifact.exists else "missing"
    lines = [
        f"{artifact.title} [{artifact.category}] ({status}, {artifact.size_bytes} bytes)",
        f"Path: {artifact.path}",
    ]
    if artifact.summary:
        lines.append(f"Summary: {artifact.summary}")
    lines.append(f"Verified: {artifact.verified_at or 'never'}")
    return "\n".join(lines)


def format_for_context(limit: int = 8) -> str:
    artifacts = list_artifacts(include_missing=False)[:limit]
    if not artifacts:
        return ""
    lines = ["## Saved Files / Artifacts (durable memory)"]
    for art in artifacts:
        lines.append(f"- {art.title} [{art.category}] -> {art.path}")
    return "\n".join(lines)
