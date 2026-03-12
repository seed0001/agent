"""
Speech-to-text for long recordings.
Uses faster-whisper (local, no API key) - handles long audio well.
Model is loaded lazily on first transcribe.
"""
import tempfile
from pathlib import Path

_model = None


def _get_model():
    global _model
    if _model is None:
        from faster_whisper import WhisperModel
        _model = WhisperModel("base", device="cpu", compute_type="int8")
    return _model


def transcribe_audio(audio_bytes: bytes, language: str = "en") -> str:
    """
    Transcribe audio bytes to text.
    Expects raw audio or common formats (wav, mp3, etc).
    """
    with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as f:
        f.write(audio_bytes)
        path = f.name

    try:
        model = _get_model()
        segments, _ = model.transcribe(path, language=language)
        return " ".join(s.text.strip() for s in segments if s.text).strip()
    finally:
        Path(path).unlink(missing_ok=True)
