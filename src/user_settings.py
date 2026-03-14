"""User settings (voice, etc.) persisted per profile."""
import json
from pathlib import Path

from config.settings import USER_PROFILES_DIR


def _settings_path(user_id: str = "default") -> Path:
    return USER_PROFILES_DIR / user_id / "user_settings.json"


def get_settings(user_id: str = "default") -> dict:
    path = _settings_path(user_id)
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def set_setting(key: str, value: str | None, user_id: str = "default") -> None:
    path = _settings_path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = get_settings(user_id)
    if value is None:
        data.pop(key, None)
    else:
        data[key] = value
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def get_tts_voice(user_id: str = "default") -> str:
    from config.settings import EDGE_TTS_VOICE
    s = get_settings(user_id)
    return s.get("tts_voice") or EDGE_TTS_VOICE
