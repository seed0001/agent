"""LuxTTS engine — local voice-cloned TTS via vendor/luxtts (zipvoice/luxvoice).

Produces MP3 bytes to match the existing TTS contract used by Discord and web.
The model and per-voice encoded prompts are cached at module level since both
are slow to construct (model ~7s, prompt ~10s on first encode).
"""
import asyncio
import io
import sys
from pathlib import Path
from threading import Lock

ROOT = Path(__file__).resolve().parents[2]
_LUXTTS_PATH = ROOT / "vendor" / "luxtts"
if _LUXTTS_PATH.exists() and str(_LUXTTS_PATH) not in sys.path:
    sys.path.insert(0, str(_LUXTTS_PATH))

_VOICES_DIR = ROOT / "data" / "voices"

_model = None
_model_lock = Lock()
_prompt_cache: dict[str, object] = {}


def _get_model(device: str = "cpu"):
    """Lazy-load and cache the LuxTTS model (process-wide singleton)."""
    global _model
    with _model_lock:
        if _model is None:
            from zipvoice.luxvoice import LuxTTS

            _model = LuxTTS("YatharthS/LuxTTS", device=device)
        return _model


def _resolve_voice_path(voice: str) -> Path:
    """Map a voice identifier to a reference audio file under data/voices/.

    Accepts:
      - absolute path to an audio file
      - filename relative to data/voices/ (with or without extension)
      - bare logical name like 'edward' → finds edward.{mp3,wav,flac,ogg}
    Falls back to the first audio file in data/voices/ if voice is empty.
    """
    if voice:
        p = Path(voice)
        if p.is_absolute() and p.exists():
            return p
        direct = _VOICES_DIR / voice
        if direct.exists() and direct.is_file():
            return direct
        for ext in (".mp3", ".wav", ".flac", ".ogg", ".m4a"):
            candidate = _VOICES_DIR / f"{voice}{ext}"
            if candidate.exists():
                return candidate
        if _VOICES_DIR.exists():
            for f in _VOICES_DIR.iterdir():
                if f.is_file() and f.stem.lower() == voice.lower():
                    return f
    if _VOICES_DIR.exists():
        for f in sorted(_VOICES_DIR.iterdir()):
            if f.is_file() and f.suffix.lower() in (".mp3", ".wav", ".flac", ".ogg", ".m4a"):
                return f
    raise FileNotFoundError(
        f"No voice reference for {voice!r}. Drop an audio file in {_VOICES_DIR}."
    )


def _get_prompt(model, voice_path: Path, ref_duration: int = 10, rms: float = 0.01):
    """Cache encoded prompts per file path (encoding is ~10s on first call)."""
    key = str(voice_path.resolve())
    if key not in _prompt_cache:
        _prompt_cache[key] = model.encode_prompt(
            str(voice_path), duration=ref_duration, rms=rms
        )
    return _prompt_cache[key]


def _generate_sync(
    text: str,
    voice: str,
    device: str,
    num_steps: int,
    t_shift: float,
    speed: float,
) -> bytes:
    import soundfile as sf

    voice_path = _resolve_voice_path(voice)
    model = _get_model(device)
    prompt = _get_prompt(model, voice_path)
    wav = model.generate_speech(
        text,
        prompt,
        num_steps=num_steps,
        t_shift=t_shift,
        speed=speed,
    )
    arr = wav.numpy().squeeze()
    buf = io.BytesIO()
    sf.write(buf, arr, 48000, format="MP3")
    return buf.getvalue()


async def synthesize_lux(
    text: str,
    voice: str = "",
    device: str = "cpu",
    num_steps: int = 4,
    t_shift: float = 0.9,
    speed: float = 1.0,
) -> bytes:
    """Async wrapper: runs the sync LuxTTS generation in a worker thread."""
    if not text or not text.strip():
        return b""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None, _generate_sync, text, voice, device, num_steps, t_shift, speed
    )


def warmup(device: str = "cpu", voice: str = "") -> None:
    """Eagerly load the model and pre-encode a voice (use during app startup
    to avoid the 15-20s first-call latency hitting a real user request)."""
    model = _get_model(device)
    try:
        path = _resolve_voice_path(voice)
        _get_prompt(model, path)
    except FileNotFoundError:
        pass
