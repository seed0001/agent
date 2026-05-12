"""FastAPI app: dashboard, chat, voice, Discord, notifications."""
import asyncio
import base64
import json
import re
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from config.settings import WEB_HOST, WEB_PORT
from src.agent import core as agent_core
from src.agent.core import AssistiveAgent
from src.tools import tool_queue
from src.voice.stt import transcribe_audio
from src.voice.tts import synthesize

# Global agent instance
agent: AssistiveAgent | None = None
_discord_task: asyncio.Task | None = None
_background_thoughts_task: asyncio.Task | None = None
_status_check_task: asyncio.Task | None = None
_consolidator_task: asyncio.Task | None = None
_consolidator_stop: asyncio.Event | None = None
async def _status_check_loop():
    """Periodic self-diagnostic: sub-agent status. Alert only when issues first appear, not every poll."""
    STATUS_INTERVAL = 600  # 10 min
    _last_had_issues = False
    while True:
        await asyncio.sleep(STATUS_INTERVAL)
        if agent is None:
            continue
        try:
            from src.agent import core as agent_core
            from src.logging_config import log_status_check
            from src import notifications
            mgr = agent_core._get_subagent_manager()
            status = mgr.status()
            issues = "failed" in status.lower() or "error" in status.lower()
            log_status_check(status, issues)
            if issues and not _last_had_issues:
                try:
                    from src.agent import soul
                    s = soul.load_soul()
                    title = (s.get("agent_name") or "Agent").strip() or "Software Lifeform"
                except Exception:
                    title = "Software Lifeform"
                notifications.emit_notification(
                    "status_alert",
                    f"{title} — Status check",
                    f"Sub-agent or tool issue detected: {status[:150]}",
                    {"status": status},
                )
                try:
                    agent.memory.add_short_term(f"[Status alert I sent you]: Sub-agent or tool issue: {status[:200]}")
                except Exception:
                    pass
                _last_had_issues = True
            elif not issues:
                _last_had_issues = False
        except asyncio.CancelledError:
            break
        except Exception as e:
            try:
                from src.logging_config import log_error
                log_error("status_check_loop", e)
            except Exception:
                pass


async def _background_thoughts_loop():
    """Drive-gated background thinking: runs when connection/expression urges exceed threshold."""
    from background_thoughts import run_once

    # Poll interval: check drives every 2 min
    POLL_SEC = 120
    while True:
        await asyncio.sleep(POLL_SEC)
        if agent is None:
            continue
        try:
            if not agent.biology.should_proactive():
                continue
            await run_once()
            agent.biology.record_proactive()
        except asyncio.CancelledError:
            break
        except Exception as e:
            try:
                from src.logging_config import log_error
                log_error("background_thoughts", e)
            except Exception:
                print(f"Background thought error: {e}")


