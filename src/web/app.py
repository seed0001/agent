"""FastAPI app: dashboard, chat, voice."""
import base64
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from config.settings import WEB_HOST, WEB_PORT
from src.agent.core import AssistiveAgent
from src.voice.stt import transcribe_audio
from src.voice.tts import synthesize

# Global agent instance
agent: AssistiveAgent | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global agent
    agent = AssistiveAgent(user_id="default")
    yield
    agent = None


app = FastAPI(title="Assistive Agent", lifespan=lifespan)

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


@app.post("/api/chat")
async def api_chat(message: str = Form(...)):
    if not agent:
        return JSONResponse({"error": "Agent not ready"}, status_code=503)
    try:
        response = await agent.chat(message)
        return {"response": response}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


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
    import uvicorn
    uvicorn.run(app, host=WEB_HOST, port=WEB_PORT)


if __name__ == "__main__":
    run()
