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


async def _status_check_loop():
    """Periodic self-diagnostic: sub-agent status, alert on issues before they escalate."""
    STATUS_INTERVAL = 600  # 10 min
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
            if issues:
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    global agent, _discord_task, _background_thoughts_task, _status_check_task
    agent = AssistiveAgent(user_id="default")
    _background_thoughts_task = asyncio.create_task(_background_thoughts_loop())
    _status_check_task = asyncio.create_task(_status_check_loop())
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
    m = agent.memory
    return {
        "profile": m.get_profile_view(),
        "episodic": m.get_episodic_view(),
        "working": m.get_working_view(),
        "thoughts": m.get_thoughts_view(),
        "biology": agent.biology.get_view(),
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


@app.post("/api/speak")
async def api_speak(text: str = Form(...)):
    """Convert text to speech, return base64 mp3."""
    try:
        audio_bytes = await synthesize(text)
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
