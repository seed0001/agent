# Assistive Operating Agent

An assistive AI agent powered by Grok 3 (xAI), with full system access, multi-layer memory, Doctor Mode (self-healing), voice interface (Edge TTS - Ryan), and web dashboard.

## Setup

1. **Create `.env`** from the example:
   ```
   cp .env.example .env
   ```
   Add your xAI API key:
   ```
   XAI_API_KEY=your_key_here
   XAI_MODEL=grok-3
   ```

2. **Install dependencies:**
   ```
   pip install -r requirements.txt
   ```

3. **Run:**
   ```
   python main.py
   ```
   Dashboard: http://127.0.0.1:8765

## Features

- **Voice**: Record (no auto-stop) → Stop → Send. Edge TTS (Ryan, British male) for responses.
- **Web dashboard**: Chat, voice controls, toggles for "Speak responses" and "Listening".
- **Tools**: File read/write, list dir, run commands, system info, build projects.
- **Memory**: 5-layer (immediate, short-term, working, episodic, user profile).
- **Doctor Mode**: Built-in self-healing when something breaks.
- **DAG orchestration**: For multi-step task decomposition (foundation in place).

## API Key

Create a `.env` file in the project root with your xAI API key. Get keys at [console.x.ai](https://console.x.ai).
