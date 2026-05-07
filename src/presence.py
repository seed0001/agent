"""
Online presence: optional website URL and project list for identity context.

She can record a site and track projects when she chooses; nothing is injected
into the prompt until something is saved.

Data: data/presence.json
"""
import json
import uuid
from datetime import datetime
from pathlib import Path

from config.settings import DATA_DIR

PRESENCE_PATH = DATA_DIR / "presence.json"

VALID_PROJECT_STATUSES = ("idea", "in_progress", "live", "paused", "archived")


def _load() -> dict:
    if not PRESENCE_PATH.exists():
        return {"website": None, "projects": []}
    try:
        with open(PRESENCE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {"website": None, "projects": []}


def _save(data: dict) -> None:
    PRESENCE_PATH.parent.mkdir(parents=True, exist_ok=True)
    data["updated_at"] = datetime.now().isoformat()
    with open(PRESENCE_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def has_website() -> bool:
    return bool(_load().get("website"))


def set_website(url: str, host: str = "", description: str = "", notes: str = "") -> str:
    """Record or update her website."""
    url = url.strip()
    if not url:
        return "URL is required."
    data = _load()
    data["website"] = {
        "url": url,
        "host": host.strip(),
        "description": description.strip(),
        "notes": notes.strip(),
        "set_at": datetime.now().isoformat(),
    }
    _save(data)
    return f"Website set: {url}"


def get_website() -> dict | None:
    return _load().get("website")


def add_project(
    name: str,
    description: str = "",
    url: str = "",
    status: str = "idea",
    notes: str = "",
) -> str:
    """Add a project or app. Returns confirmation with project id."""
    name = name.strip()
    if not name:
        return "Project name is required."
    st = status.lower().strip() if status else "idea"
    if st not in VALID_PROJECT_STATUSES:
        st = "idea"
    data = _load()
    project = {
        "id": str(uuid.uuid4())[:8],
        "name": name,
        "description": description.strip(),
        "url": url.strip(),
        "status": st,
        "notes": notes.strip(),
        "created_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat(),
    }
    data.setdefault("projects", []).append(project)
    _save(data)
    return f"Project added ({st}): '{name}' [id: {project['id']}]"


def update_project(
    project_id: str,
    name: str = "",
    description: str = "",
    url: str = "",
    status: str = "",
    notes: str = "",
) -> str:
    """Update an existing project by id."""
    data = _load()
    projects = data.get("projects", [])
    for p in projects:
        if p.get("id") == project_id:
            if name:
                p["name"] = name.strip()
            if description:
                p["description"] = description.strip()
            if url:
                p["url"] = url.strip()
            if status and status.lower() in VALID_PROJECT_STATUSES:
                p["status"] = status.lower()
            if notes:
                p["notes"] = notes.strip()
            p["updated_at"] = datetime.now().isoformat()
            _save(data)
            return f"Project '{p['name']}' updated."
    return f"No project found with id '{project_id}'."


def remove_project(project_id: str) -> str:
    data = _load()
    before = len(data.get("projects", []))
    data["projects"] = [p for p in data.get("projects", []) if p.get("id") != project_id]
    if len(data["projects"]) == before:
        return f"No project found with id '{project_id}'."
    _save(data)
    return f"Project '{project_id}' removed."


def get_all_projects() -> list[dict]:
    return _load().get("projects", [])


def format_for_prompt() -> str:
    """
    Format presence for system prompt injection.
    Returns a brief summary when a website and/or projects are recorded; otherwise empty.
    """
    data = _load()
    website = data.get("website")
    projects = data.get("projects", [])

    lines: list[str] = []
    if website:
        lines.append(f"Your website: {website['url']}")
        if website.get("host"):
            lines.append(f"Host: {website['host']}")
        if website.get("description"):
            lines.append(website["description"])

    active = [p for p in projects if p.get("status") in ("in_progress", "live")]
    if active:
        lines.append(
            "Active projects: "
            + "; ".join(
                f"{p['name']} ({p['status']})" + (f" — {p['url']}" if p.get("url") else "")
                for p in active
            )
        )
    ideas = [p for p in projects if p.get("status") == "idea"]
    if ideas:
        lines.append("Ideas: " + ", ".join(p["name"] for p in ideas))

    return " | ".join(lines)


def get_view() -> dict:
    """For dashboard / memory-view API."""
    data = _load()
    return {
        "website": data.get("website"),
        "projects": data.get("projects", []),
        "has_website": bool(data.get("website")),
    }
