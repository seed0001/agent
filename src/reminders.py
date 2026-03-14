"""
Time-based reminders (e.g. Chance: morning 8–9, evening 7–8).
"""
import json
from datetime import datetime
from pathlib import Path

from config.settings import USER_PROFILES_DIR

# Chance reminder windows (local time): hour 8 or 9 = morning, hour 19 or 20 = evening
CHANCE_MORNING_HOURS = (8, 9)
CHANCE_EVENING_HOURS = (19, 20)


def _state_path(user_id: str = "default") -> Path:
    return USER_PROFILES_DIR / user_id / "chance_reminder_state.json"


def is_chance_window() -> bool:
    """True if current local time is in morning (8–9) or evening (7–8) window."""
    hour = datetime.now().hour
    return hour in CHANCE_MORNING_HOURS or hour in CHANCE_EVENING_HOURS


def _get_current_window() -> str | None:
    """'morning' or 'evening' if in window, else None."""
    hour = datetime.now().hour
    if hour in CHANCE_MORNING_HOURS:
        return "morning"
    if hour in CHANCE_EVENING_HOURS:
        return "evening"
    return None


def _load_state(user_id: str = "default") -> dict:
    p = _state_path(user_id)
    if not p.exists():
        return {}
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(state: dict, user_id: str = "default") -> None:
    p = _state_path(user_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def should_send_chance_reminder(user_id: str = "default") -> bool:
    """True if we're in a Chance window and haven't sent for that window today."""
    window = _get_current_window()
    if not window:
        return False
    state = _load_state(user_id)
    today = datetime.now().strftime("%Y-%m-%d")
    return state.get(window) != today


def record_chance_reminder_sent(user_id: str = "default") -> None:
    """Record that we sent a Chance reminder for the current window today."""
    window = _get_current_window()
    if not window:
        return
    state = _load_state(user_id)
    today = datetime.now().strftime("%Y-%m-%d")
    state[window] = today
    _save_state(state, user_id)


def get_chance_reminder_message() -> str:
    """Template for the Chance reminder."""
    hour = datetime.now().hour
    if hour in CHANCE_MORNING_HOURS:
        return "Hey, good morning. Reminder to check on Chance—food, water, and let him out when you're ready."
    return "Hey, reminder to check on Chance—food, water, and see if he needs to come back in or go out."
