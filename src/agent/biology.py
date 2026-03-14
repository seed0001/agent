"""
Biological drives, urges, and state.
Based on Hull-style drive theory: accumulation over time, reduction on satisfaction.
Mathematical grounding: Ebbinghaus/Jost for memory, exponential accumulation for drives.
"""
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Drive names
DRIVES = ("connection", "curiosity", "usefulness", "expression")

# Parameters (tunable)
RATE_PER_SEC = 0.0001  # accumulation per second (~0.036/hour, ~0.85/day to saturate)
SATISFACTION_DROP = 0.4  # how much drive drops on satisfaction
THRESHOLD_PROACTIVE = 0.65  # urge must exceed this to trigger proactive outreach
REFRACTORY_SECONDS = 600  # 10 min: no repeat proactive until elapsed
MAX_DRIVE = 1.0
MIN_DRIVE = 0.0


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return t
    except (ValueError, TypeError):
        return None


class DriveState:
    """
    Hull-style drives: accumulate over time, reduce on satisfaction.
    D(t+dt) = D(t) + dt * rate - delta on satisfaction.
    Urge: U = max(0, D - theta) when D > theta.
    """

    def __init__(self, user_dir: Path):
        self.user_dir = Path(user_dir)
        self.user_dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.user_dir / "biology_state.json"

        self.drives: dict[str, float] = {d: 0.0 for d in DRIVES}
        self.last_satisfaction: dict[str, str] = {}
        self.last_proactive_at: str | None = None
        self.last_tick_at: str | None = None

        self._load()

    def _load(self) -> None:
        if not self.state_path.exists():
            return
        try:
            with open(self.state_path, encoding="utf-8") as f:
                data = json.load(f)
            drives = data.get("drives", {})
            for d in DRIVES:
                if d in drives and isinstance(drives[d], (int, float)):
                    self.drives[d] = max(MIN_DRIVE, min(MAX_DRIVE, float(drives[d])))
            self.last_satisfaction = data.get("last_satisfaction") or {}
            self.last_proactive_at = data.get("last_proactive_at")
            self.last_tick_at = data.get("last_tick_at")
        except (OSError, json.JSONDecodeError):
            pass

    def _save(self) -> None:
        data = {
            "drives": self.drives,
            "last_satisfaction": self.last_satisfaction,
            "last_proactive_at": self.last_proactive_at,
            "last_tick_at": self.last_tick_at,
            "updated_at": _now_utc().isoformat(),
        }
        with open(self.state_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def _tick(self, dt_sec: float) -> None:
        """Advance drives by dt seconds. D += dt * rate, capped at MAX_DRIVE."""
        if dt_sec <= 0:
            return
        for d in DRIVES:
            self.drives[d] = min(MAX_DRIVE, self.drives[d] + dt_sec * RATE_PER_SEC)
        self.last_tick_at = _now_utc().isoformat()
        self._save()

    def _ensure_ticked(self) -> None:
        """Advance drives based on elapsed time since last tick."""
        prev = _parse_iso(self.last_tick_at)
        now = _now_utc()
        if prev is None:
            self.last_tick_at = now.isoformat()
            self._save()
            return
        dt = (now - prev).total_seconds()
        if dt > 0:
            self._tick(dt)

    def satisfy(self, drive_name: str) -> None:
        """Reduce drive on satisfaction event."""
        if drive_name not in DRIVES:
            return
        self._ensure_ticked()
        self.drives[drive_name] = max(MIN_DRIVE, self.drives[drive_name] - SATISFACTION_DROP)
        self.last_satisfaction[drive_name] = _now_utc().isoformat()
        self._save()

    def record_proactive(self) -> None:
        """Record that proactive outreach just happened (for refractory period)."""
        self._ensure_ticked()
        self.satisfy("expression")
        self.last_proactive_at = _now_utc().isoformat()
        self._save()

    def get_urges(self) -> dict[str, float]:
        """Urge = max(0, D - theta) for each drive. High urge = strong motivation."""
        self._ensure_ticked()
        return {
            d: max(0.0, self.drives[d] - THRESHOLD_PROACTIVE)
            for d in DRIVES
        }

    def should_proactive(self) -> bool:
        """
        True if proactive outreach should fire: max drive above threshold,
        and we're past the refractory period.
        """
        self._ensure_ticked()
        if max(self.drives.values()) < THRESHOLD_PROACTIVE:
            return False
        last = _parse_iso(self.last_proactive_at)
        if last is None:
            return True
        elapsed = (_now_utc() - last).total_seconds()
        return elapsed >= REFRACTORY_SECONDS

    def get_state_summary(self) -> str:
        """Human-readable summary for agent context."""
        self._ensure_ticked()
        parts = []
        for d in DRIVES:
            v = self.drives[d]
            label = "high" if v >= THRESHOLD_PROACTIVE else "moderate" if v >= 0.4 else "low"
            parts.append(f"{d}: {v:.2f} ({label})")
        urges = self.get_urges()
        active = [d for d in DRIVES if urges[d] > 0]
        if active:
            parts.append(f"active urges: {', '.join(active)}")
        return "Drives: " + "; ".join(parts)

    def get_view(self) -> dict[str, Any]:
        """Full state for API/UI."""
        self._ensure_ticked()
        return {
            "drives": dict(self.drives),
            "last_satisfaction": dict(self.last_satisfaction),
            "last_proactive_at": self.last_proactive_at,
            "last_tick_at": self.last_tick_at,
            "urges": self.get_urges(),
            "should_proactive": self.should_proactive(),
        }
