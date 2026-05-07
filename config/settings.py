"""Configuration for the assistive operating agent."""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Paths
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
MEMORY_DIR = DATA_DIR / "memory"
USER_PROFILES_DIR = DATA_DIR / "profiles"
LOGS_DIR = PROJECT_ROOT / "logs"

KNOWLEDGE_DIR = PROJECT_ROOT / "knowledge"
RESEARCH_OUTPUT_DIR = DATA_DIR / "research_output"
TRAINING_DATA_DIR = DATA_DIR / "training_data"
SOUL_TRAINING_DIR = DATA_DIR / "soul_training"
VOICES_DIR = DATA_DIR / "voices"

# Generated images (gitignored). Override with IMAGE_OUTPUT_DIR env (e.g. ~/Pictures/Adam).
IMAGE_OUTPUT_DIR = Path(
    os.getenv("IMAGE_OUTPUT_DIR", str(PROJECT_ROOT / "generated_images"))
).expanduser().resolve()

for d in (DATA_DIR, MEMORY_DIR, USER_PROFILES_DIR, LOGS_DIR, KNOWLEDGE_DIR, RESEARCH_OUTPUT_DIR, TRAINING_DATA_DIR, SOUL_TRAINING_DIR, VOICES_DIR):
    d.mkdir(parents=True, exist_ok=True)

# xAI Grok
XAI_API_KEY = os.getenv("XAI_API_KEY", "")
XAI_BASE_URL = "https://api.x.ai/v1"
XAI_MODEL = os.getenv("XAI_MODEL", "grok-3")

# Voice
EDGE_TTS_VOICE = "en-GB-RyanNeural"  # British male - Ryan (fallback)
TTS_ENGINE = os.getenv("TTS_ENGINE", "edge").strip().lower()  # "lux" | "edge"
LUX_VOICE_REF = os.getenv("LUX_VOICE_REF", "edward").strip()  # name in data/voices/ or full path
LUX_DEVICE = os.getenv("LUX_DEVICE", "cpu").strip().lower()  # "cpu" | "cuda" (ROCm shim)
LUX_NUM_STEPS = int(os.getenv("LUX_NUM_STEPS", "4"))  # 3-4 best speed/quality tradeoff

# Web (0.0.0.0 = accessible from phone on local network)
WEB_HOST = os.getenv("WEB_HOST", "0.0.0.0")
WEB_PORT = int(os.getenv("WEB_PORT", "8765"))

# Cursor CLI (escalation when Doctor Mode exhausts attempts)
CURSOR_CLI_CMD = os.getenv("CURSOR_CLI_CMD", "agent")
CURSOR_API_KEY = os.getenv("CURSOR_API_KEY", "")

# Discord (bot + proactive outreach)
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")
DISCORD_OWNER_ID = os.getenv("DISCORD_OWNER_ID", "")  # Primary owner Discord ID for DMs

# Soul training base model (Hugging Face)
SOUL_BASE_MODEL = os.getenv("SOUL_BASE_MODEL", "TinyLlama/TinyLlama-1.1B-Chat-v1.0")
