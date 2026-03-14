"""Edge TTS - Ryan (British male)."""
import re
import io
from pathlib import Path

import edge_tts

from config.settings import EDGE_TTS_VOICE


def _sanitize_for_tts(text: str) -> str:
    """Strip markdown so TTS doesn't read delimiters aloud (asterisk, hashtag, backtick, etc.)."""
    if not text or not isinstance(text, str):
        return text
    s = text
    # Remove **bold** and *italic* – keep inner text
    s = re.sub(r"\*\*(.+?)\*\*", r"\1", s)
    s = re.sub(r"\*(.+?)\*", r"\1", s)
    s = re.sub(r"__(.+?)__", r"\1", s)
    s = re.sub(r"_(.+?)_", r"\1", s)
    # Remove `code` – keep inner text
    s = re.sub(r"`([^`]+)`", r"\1", s)
    # Remove [link](url) – keep link text only
    s = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", s)
    s = re.sub(r"\[([^\]]+)\]", r"\1", s)
    # Remove # headers – keep text (## ### etc)
    s = re.sub(r"^#{1,6}\s+", "", s, flags=re.MULTILINE)
    # Remove # mid-text (hashtags)
    s = re.sub(r"#+(\w+)", r"\1", s)
    s = re.sub(r"#+", " ", s)
    # Remove > blockquote prefix
    s = re.sub(r"^>\s*", "", s, flags=re.MULTILINE)
    # Remove pipe (table separator)
    s = re.sub(r"\s*\|\s*", " ", s)
    # Remove orphan asterisks/underscores
    s = re.sub(r"[*_]{1,}", " ", s)
    # Remove horizontal rules (---, ***)
    s = re.sub(r"^[-*]{2,}\s*$", "", s, flags=re.MULTILINE)
    # Collapse extra spaces and newlines
    s = re.sub(r"  +", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s).strip()
    return s


async def synthesize(text: str, voice: str = EDGE_TTS_VOICE) -> bytes:
    """Convert text to speech, return audio bytes (mp3). Strips markdown for cleaner speech."""
    clean = _sanitize_for_tts(text)
    communicate = edge_tts.Communicate(clean, voice)
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
