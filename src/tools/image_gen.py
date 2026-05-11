"""
Image generation via configured OpenAI-compatible provider.
Usage tracking for budget control. All images saved to IMAGE_OUTPUT_DIR (gitignored).
Image generation and download can take 30-60+ seconds; extended timeouts used.
"""
import base64
import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

from config.settings import (
    DATA_DIR,
    IMAGE_OUTPUT_DIR,
    get_api_key,
    get_api_key_env_name,
    get_base_url,
    get_image_model,
    get_llm_provider,
)
from openai import AsyncOpenAI

USAGE_PATH = DATA_DIR / "image_usage.json"
METADATA_PATH = DATA_DIR / "generated_images.jsonl"
DEFAULT_DAILY_LIMIT = 20  # configurable via env


def _load_usage() -> dict[str, Any]:
    """Load usage stats: {date: count, total: N}."""
    if not USAGE_PATH.exists():
        return {"by_date": {}, "total": 0}
    try:
        with open(USAGE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {"by_date": {}, "total": 0}


def _save_usage(data: dict) -> None:
    USAGE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(USAGE_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def _inc_usage(count: int = 1) -> dict[str, Any]:
    today = str(date.today())
    data = _load_usage()
    by_date = data.get("by_date", {})
    by_date[today] = by_date.get(today, 0) + count
    data["by_date"] = by_date
    data["total"] = data.get("total", 0) + count
    data["last_used"] = datetime.now().isoformat()
    _save_usage(data)
    return data


def get_usage_data() -> dict:
    """Return structured usage for dashboard: today, limit, remaining, total."""
    import os
    limit = int(os.getenv("IMAGE_GEN_DAILY_LIMIT", str(DEFAULT_DAILY_LIMIT)))
    data = _load_usage()
    today = str(date.today())
    today_count = data.get("by_date", {}).get(today, 0)
    remaining = max(0, limit - today_count)
    return {"today": today_count, "limit": limit, "remaining": remaining, "total": data.get("total", 0)}


def get_image_usage(daily_limit: int | None = None) -> str:
    """
    Return current image generation usage for budget tracking.
    Use before generate_image to check remaining quota.
    """
    import os
    limit = daily_limit
    if limit is None:
        limit = int(os.getenv("IMAGE_GEN_DAILY_LIMIT", str(DEFAULT_DAILY_LIMIT)))
    data = _load_usage()
    today = str(date.today())
    today_count = data.get("by_date", {}).get(today, 0)
    remaining = max(0, limit - today_count)
    recent_line = ""
    recent = list_generated_images(limit=3)
    if recent:
        recent_names = ", ".join(Path(item["path"]).name for item in recent)
        recent_line = f" Recent files: {recent_names}."
    return (
        f"Image generation usage: {today_count}/{limit} today, {remaining} remaining. "
        f"Total all-time: {data.get('total', 0)}. "
        f"Last used: {data.get('last_used', 'never')}"
        f"{recent_line}"
    )


def _check_limit(count: int, daily_limit: int) -> tuple[bool, str]:
    """Return (ok, message)."""
    data = _load_usage()
    today = str(date.today())
    today_count = data.get("by_date", {}).get(today, 0)
    if today_count + count > daily_limit:
        return False, (
            f"Daily limit ({daily_limit}) would be exceeded: {today_count} used today, "
            f"{count} requested. Try again tomorrow or increase IMAGE_GEN_DAILY_LIMIT."
        )
    return True, ""


async def generate_image(
    prompt: str,
    *,
    n: int = 1,
    aspect_ratio: str = "1:1",
    save_path: str | None = None,
    daily_limit: int | None = None,
) -> str:
    """
    Generate images from text via configured provider image API.
    Returns URL(s) or saves to file. Tracks usage for budget.
    """
    import os
    provider = get_llm_provider()
    api_key = get_api_key()
    if not api_key:
        key_name = get_api_key_env_name()
        return f"Error: {key_name} not set. Configure in .env for image generation."
    model = get_image_model()
    if not model:
        return (
            "Error: image generation model is not configured for this provider. "
            "Set MISTRAL_IMAGE_MODEL in .env to a supported model."
        )
    limit = daily_limit
    if limit is None:
        limit = int(os.getenv("IMAGE_GEN_DAILY_LIMIT", str(DEFAULT_DAILY_LIMIT)))
    ok, err = _check_limit(n, limit)
    if not ok:
        return f"Error: {err}"
    try:
        client = AsyncOpenAI(api_key=api_key, base_url=get_base_url(), timeout=90.0)
        # xAI supports aspect_ratio via extra_body; other providers ignore this path.
        kwargs: dict[str, Any] = {
            "model": model,
            "prompt": prompt.strip(),
            "n": min(n, 4),
        }
        if provider == "xai" and aspect_ratio and aspect_ratio != "auto":
            kwargs["extra_body"] = {"aspect_ratio": aspect_ratio}
        response = await client.images.generate(**kwargs)
        urls = []
        for img in (response.data or []):
            url = getattr(img, "url", None) or getattr(img, "b64_json", None)
            if url:
                urls.append(url)
        if not urls:
            return "Error: No image returned from API."
        _inc_usage(len(urls))
        IMAGE_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        date_str = date.today().isoformat()
        slug = re.sub(r"[^\w\-]", "", prompt.strip()[:40]) or "image"
        saved_paths: list[Path] = []
        result_lines: list[str] = []
        metadata_items: list[dict[str, Any]] = []
        now_iso = datetime.now().isoformat()
        for i, u in enumerate(urls):
            try:
                if u.startswith("http"):
                    import httpx
                    async with httpx.AsyncClient(timeout=90.0) as c:
                        r = await c.get(u)
                        r.raise_for_status()
                        raw = r.content
                elif u.startswith("data:") and ";base64," in u:
                    b64 = u.split(";base64,", 1)[1]
                    raw = base64.b64decode(b64)
                elif isinstance(u, str) and len(u) > 100:
                    raw = base64.b64decode(u)
                else:
                    result_lines.append(f"Image {i+1}: unsupported format")
                    continue
                base_name = f"{date_str}_{slug}_{i+1:02d}"
                ext = ".png"  # default; could infer from content-type
                out_path = IMAGE_OUTPUT_DIR / f"{base_name}{ext}"
                out_path.write_bytes(raw)
                saved_paths.append(out_path)
                meta = {
                    "created_at": now_iso,
                    "prompt": prompt.strip(),
                    "aspect_ratio": aspect_ratio,
                    "model": model,
                    "index": i + 1,
                    "path": str(out_path.resolve()),
                    "filename": out_path.name,
                    "size_bytes": len(raw),
                }
                metadata_items.append(meta)
                result_lines.append(
                    f"Image {i+1}: {meta['filename']} -> {meta['path']} ({meta['size_bytes']} bytes)"
                )
            except Exception as ex:
                url_preview = (u[:60] + "…") if len(str(u)) > 60 else u
                result_lines.append(f"Image {i+1}: save failed ({ex}) — {url_preview}")
        if save_path and saved_paths:
            try:
                dest = Path(save_path)
                dest.parent.mkdir(parents=True, exist_ok=True)
                if not dest.suffix:
                    dest = dest.with_suffix(".png")
                dest.write_bytes(saved_paths[0].read_bytes())
                result_lines.append(f"Copy to: {dest}")
            except Exception as ex:
                result_lines.append(f"Copy to {save_path} failed: {ex}")
        for item in metadata_items:
            _append_metadata(item)
        result_lines.insert(
            0,
            f"Generated {len(saved_paths)} of {len(urls)} image(s). Output dir: {IMAGE_OUTPUT_DIR.resolve()}",
        )
        if metadata_items:
            result_lines.insert(1, f"Prompt: {prompt.strip()}")
            result_lines.insert(2, f"Metadata log: {METADATA_PATH.resolve()}")
        return "\n".join(result_lines)
    except Exception as e:
        return f"Error: {e}"


def _append_metadata(entry: dict[str, Any]) -> None:
    METADATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(METADATA_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def list_generated_images(limit: int = 20) -> list[dict[str, Any]]:
    """Return recent generated image metadata entries (newest first)."""
    if not METADATA_PATH.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        with open(METADATA_PATH, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(item, dict):
                    rows.append(item)
    except OSError:
        return []
    rows.sort(key=lambda x: str(x.get("created_at", "")), reverse=True)
    return rows[: max(1, int(limit))]


def get_recent_images(limit: int = 10) -> str:
    """Human-readable listing of recent generated images."""
    items = list_generated_images(limit=limit)
    if not items:
        return "No generated images recorded yet."
    lines = [f"Recent generated images (showing {len(items)}):"]
    for i, item in enumerate(items, start=1):
        lines.append(
            f"{i}. {item.get('filename', 'unknown')} | path={item.get('path', '')} | "
            f"prompt={item.get('prompt', '')}"
        )
    return "\n".join(lines)
