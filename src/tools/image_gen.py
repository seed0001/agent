"""
Image generation via xAI Grok Imagine API (grok-imagine-image).
Usage tracking for budget control. All images saved to IMAGE_OUTPUT_DIR (gitignored).
Image generation and download can take 30-60+ seconds; extended timeouts used.
"""
import base64
import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

from config.settings import DATA_DIR, IMAGE_OUTPUT_DIR, XAI_API_KEY, XAI_BASE_URL
from openai import AsyncOpenAI

IMAGE_GEN_MODEL = "grok-imagine-image"
USAGE_PATH = DATA_DIR / "image_usage.json"
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
    return (
        f"Image generation usage: {today_count}/{limit} today, {remaining} remaining. "
        f"Total all-time: {data.get('total', 0)}. "
        f"Last used: {data.get('last_used', 'never')}"
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
    Generate images from text via xAI Grok Imagine API.
    Returns URL(s) or saves to file. Tracks usage for budget.
    """
    import os
    if not XAI_API_KEY:
        return "Error: XAI_API_KEY not set. Configure in .env for image generation."
    limit = daily_limit
    if limit is None:
        limit = int(os.getenv("IMAGE_GEN_DAILY_LIMIT", str(DEFAULT_DAILY_LIMIT)))
    ok, err = _check_limit(n, limit)
    if not ok:
        return f"Error: {err}"
    try:
        client = AsyncOpenAI(api_key=XAI_API_KEY, base_url=XAI_BASE_URL, timeout=90.0)
        # extra_body for xAI-specific params (aspect_ratio, resolution)
        kwargs: dict[str, Any] = {
            "model": IMAGE_GEN_MODEL,
            "prompt": prompt.strip(),
            "n": min(n, 4),
        }
        if aspect_ratio and aspect_ratio != "auto":
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
                result_lines.append(f"Image {i+1}: {out_path}")
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
        result_lines.insert(0, f"Generated {len(urls)} image(s). All saved to {IMAGE_OUTPUT_DIR}")
        return "\n".join(result_lines)
    except Exception as e:
        return f"Error: {e}"
