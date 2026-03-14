"""FastAPI app: dashboard, chat, voice, Discord, notifications."""
import asyncio
import base64
import json
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
_chance_reminder_task: asyncio.Task | None = None


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


async def _chance_reminder_loop():
    """Send Chance reminders only at 8–9 AM and 7–8 PM local time, once per window per day."""
    from src.reminders import (
        should_send_chance_reminder,
        record_chance_reminder_sent,
        get_chance_reminder_message,
    )
    from config.settings import DISCORD_OWNER_ID

    CHECK_INTERVAL = 300  # 5 min
    while True:
        await asyncio.sleep(CHECK_INTERVAL)
        if agent is None or not DISCORD_OWNER_ID:
            continue
        try:
            if not should_send_chance_reminder("default"):
                continue
            msg = get_chance_reminder_message()
            record_chance_reminder_sent("default")
            from src import notifications
            try:
                from src.agent import soul
                title = (soul.load_soul().get("agent_name") or "").strip() or "Software Lifeform"
            except Exception:
                title = "Software Lifeform"
            notifications.emit_notification("proactive", title, msg, {"content": msg})
            from src.outreach import queue_outreach
            queue_outreach("discord", msg, target_user_id=DISCORD_OWNER_ID)
        except asyncio.CancelledError:
            break
        except Exception as e:
            try:
                from src.logging_config import log_error
                log_error("chance_reminder", e)
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    global agent, _discord_task, _background_thoughts_task, _status_check_task, _chance_reminder_task
    agent = AssistiveAgent(user_id="default")
    _background_thoughts_task = asyncio.create_task(_background_thoughts_loop())
    _status_check_task = asyncio.create_task(_status_check_loop())
    _chance_reminder_task = asyncio.create_task(_chance_reminder_loop())
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
    if _chance_reminder_task and not _chance_reminder_task.done():
        _chance_reminder_task.cancel()
        try:
            await _chance_reminder_task
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
            result = await agent.chat(ctx + message, narrate_queue=queue)
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
        "image_usage": image_gen.get_usage_data(),
        "subagents": agent_core._get_subagent_manager().status(),
    }


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