def _build_consolidator_llm():
    """Return an async (system, user) -> str function backed by the active backend.

    The consolidator follows backend_state.json on each call, so background
    memory work cannot keep using an old .env provider after Andrew switches.
    """
    from openai import AsyncOpenAI

    async def _call(system: str, user: str) -> str:
        from src import backend_switching

        active = backend_switching.get_active_backend(user_id="default")
        client = AsyncOpenAI(api_key=active.api_key, base_url=active.base_url)
        resp = await client.chat.completions.create(
            model=active.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        try:
            from src.cost_tracking import record_openai_chat_usage

            record_openai_chat_usage(
                response=resp,
                provider=active.provider,
                model=active.model,
                source_type="background",
                task_label="memory_consolidator",
                fallback_prompt_text=f"{system}\n\n{user}",
                fallback_output_text=(resp.choices[0].message.content or "").strip(),
                user_id="default",
            )
        except Exception:
            pass
        return (resp.choices[0].message.content or "").strip()

    return _call


async def _consolidator_loop(stop_event: asyncio.Event):
    """Stochastic memory consolidator. Runs decay + LLM consolidation passes
    against the live agent's profile."""
    from src.agent.memory_consolidator import ConsolidatorConfig, MemoryConsolidator

    try:
        llm = _build_consolidator_llm()
    except Exception as e:
        try:
            from src.logging_config import log_error
            log_error("consolidator_init", e)
        except Exception:
            print(f"Consolidator LLM init failed; running decay-only: {e}")
        llm = None

    consolidator = MemoryConsolidator(
        user_id="default",
        config=ConsolidatorConfig(),
        llm=llm,
    )
    try:
        await consolidator.run_loop(stop_event)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        try:
            from src.logging_config import log_error
            log_error("consolidator_loop", e)
        except Exception:
            print(f"Consolidator crashed: {e}")


async def _run_completion_review(aid: str, task: str, status: str):
    """Auto-notify Creator when a background task completes."""
    global agent
    if agent is None:
        return
    try:
        from config.settings import DISCORD_OWNER_ID
        from src import background_completions, contacts, notifications
        from src.outreach import queue_outreach

        def _extract_output_path(text: str) -> str:
            if not text:
                return ""
            patterns = [
                r"Output:\s*([^\r\n]+)",
                r"Saved to:\s*([^\r\n]+)",
            ]
            for pat in patterns:
                m = re.search(pat, text, flags=re.IGNORECASE)
                if m:
                    candidate = m.group(1).strip().strip("'\"")
                    p = Path(candidate).expanduser()
                    if p.exists():
                        return str(p.resolve())
                    if not p.is_absolute():
                        rel = (Path.cwd() / p).resolve()
                        if rel.exists():
                            return str(rel)
            return ""

        def _short_summary(task_name: str, completion_status: str, output: str) -> str:
            output = (output or "").strip()
            if not output:
                return f"Background task '{task_name}' ({completion_status}) finished with no captured output."
            lines = [ln.strip() for ln in output.splitlines() if ln.strip()]
            headline = lines[-1] if lines else output[:180]
            if len(headline) > 200:
                headline = headline[:197] + "..."
            return f"Background task '{task_name}' ({completion_status}) finished. {headline}"

        # Ensure owner is always treated as creator for notification paths.
        owner_id = str(DISCORD_OWNER_ID or "").strip()
        if owner_id:
            try:
                contacts.update_contact(
                    "",
                    discord_id=owner_id,
                    name="Creator",
                    tier="creator",
                    preferred_channel="discord",
                )
            except Exception:
                pass

        mgr = agent_core._get_subagent_manager()
        output = mgr.get_output(aid)
        out_path = _extract_output_path(output)
        summary = _short_summary(task, status, output)

        body = summary
        if out_path:
            body += f" Full output: {out_path}"
        elif output:
            preview = output.replace("\r", " ").replace("\n", " ")
            if len(preview) > 260:
                preview = preview[:257] + "..."
            body += f" Output preview: {preview}"

        notifications.emit_notification(
            "task_complete",
            f"Background task complete: {aid}",
            body,
            {"aid": aid, "task": task, "status": status, "output_path": out_path},
        )
        notifications.show_desktop_notification(
            f"Task complete: {aid}",
            body[:200],
        )

        delivered = False
        if owner_id:
            try:
                queue_outreach(
                    "discord",
                    f"[Task complete] {aid}: {body}",
                    target_user_id=owner_id,
                    source="background_completion",
                    trigger_key=f"background_complete:{aid}",
                    event_id=f"background_complete:{aid}",
                    is_direct=True,
                )
                delivered = True
            except Exception:
                delivered = False

        if not delivered:
            notifications.emit_notification(
                "proactive",
                "Background task complete",
                body,
                {"aid": aid, "task": task, "status": status, "output_path": out_path},
            )

        try:
            agent.memory.add_short_term(f"[Background task completion sent] {aid} ({task}): {body}")
        except Exception:
            pass
        background_completions.acknowledge(aid, user_id="default")
    except Exception as e:
        try:
            from src.logging_config import log_error
            log_error("completion_review", e)
        except Exception:
            pass


def _on_subagent_complete(aid: str, task: str, status: str):
    """Called when a subagent finishes. Persists completion and triggers Nova review."""
    from src import background_completions
    background_completions.add(aid, task, status, user_id="default")
    try:
        loop = asyncio.get_event_loop()
        loop.create_task(_run_completion_review(aid, task, status))
    except Exception:
        pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    global agent, _discord_task, _background_thoughts_task, _status_check_task
    global _consolidator_task, _consolidator_stop
    agent = AssistiveAgent(user_id="default")
    from src.tools import subagents
    subagents.set_completion_callback(_on_subagent_complete)
    _background_thoughts_task = asyncio.create_task(_background_thoughts_loop())
    _status_check_task = asyncio.create_task(_status_check_loop())
    _consolidator_stop = asyncio.Event()
    _consolidator_task = asyncio.create_task(_consolidator_loop(_consolidator_stop))
    # Start Discord bot (and outreach consumer) if configured
    try:
        from src.discord_bot import set_agent, start_discord_task

        set_agent(agent)
        _discord_task = start_discord_task()
    except Exception as e:
        print(f"Discord bot not started: {e}")
    yield
    if _background_thoughts_task and not _background_thoughts_task.done():
        _background_thoughts_task.cancel()
        try:
            await _background_thoughts_task
        except asyncio.CancelledError:
            pass
    if _status_check_task and not _status_check_task.done():
        _status_check_task.cancel()
        try:
            await _status_check_task
        except asyncio.CancelledError:
            pass
    if _consolidator_task and not _consolidator_task.done():
        if _consolidator_stop:
            _consolidator_stop.set()
        _consolidator_task.cancel()
        try:
            await _consolidator_task
        except asyncio.CancelledError:
            pass
    if _discord_task and not _discord_task.done():
        _discord_task.cancel()
        try:
            await _discord_task
        except asyncio.CancelledError:
            pass
    agent = None


app = FastAPI(title="Software Lifeform", lifespan=lifespan)

# Paths
_WEB_DIR = Path(__file__).resolve().parent
_STATIC = _WEB_DIR / "static"
_TEMPLATES = _WEB_DIR / "templates"

if _STATIC.exists():
    app.mount("/static", StaticFiles(directory=str(_STATIC)), name="static")


@app.get("/", response_class=HTMLResponse)
async def index():
    html_path = _TEMPLATES / "index.html"
    with open(html_path, encoding="utf-8") as f:
        return f.read()


async def _stream_chat_generator(message: str):
    """Stream narration events then final response as SSE."""
    queue: asyncio.Queue = asyncio.Queue()

    async def run_agent():
        try:
            agent.memory.set_working("current_speaker_discord_id", None)
            from src.agent.soul import get_context_for_speaker
            ctx = get_context_for_speaker(is_web=True)
            result = await agent.chat(
                ctx + message,
                narrate_queue=queue,
                speaker_discord_id=None,
            )
            await queue.put({"type": "response", "text": result})
        except Exception as e:
            await queue.put({"type": "error", "text": str(e)})
        finally:
            await queue.put(None)

    asyncio.create_task(run_agent())

    while True:
        item = await queue.get()
        if item is None:
            break
        yield f"data: {json.dumps(item)}\n\n"


@app.post("/api/chat")
async def api_chat(message: str = Form(...)):
    if not agent:
        return JSONResponse({"error": "Agent not ready"}, status_code=503)
    return StreamingResponse(
        _stream_chat_generator(message),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/transcribe")
async def api_transcribe(audio: UploadFile = File(...)):
    """Transcribe uploaded audio (from Record -> Stop -> Send flow)."""
    import asyncio
    try:
        data = await audio.read()
        text = await asyncio.to_thread(transcribe_audio, data)
        return {"text": text}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/tool-queue")
async def api_tool_queue():
    return tool_queue.get_queue()


@app.get("/api/memory-view")
async def api_memory_view():
    """Return profile, episodic memories, working memory, and biology state."""
    if not agent:
        return JSONResponse({"error": "Agent not ready"}, status_code=503)
    from src.tools import image_gen
    m = agent.memory
    return {
        "profile": m.get_profile_view(),
        "episodic": m.get_episodic_view(),
        "working": m.get_working_view(),
        "thoughts": m.get_thoughts_view(),
        "biology": agent.biology.get_view(),
        "existential": agent.existential.get_view(),
        "values_vault": __import__("src.values_vault", fromlist=["get_view"]).get_view(),
        "presence": __import__("src.presence", fromlist=["get_view"]).get_view(),
        "image_usage": image_gen.get_usage_data(),
        "subagents": agent_core._get_subagent_manager().status(),
    }


@app.post("/api/memory/remember")
async def api_memory_remember(
    category: str = Form(""),
    fact: str = Form(""),
    key: str = Form(""),
):
    """Manually store a profile fact (always protected)."""
    if not agent:
        return JSONResponse({"error": "Agent not ready"}, status_code=503)
    fact = (fact or "").strip()
    if not fact:
        return JSONResponse({"error": "fact is required"}, status_code=400)
    try:
        if key:
            agent.memory.profile.set(
                key.strip(), fact,
                category=(category or "general").strip(),
                source="user", protected=True,
            )
            msg = f"remembered (key='{key.strip()}', protected)."
        else:
            msg = agent.memory.add_profile_fact(category or "other", fact)
        return {"ok": True, "message": msg}
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.post("/api/memory/forget")
async def api_memory_forget(key: str = Form(...)):
    """Soft-delete a profile fact by exact key."""
    if not agent:
        return JSONResponse({"error": "Agent not ready"}, status_code=503)
    ok = agent.memory.profile.delete(key.strip())
    return {"ok": ok}


@app.post("/api/memory/protect")
async def api_memory_protect(key: str = Form(...), protected: int = Form(1)):
    """Toggle the protected flag on a profile fact by exact key."""
    if not agent:
        return JSONResponse({"error": "Agent not ready"}, status_code=503)
    ok = agent.memory.profile.protect(key.strip(), protected=bool(int(protected)))
    return {"ok": ok}


@app.post("/api/memory/decay-config")
async def api_memory_decay_config(
    half_life_days: float = Form(...),
    min_confidence: float = Form(...),
):
    """Persist decay tuning for the consolidator to pick up next tick."""
    if not agent:
        return JSONResponse({"error": "Agent not ready"}, status_code=503)
    if half_life_days <= 0 or not (0 <= min_confidence <= 1):
        return JSONResponse(
            {"error": "half_life_days > 0 and 0 <= min_confidence <= 1"}, status_code=400
        )
    agent.memory.state.set("memory.config.half_life_days", half_life_days)
    agent.memory.state.set("memory.config.min_confidence", min_confidence)
    return {"ok": True}


@app.post("/api/memory/forget-all")
async def api_memory_forget_all(confirm: str = Form("")):
    """Wipe ALL profile facts. Requires confirm=yes-i-am-sure."""
    if not agent:
        return JSONResponse({"error": "Agent not ready"}, status_code=503)
    if confirm != "yes-i-am-sure":
        return JSONResponse({"error": "missing confirm token"}, status_code=400)
    facts = agent.memory.profile.get_all()
    n = 0
    for f in facts:
        if agent.memory.profile.delete(f.key):
            n += 1
    return {"ok": True, "deleted": n}


@app.post("/api/tool-approve")
async def api_tool_approve(tool_id: str = Form(...)):
    return {"result": tool_queue.approve_tool(tool_id)}


@app.post("/api/tool-reject")
async def api_tool_reject(tool_id: str = Form(...)):
    return {"result": tool_queue.reject_tool(tool_id)}


@app.post("/api/tool-reload")
async def api_tool_reload():
    if agent:
        agent._reload_dynamic()
    return {"result": "Tools reloaded"}


@app.post("/api/subagents-stop-all")
async def api_subagents_stop_all():
    """Stop all running sub-agents."""
    mgr = agent_core._get_subagent_manager()
    n = mgr.stop_all()
    return {"result": f"Stopped {n} sub-agent(s)"}


async def _notification_sse_generator():
    """SSE stream for notifications (Discord messages, proactive outreach)."""
    from src.notifications import get_notification_queue, NotificationEvent

    q = get_notification_queue()
    while True:
        try:
            ev = await asyncio.wait_for(q.get(), timeout=30.0)
            if isinstance(ev, NotificationEvent):
                payload = {
                    "type": ev.type,
                    "title": ev.title,
                    "body": ev.body,
                    "meta": ev.meta or {},
                    "ts": ev.created_at,
                }
                yield f"data: {json.dumps(payload)}\n\n"
        except asyncio.TimeoutError:
            yield "data: {\"type\":\"ping\"}\n\n"


@app.get("/api/notifications/stream")
async def api_notifications_stream():
    """SSE stream: Discord messages, proactive messages, etc."""
    return StreamingResponse(
        _notification_sse_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@app.get("/api/contacts")
async def api_contacts():
    """Get all contacts (friends, Discord users)."""
    from src import contacts

    return {"contacts": contacts.get_all_contacts()}


@app.get("/api/access-policy")
async def api_get_access_policy():
    """Get access policy (tools per tier). Edit data/profiles/default/access_policy.json to change."""
    from config.access_policy import _load_policy, DEFAULT_POLICY, CONTACT_TIERS

    policy = _load_policy()
    return {"policy": policy, "tiers": list(CONTACT_TIERS)}


@app.get("/api/voices")
async def api_voices():
    """List available Edge TTS voices for voice selector."""
    try:
        import edge_tts
        voices = await edge_tts.list_voices()
        return {
            "voices": [
                {"id": v.get("ShortName", ""), "name": v.get("FriendlyName", ""), "gender": v.get("Gender", ""), "locale": v.get("Locale", "")}
                for v in (voices or []) if v.get("ShortName")
            ]
        }
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/settings")
async def api_get_settings():
    """Get user settings (tts_voice, etc.)."""
    from src.user_settings import get_settings
    return get_settings("default")


@app.post("/api/settings")
async def api_set_settings(tts_voice: str = Form(None)):
    """Update user settings. tts_voice: Edge TTS ShortName (e.g. en-GB-RyanNeural)."""
    from src.user_settings import set_setting
    if tts_voice:
        set_setting("tts_voice", tts_voice)
    return {"ok": True}


@app.get("/api/cost/snapshot")
async def api_cost_snapshot(
    period: str = "today",
    include_free: int = 1,
    group_by: str = "provider",
):
    """Live API/token cost snapshot."""
    from src.cost_tracking import get_cost_snapshot

    return get_cost_snapshot(
        period=period,
        include_free=bool(int(include_free)),
        group_by=group_by,
        user_id="default",
    )


@app.get("/api/cost/events")
async def api_cost_events(limit: int = 100):
    """Recent usage/cost events."""
    from src.cost_tracking import get_recent_events

    return {"events": get_recent_events(limit=limit, user_id="default")}


@app.post("/api/cost/pricing")
async def api_set_cost_pricing(
    provider: str = Form(...),
    model: str = Form(...),
    input_per_million: float = Form(None),
    output_per_million: float = Form(None),
    input_per_token: float = Form(None),
    output_per_token: float = Form(None),
    cached_input_per_million: float = Form(None),
    reasoning_per_million: float = Form(None),
    local: int = Form(0),
    currency: str = Form("USD"),
    notes: str = Form(""),
):
    """Create/update model pricing."""
    from src.cost_tracking import set_model_pricing

    msg = set_model_pricing(
        provider=provider,
        model=model,
        input_per_million=input_per_million,
        output_per_million=output_per_million,
        input_per_token=input_per_token,
        output_per_token=output_per_token,
        cached_input_per_million=cached_input_per_million,
        reasoning_per_million=reasoning_per_million,
        local=bool(int(local)),
        currency=currency,
        notes=notes,
        user_id="default",
    )
    return {"ok": not msg.lower().startswith("error"), "message": msg}


@app.post("/api/cost/budget")
async def api_set_cost_budget(
    daily_limit: float = Form(None),
    weekly_limit: float = Form(None),
    monthly_limit: float = Form(None),
    warning_threshold_percent: float = Form(None),
    hard_stop_threshold_percent: float = Form(None),
    require_confirmation_over_amount: float = Form(None),
    currency: str = Form(None),
):
    """Update cost budget/threshold settings."""
    from src.cost_tracking import set_budget_limits, get_budget_settings

    msg = set_budget_limits(
        daily_limit=daily_limit,
        weekly_limit=weekly_limit,
        monthly_limit=monthly_limit,
        warning_threshold_percent=warning_threshold_percent,
        hard_stop_threshold_percent=hard_stop_threshold_percent,
        require_confirmation_over_amount=require_confirmation_over_amount,
        currency=currency,
        user_id="default",
    )
    return {"ok": True, "message": msg, "budget": get_budget_settings(user_id="default")}


@app.get("/api/backend/status")
async def api_backend_status():
    """Current backend/fallback status."""
    from src.backend_switching import get_backend_status

    try:
        return get_backend_status(user_id="default")
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/backend/switch")
async def api_backend_switch(
    target: str = Form(...),
    reason: str = Form("creator_requested"),
    dry_run: int = Form(0),
    force: int = Form(0),
):
    """Creator-initiated backend switch with health-check fallback."""
    from src.backend_switching import switch_backend_provider

    try:
        result = await switch_backend_provider(
            target=target,
            reason=reason,
            dry_run=bool(int(dry_run)),
            force=bool(int(force)),
            requested_by="creator",
            user_id="default",
        )
        return result
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/speak")
async def api_speak(text: str = Form(...), voice: str = Form(None)):
    """Convert text to speech, return base64 mp3."""
    try:
        from src.user_settings import get_tts_voice
        voice_id = voice or get_tts_voice("default")
        audio_bytes = await synthesize(text, voice=voice_id)
        b64 = base64.b64encode(audio_bytes).decode("utf-8")
        return {"audio": f"data:audio/mp3;base64,{b64}"}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


def run():
    import socket
    import uvicorn
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
        print(f"\n  Mobile: http://{local_ip}:{WEB_PORT}\n")
    except Exception:
        pass
    uvicorn.run(app, host=WEB_HOST, port=WEB_PORT)


if __name__ == "__main__":
    run()
