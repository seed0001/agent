"""TTS dispatcher.

Public API: ``synthesize(text, voice) -> bytes`` (mp3 bytes).
Engines:
  - "edge": Microsoft Edge TTS (cloud, free, fast, fixed voices)
  - "lux":  LuxTTS (local voice clone from data/voices/<ref>.mp3)

Selected via ``TTS_ENGINE`` env var. The ``voice`` argument is the Edge voice ID
when engine=edge; with engine=lux the configured ``LUX_VOICE_REF`` is used and
the argument is ignored. Falls back to Edge if Lux errors out so Andrew never
loses his voice.
"""
import io
import re
from pathlib import Path

import edge_tts

from config.settings import (
    EDGE_TTS_VOICE,
    LUX_DEVICE,
    LUX_NUM_STEPS,
    LUX_VOICE_REF,
    TTS_ENGINE,
)


def _sanitize_for_tts(text: str) -> str:
    """Strip markdown so TTS doesn't read delimiters aloud."""
    if not text or not isinstance(text, str):
        return text
    s = text
    s = re.sub(r"\*\*(.+?)\*\*", r"\1", s)
    s = re.sub(r"\*(.+?)\*", r"\1", s)
    s = re.sub(r"__(.+?)__", r"\1", s)
    s = re.sub(r"_(.+?)_", r"\1", s)
    s = re.sub(r"`([^`]+)`", r"\1", s)
    s = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", s)
    s = re.sub(r"\[([^\]]+)\]", r"\1", s)
    s = re.sub(r"^#{1,6}\s+", "", s, flags=re.MULTILINE)
    s = re.sub(r"#+(\w+)", r"\1", s)
    s = re.sub(r"#+", " ", s)
    s = re.sub(r"^>\s*", "", s, flags=re.MULTILINE)
    s = re.sub(r"\s*\|\s*", " ", s)
    s = re.sub(r"[*_]{1,}", " ", s)
    s = re.sub(r"^[-*]{2,}\s*$", "", s, flags=re.MULTILINE)
    s = re.sub(r"  +", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s).strip()
    return s


async def _synthesize_edge(text: str, voice: str) -> bytes:
    communicate = edge_tts.Communicate(text, voice)
    buf = io.BytesIO()
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            buf.write(chunk["data"])
    return buf.getvalue()


async def synthesize(text: str, voice: str = EDGE_TTS_VOICE) -> bytes:
    """Convert text to speech, return mp3 bytes.

    Engine is selected by TTS_ENGINE env var. ``voice`` is honored only by the
    edge engine; lux uses LUX_VOICE_REF from settings.
    """
    clean = _sanitize_for_tts(text)
    if not clean:
        return b""

    if TTS_ENGINE == "lux":
        try:
            from src.voice.lux_tts import synthesize_lux

            return await synthesize_lux(
                clean,
                voice=LUX_VOICE_REF,
                device=LUX_DEVICE,
                num_steps=LUX_NUM_STEPS,
            )
        except Exception as e:
            import os
            import traceback

            if os.getenv("TTS_DEBUG"):
                traceback.print_exc()
            try:
                from src.logging_config import log_error

                log_error("tts_lux_fallback", e)
            except Exception:
                pass
    return await _synthesize_edge(clean, voice)


async def save_to_file(text: str, output_path: str | Path, voice: str = EDGE_TTS_VOICE) -> str:
    """Synthesize and save mp3 to file."""
    audio = await synthesize(text, voice)
    p = Path(output_path)
    p.write_bytes(audio)
    return str(p)
