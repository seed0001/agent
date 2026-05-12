"""Token and API cost tracking.

First-pass scope:
- SQLite persistence for pricing, budgets, usage events
- Cost calculator (per-token or per-million pricing)
- Usage recording helpers for OpenAI-compatible and local/Ollama calls
- Snapshot + estimate helpers for runtime decisions
"""
from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from config.settings import USER_PROFILES_DIR, get_chat_model, get_llm_provider

DB_FILENAME = "cost_tracking.db"
_LOCK = threading.Lock()
_CONNECTIONS: dict[str, sqlite3.Connection] = {}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None = None) -> str:
    return (dt or _utcnow()).isoformat()


def _period_start(period: str) -> datetime | None:
    now = _utcnow()
    p = (period or "today").strip().lower()
    if p == "all":
        return None
    if p == "week":
        d = now - timedelta(days=now.weekday())
        return d.replace(hour=0, minute=0, second=0, microsecond=0)
    if p == "month":
        return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def _estimate_tokens(text: str) -> int:
    if not text:
        return 0
    # Rough fallback for unknown tokenizers.
    return max(1, int(len(text) / 4))


def db_path(user_id: str = "default") -> Path:
    return USER_PROFILES_DIR / user_id / DB_FILENAME


def _open_conn(user_id: str = "default") -> sqlite3.Connection:
    p = db_path(user_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        str(p),
        timeout=10.0,
        isolation_level=None,
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    _ensure_schema(conn)
    return conn


def _conn(user_id: str = "default") -> sqlite3.Connection:
    with _LOCK:
        c = _CONNECTIONS.get(user_id)
        if c is None:
            c = _open_conn(user_id)
            _CONNECTIONS[user_id] = c
        return c


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS model_pricing (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            input_per_token REAL,
            output_per_token REAL,
            input_per_million REAL,
            output_per_million REAL,
            cached_input_per_million REAL,
            reasoning_per_million REAL,
            currency TEXT NOT NULL DEFAULT 'USD',
            local INTEGER NOT NULL DEFAULT 0,
            active INTEGER NOT NULL DEFAULT 1,
            effective_date TEXT,
            notes TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(provider, model)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS usage_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL DEFAULT (datetime('now')),
            session_id TEXT,
            conversation_id TEXT,
            message_id TEXT,
            user_id TEXT,
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            source_type TEXT NOT NULL DEFAULT 'conversation',
            project_id TEXT,
            tool_name TEXT,
            task_label TEXT,
            input_tokens INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0,
            cached_input_tokens INTEGER NOT NULL DEFAULT 0,
            reasoning_tokens INTEGER NOT NULL DEFAULT 0,
            total_tokens INTEGER NOT NULL DEFAULT 0,
            actual_usage INTEGER NOT NULL DEFAULT 0,
            estimated_usage INTEGER NOT NULL DEFAULT 0,
            local INTEGER NOT NULL DEFAULT 0,
            free INTEGER NOT NULL DEFAULT 0,
            cost_unknown INTEGER NOT NULL DEFAULT 0,
            input_cost REAL,
            output_cost REAL,
            other_cost REAL,
            total_cost REAL,
            currency TEXT NOT NULL DEFAULT 'USD',
            metadata_json TEXT NOT NULL DEFAULT '{}'
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS budget_settings (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            daily_limit REAL,
            weekly_limit REAL,
            monthly_limit REAL,
            warning_threshold_percent REAL NOT NULL DEFAULT 80.0,
            hard_stop_threshold_percent REAL NOT NULL DEFAULT 100.0,
            require_confirmation_over_amount REAL NOT NULL DEFAULT 1.0,
            currency TEXT NOT NULL DEFAULT 'USD',
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute(
        """
        INSERT INTO budget_settings (
            id, warning_threshold_percent, hard_stop_threshold_percent, require_confirmation_over_amount, currency
        )
        VALUES (1, 80.0, 100.0, 1.0, 'USD')
        ON CONFLICT(id) DO NOTHING
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_usage_timestamp ON usage_events(timestamp DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_usage_provider_model ON usage_events(provider, model, timestamp DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_usage_source ON usage_events(source_type, timestamp DESC)"
    )


@dataclass
class Pricing:
    provider: str
    model: str
    input_per_token: float | None
    output_per_token: float | None
    input_per_million: float | None
    output_per_million: float | None
    cached_input_per_million: float | None
    reasoning_per_million: float | None
    currency: str
    local: bool
    active: bool


def set_model_pricing(
    *,
    provider: str,
    model: str,
    input_per_million: float | None = None,
    output_per_million: float | None = None,
    input_per_token: float | None = None,
    output_per_token: float | None = None,
    cached_input_per_million: float | None = None,
    reasoning_per_million: float | None = None,
    local: bool = False,
    currency: str = "USD",
    notes: str = "",
    effective_date: str = "",
    active: bool = True,
    user_id: str = "default",
) -> str:
    provider = (provider or "").strip().lower()
    model = (model or "").strip()
    if not provider or not model:
        return "Error: provider and model are required."
    c = _conn(user_id)
    c.execute(
        """
        INSERT INTO model_pricing (
            provider, model, input_per_token, output_per_token,
            input_per_million, output_per_million, cached_input_per_million, reasoning_per_million,
            currency, local, active, effective_date, notes, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(provider, model) DO UPDATE SET
            input_per_token=excluded.input_per_token,
            output_per_token=excluded.output_per_token,
            input_per_million=excluded.input_per_million,
            output_per_million=excluded.output_per_million,
            cached_input_per_million=excluded.cached_input_per_million,
            reasoning_per_million=excluded.reasoning_per_million,
            currency=excluded.currency,
            local=excluded.local,
            active=excluded.active,
            effective_date=excluded.effective_date,
            notes=excluded.notes,
            updated_at=excluded.updated_at
        """,
        (
            provider,
            model,
            input_per_token,
            output_per_token,
            input_per_million,
            output_per_million,
            cached_input_per_million,
            reasoning_per_million,
            (currency or "USD").upper(),
            1 if local else 0,
            1 if active else 0,
            effective_date or None,
            notes or "",
            _iso(),
            _iso(),
        ),
    )
    return f"Pricing saved: {provider}/{model}"


def _get_pricing(provider: str, model: str, user_id: str = "default") -> Pricing | None:
    p = (provider or "").strip().lower()
    m = (model or "").strip()
    if not p or not m:
        return None
    c = _conn(user_id)
    row = c.execute(
        """
        SELECT provider, model, input_per_token, output_per_token, input_per_million, output_per_million,
               cached_input_per_million, reasoning_per_million, currency, local, active
        FROM model_pricing
        WHERE provider = ? AND model = ? AND active = 1
        """,
        (p, m),
    ).fetchone()
    if not row:
        return None
    return Pricing(
        provider=row["provider"],
        model=row["model"],
        input_per_token=row["input_per_token"],
        output_per_token=row["output_per_token"],
        input_per_million=row["input_per_million"],
        output_per_million=row["output_per_million"],
        cached_input_per_million=row["cached_input_per_million"],
        reasoning_per_million=row["reasoning_per_million"],
        currency=row["currency"] or "USD",
        local=bool(row["local"]),
        active=bool(row["active"]),
    )


def set_budget_limits(
    *,
    daily_limit: float | None = None,
    weekly_limit: float | None = None,
    monthly_limit: float | None = None,
    warning_threshold_percent: float | None = None,
    hard_stop_threshold_percent: float | None = None,
    require_confirmation_over_amount: float | None = None,
    currency: str | None = None,
    user_id: str = "default",
) -> str:
    c = _conn(user_id)
    current = c.execute("SELECT * FROM budget_settings WHERE id = 1").fetchone()
    if not current:
        _ensure_schema(c)
        current = c.execute("SELECT * FROM budget_settings WHERE id = 1").fetchone()
    c.execute(
        """
        UPDATE budget_settings
        SET daily_limit = ?,
            weekly_limit = ?,
            monthly_limit = ?,
            warning_threshold_percent = ?,
            hard_stop_threshold_percent = ?,
            require_confirmation_over_amount = ?,
            currency = ?,
            updated_at = ?
        WHERE id = 1
        """,
        (
            daily_limit if daily_limit is not None else current["daily_limit"],
            weekly_limit if weekly_limit is not None else current["weekly_limit"],
            monthly_limit if monthly_limit is not None else current["monthly_limit"],
            warning_threshold_percent
            if warning_threshold_percent is not None
            else current["warning_threshold_percent"],
            hard_stop_threshold_percent
            if hard_stop_threshold_percent is not None
            else current["hard_stop_threshold_percent"],
            require_confirmation_over_amount
            if require_confirmation_over_amount is not None
            else current["require_confirmation_over_amount"],
            (currency or current["currency"] or "USD").upper(),
            _iso(),
        ),
    )
    return "Budget settings updated."


def get_budget_settings(user_id: str = "default") -> dict[str, Any]:
    row = _conn(user_id).execute(
        "SELECT * FROM budget_settings WHERE id = 1"
    ).fetchone()
    if not row:
        return {}
    return dict(row)


def _per_token(price_per_token: float | None, price_per_million: float | None) -> float | None:
    if price_per_token is not None:
        return float(price_per_token)
    if price_per_million is None:
        return None
    return float(price_per_million) / 1_000_000.0


def calculate_cost(
    *,
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cached_input_tokens: int = 0,
    reasoning_tokens: int = 0,
    user_id: str = "default",
) -> dict[str, Any]:
    pricing = _get_pricing(provider, model, user_id=user_id)
    local = (provider or "").lower() in {"ollama", "local"}
    if local and pricing is None:
        return {
            "input_cost": 0.0,
            "output_cost": 0.0,
            "cached_input_cost": 0.0,
            "reasoning_cost": 0.0,
            "total_cost": 0.0,
            "currency": "USD",
            "estimated": False,
            "cost_unknown": False,
            "local": True,
            "free": True,
        }

    if pricing is None:
        return {
            "input_cost": None,
            "output_cost": None,
            "cached_input_cost": None,
            "reasoning_cost": None,
            "total_cost": None,
            "currency": "USD",
            "estimated": True,
            "cost_unknown": True,
            "local": local,
            "free": local,
        }

    in_pt = _per_token(pricing.input_per_token, pricing.input_per_million)
    out_pt = _per_token(pricing.output_per_token, pricing.output_per_million)
    cached_pt = _per_token(None, pricing.cached_input_per_million)
    reasoning_pt = _per_token(None, pricing.reasoning_per_million)

    input_cost = (in_pt or 0.0) * max(0, int(input_tokens))
    output_cost = (out_pt or 0.0) * max(0, int(output_tokens))
    cached_cost = (cached_pt or 0.0) * max(0, int(cached_input_tokens))
    reasoning_cost = (reasoning_pt or 0.0) * max(0, int(reasoning_tokens))
    total = input_cost + output_cost + cached_cost + reasoning_cost
    return {
        "input_cost": round(input_cost, 8),
        "output_cost": round(output_cost, 8),
        "cached_input_cost": round(cached_cost, 8),
        "reasoning_cost": round(reasoning_cost, 8),
        "total_cost": round(total, 8),
        "currency": pricing.currency or "USD",
        "estimated": False,
        "cost_unknown": False,
        "local": bool(pricing.local),
        "free": bool(pricing.local) and total <= 0.0,
    }


def extract_openai_usage(response: Any) -> dict[str, Any]:
    usage = getattr(response, "usage", None)
    if usage is None and isinstance(response, dict):
        usage = response.get("usage")
    if usage is None:
        return {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "cached_input_tokens": 0,
            "reasoning_tokens": 0,
            "actual_usage": False,
            "estimated_usage": True,
        }

    def _g(obj: Any, key: str, default: int = 0) -> int:
        if isinstance(obj, dict):
            return int(obj.get(key, default) or default)
        return int(getattr(obj, key, default) or default)

    input_tokens = _g(usage, "prompt_tokens")
    output_tokens = _g(usage, "completion_tokens")
    total_tokens = _g(usage, "total_tokens")
    prompt_details = usage.get("prompt_tokens_details", {}) if isinstance(usage, dict) else getattr(usage, "prompt_tokens_details", None) or {}
    completion_details = usage.get("completion_tokens_details", {}) if isinstance(usage, dict) else getattr(usage, "completion_tokens_details", None) or {}
    cached_tokens = _g(prompt_details, "cached_tokens")
    reasoning_tokens = _g(completion_details, "reasoning_tokens")
    return {
        "input_tokens": max(0, input_tokens),
        "output_tokens": max(0, output_tokens),
        "total_tokens": max(0, total_tokens or (input_tokens + output_tokens)),
        "cached_input_tokens": max(0, cached_tokens),
        "reasoning_tokens": max(0, reasoning_tokens),
        "actual_usage": True,
        "estimated_usage": False,
    }


def record_usage_event(
    *,
    provider: str,
    model: str,
    source_type: str,
    input_tokens: int,
    output_tokens: int,
    cached_input_tokens: int = 0,
    reasoning_tokens: int = 0,
    total_tokens: int | None = None,
    actual_usage: bool = False,
    estimated_usage: bool = False,
    local: bool = False,
    free: bool = False,
    session_id: str = "",
    conversation_id: str = "",
    message_id: str = "",
    user_id_for_event: str = "",
    project_id: str = "",
    tool_name: str = "",
    task_label: str = "",
    metadata: dict[str, Any] | None = None,
    user_id: str = "default",
) -> dict[str, Any]:
    total = int(total_tokens if total_tokens is not None else (input_tokens + output_tokens))
    costs = calculate_cost(
        provider=provider,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_input_tokens=cached_input_tokens,
        reasoning_tokens=reasoning_tokens,
        user_id=user_id,
    )
    if local:
        costs["local"] = True
    if free:
        costs["free"] = True

    c = _conn(user_id)
    c.execute(
        """
        INSERT INTO usage_events (
            timestamp, session_id, conversation_id, message_id, user_id,
            provider, model, source_type, project_id, tool_name, task_label,
            input_tokens, output_tokens, cached_input_tokens, reasoning_tokens, total_tokens,
            actual_usage, estimated_usage, local, free, cost_unknown,
            input_cost, output_cost, other_cost, total_cost, currency, metadata_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            _iso(),
            session_id or None,
            conversation_id or None,
            message_id or None,
            user_id_for_event or None,
            (provider or "").lower(),
            model or "",
            source_type or "other",
            project_id or None,
            tool_name or None,
            task_label or None,
            int(input_tokens),
            int(output_tokens),
            int(cached_input_tokens),
            int(reasoning_tokens),
            int(total),
            1 if actual_usage else 0,
            1 if estimated_usage else 0,
            1 if costs.get("local") else 0,
            1 if costs.get("free") else 0,
            1 if costs.get("cost_unknown") else 0,
            costs.get("input_cost"),
            costs.get("output_cost"),
            (costs.get("cached_input_cost") or 0.0) + (costs.get("reasoning_cost") or 0.0),
            costs.get("total_cost"),
            costs.get("currency") or "USD",
            json.dumps(metadata or {}, ensure_ascii=False),
        ),
    )
    return costs


def record_openai_chat_usage(
    *,
    response: Any,
    provider: str,
    model: str,
    source_type: str = "conversation",
    session_id: str = "",
    conversation_id: str = "",
    message_id: str = "",
    user_id_for_event: str = "",
    project_id: str = "",
    tool_name: str = "",
    task_label: str = "",
    fallback_prompt_text: str = "",
    fallback_output_text: str = "",
    metadata: dict[str, Any] | None = None,
    user_id: str = "default",
) -> dict[str, Any]:
    usage = extract_openai_usage(response)
    if usage["total_tokens"] <= 0:
        in_est = _estimate_tokens(fallback_prompt_text)
        out_est = _estimate_tokens(fallback_output_text)
        usage.update(
            {
                "input_tokens": in_est,
                "output_tokens": out_est,
                "total_tokens": in_est + out_est,
                "cached_input_tokens": 0,
                "reasoning_tokens": 0,
                "actual_usage": False,
                "estimated_usage": True,
            }
        )
    return record_usage_event(
        provider=provider,
        model=model,
        source_type=source_type,
        input_tokens=usage["input_tokens"],
        output_tokens=usage["output_tokens"],
        cached_input_tokens=usage.get("cached_input_tokens", 0),
        reasoning_tokens=usage.get("reasoning_tokens", 0),
        total_tokens=usage["total_tokens"],
        actual_usage=usage.get("actual_usage", False),
        estimated_usage=usage.get("estimated_usage", False),
        session_id=session_id,
        conversation_id=conversation_id,
        message_id=message_id,
        user_id_for_event=user_id_for_event,
        project_id=project_id,
        tool_name=tool_name,
        task_label=task_label,
        metadata=metadata,
        user_id=user_id,
    )


def record_local_usage(
    *,
    provider: str,
    model: str,
    source_type: str,
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int | None = None,
    actual_usage: bool = False,
    estimated_usage: bool = True,
    task_label: str = "",
    metadata: dict[str, Any] | None = None,
    user_id: str = "default",
) -> dict[str, Any]:
    return record_usage_event(
        provider=provider,
        model=model,
        source_type=source_type,
        input_tokens=prompt_tokens,
        output_tokens=completion_tokens,
        total_tokens=total_tokens,
        actual_usage=actual_usage,
        estimated_usage=estimated_usage,
        local=True,
        free=True,
        task_label=task_label,
        metadata=metadata,
        user_id=user_id,
    )


def estimate_cost(
    *,
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cached_input_tokens: int = 0,
    reasoning_tokens: int = 0,
    user_id: str = "default",
) -> dict[str, Any]:
    return calculate_cost(
        provider=provider,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_input_tokens=cached_input_tokens,
        reasoning_tokens=reasoning_tokens,
        user_id=user_id,
    )


def _current_provider_model(user_id: str = "default") -> tuple[str, str]:
    try:
        from src import backend_switching

        active = backend_switching.get_active_backend(user_id=user_id)
        return active.provider, active.model
    except Exception:
        return get_llm_provider(), get_chat_model()


def estimate_task_cost(
    *,
    task_type: str,
    expected_input_tokens: int,
    expected_output_tokens: int,
    provider: str | None = None,
    model: str | None = None,
    steps: int | None = None,
    user_id: str = "default",
) -> dict[str, Any]:
    active_provider, active_model = _current_provider_model(user_id=user_id)
    p = (provider or active_provider).lower()
    m = model or active_model
    n = max(1, int(steps or 1))
    est = estimate_cost(
        provider=p,
        model=m,
        input_tokens=max(0, int(expected_input_tokens)) * n,
        output_tokens=max(0, int(expected_output_tokens)) * n,
        user_id=user_id,
    )
    total = est.get("total_cost")
    budget = get_budget_settings(user_id=user_id)
    confirm_over = float(budget.get("require_confirmation_over_amount") or 1.0)
    should_confirm = bool(total is not None and total >= confirm_over)
    return {
        "task_type": task_type,
        "provider": p,
        "model": m,
        "steps": n,
        "input_tokens": max(0, int(expected_input_tokens)) * n,
        "output_tokens": max(0, int(expected_output_tokens)) * n,
        "estimated_cost": total,
        "currency": est.get("currency", "USD"),
        "cost_unknown": bool(est.get("cost_unknown")),
        "should_confirm": should_confirm,
    }


def get_recent_events(limit: int = 100, user_id: str = "default") -> list[dict[str, Any]]:
    rows = _conn(user_id).execute(
        """
        SELECT *
        FROM usage_events
        ORDER BY timestamp DESC, id DESC
        LIMIT ?
        """,
        (max(1, int(limit)),),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        try:
            d["metadata_json"] = json.loads(d.get("metadata_json") or "{}")
        except Exception:
            d["metadata_json"] = {}
        out.append(d)
    return out


def get_cost_snapshot(
    *,
    period: str = "today",
    include_free: bool = True,
    group_by: str = "provider",
    user_id: str = "default",
) -> dict[str, Any]:
    start = _period_start(period)
    c = _conn(user_id)
    where = ""
    params: list[Any] = []
    if start is not None:
        where = "WHERE timestamp >= ?"
        params.append(_iso(start))

    sums = c.execute(
        f"""
        SELECT
            COALESCE(SUM(CASE WHEN total_cost IS NOT NULL AND free = 0 THEN total_cost ELSE 0 END), 0) AS paid_cost,
            COALESCE(SUM(CASE WHEN estimated_usage = 1 AND total_cost IS NOT NULL THEN total_cost ELSE 0 END), 0) AS estimated_cost,
            COALESCE(SUM(input_tokens), 0) AS input_tokens,
            COALESCE(SUM(output_tokens), 0) AS output_tokens,
            COALESCE(SUM(total_tokens), 0) AS total_tokens,
            COALESCE(SUM(CASE WHEN local = 1 OR free = 1 THEN total_tokens ELSE 0 END), 0) AS free_local_tokens
        FROM usage_events
        {where}
        """,
        params,
    ).fetchone()

    by_model_rows = c.execute(
        f"""
        SELECT provider, model,
               COALESCE(SUM(total_cost), 0) AS total_cost,
               COALESCE(SUM(total_tokens), 0) AS total_tokens,
               COUNT(*) AS calls
        FROM usage_events
        {where}
        GROUP BY provider, model
        ORDER BY total_cost DESC, total_tokens DESC
        LIMIT 20
        """,
        params,
    ).fetchall()

    by_source_rows = c.execute(
        f"""
        SELECT source_type,
               COALESCE(SUM(total_cost), 0) AS total_cost,
               COALESCE(SUM(total_tokens), 0) AS total_tokens,
               COUNT(*) AS calls
        FROM usage_events
        {where}
        GROUP BY source_type
        ORDER BY total_cost DESC, total_tokens DESC
        """,
        params,
    ).fetchall()

    by_task_rows = c.execute(
        f"""
        SELECT task_label,
               COALESCE(SUM(total_cost), 0) AS total_cost,
               COALESCE(SUM(total_tokens), 0) AS total_tokens,
               COUNT(*) AS calls
        FROM usage_events
        {where}
          AND task_label IS NOT NULL
          AND TRIM(task_label) != ''
        GROUP BY task_label
        ORDER BY total_cost DESC, total_tokens DESC
        LIMIT 20
        """,
        params,
    ).fetchall()

    budget = get_budget_settings(user_id=user_id)
    paid = float(sums["paid_cost"] or 0.0)
    daily_limit = budget.get("daily_limit")
    warning_pct = float(budget.get("warning_threshold_percent") or 80.0)
    warnings: list[str] = []
    remaining_daily = None
    if daily_limit is not None:
        daily_limit = float(daily_limit)
        remaining_daily = daily_limit - paid
        if daily_limit > 0:
            pct = (paid / daily_limit) * 100.0
            if pct >= warning_pct:
                warnings.append(
                    f"Daily budget warning: {pct:.1f}% used ({paid:.4f}/{daily_limit:.4f} {budget.get('currency','USD')})."
                )

    active_provider, active_model = _current_provider_model(user_id=user_id)
    result = {
        "period": (period or "today"),
        "paid_cost": round(paid, 6),
        "estimated_cost": round(float(sums["estimated_cost"] or 0.0), 6),
        "input_tokens": int(sums["input_tokens"] or 0),
        "output_tokens": int(sums["output_tokens"] or 0),
        "total_tokens": int(sums["total_tokens"] or 0),
        "free_local_tokens": int(sums["free_local_tokens"] or 0),
        "currency": budget.get("currency", "USD"),
        "current_provider": active_provider,
        "current_model": active_model,
        "by_model": [dict(r) for r in by_model_rows],
        "by_source_type": [dict(r) for r in by_source_rows],
        "by_task": [dict(r) for r in by_task_rows],
        "budget": {
            "daily_limit": budget.get("daily_limit"),
            "weekly_limit": budget.get("weekly_limit"),
            "monthly_limit": budget.get("monthly_limit"),
            "warning_threshold_percent": budget.get("warning_threshold_percent"),
            "hard_stop_threshold_percent": budget.get("hard_stop_threshold_percent"),
            "require_confirmation_over_amount": budget.get("require_confirmation_over_amount"),
            "remaining_daily": remaining_daily,
        },
        "warnings": warnings,
    }
    if not include_free:
        result["free_local_tokens"] = 0
    try:
        from src.backend_switching import get_active_backend

        active = get_active_backend(user_id=user_id)
        result["current_provider"] = active.provider
        result["current_model"] = active.model
    except Exception:
        pass
    return result


def format_snapshot_for_context(user_id: str = "default") -> str:
    s_today = get_cost_snapshot(period="today", include_free=True, user_id=user_id)
    s_month = get_cost_snapshot(period="month", include_free=True, user_id=user_id)
    budget = s_today.get("budget", {})
    daily_limit = budget.get("daily_limit")
    currency = s_today.get("currency", "USD")
    if daily_limit is not None:
        daily_part = f"{s_today['paid_cost']:.4f} / {float(daily_limit):.4f} {currency}"
    else:
        daily_part = f"{s_today['paid_cost']:.4f} {currency}"
    return (
        "## API Cost Snapshot\n"
        f"Today paid: {daily_part}.\n"
        f"This month paid: {s_month['paid_cost']:.4f} {currency}.\n"
        f"Today free/local: {s_today['free_local_tokens']} tokens.\n"
        f"Current backend: {s_today.get('current_provider')}/{s_today.get('current_model')}.\n"
        "Policy: mention >$0.25 when relevant; ask before estimates over configured confirmation threshold."
    )

