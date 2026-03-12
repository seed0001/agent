"""Edge TTS - Ryan (British male)."""
import asyncio
import io
from pathlib import Path

import edge_tts

from config.settings import EDGE_TTS_VOICE


async def synthesize(text: str, voice: str = EDGE_TTS_VOICE) -> bytes:
    """Convert text to speech, return audio bytes (mp3)."""
    communicate = edge_tts.Communicate(text, voice)
    buf = io.BytesIO()
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            buf.write(chunk["data"])
    return buf.getvalue()


async def save_to_file(text: str, output_path: str | Path, voice: str = EDGE_TTS_VOICE) -> str:
    """Synthesize and save to file."""
    audio = await synthesize(text, voice)
    p = Path(output_path)
    p.write_bytes(audio)
    return str(p)
