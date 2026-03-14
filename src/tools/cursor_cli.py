"""
Cursor CLI escalation. When Doctor Mode exhausts attempts, queries Cursor CLI for fix.
Runs: agent -p "prompt" --output-format text
Uses CURSOR_API_KEY from env for authentication.
"""
import asyncio
import os
import shutil

from config.settings import CURSOR_API_KEY, PROJECT_ROOT


async def ask_cursor_cli(prompt: str, cwd: str | None = None, timeout: int = 120) -> str:
    """
    Run Cursor CLI in print mode with the given prompt.
    Returns the text output. Used when agent has tried 3+ approaches and failed.
    """
    cmd = shutil.which("agent") or shutil.which("cursor")
    if not cmd:
        return "Error: Cursor CLI (agent or cursor) not found in PATH. Install: irm 'https://cursor.com/install?win32=true' | iex"

    run_cwd = cwd or str(PROJECT_ROOT)
    safe_prompt = prompt.replace('"', "'").replace("\n", " ").strip()[:2000]
    full_cmd = f'{cmd} -p "{safe_prompt}" --output-format text'
    env = os.environ.copy()
    if CURSOR_API_KEY:
        env["CURSOR_API_KEY"] = CURSOR_API_KEY

    try:
        proc = await asyncio.create_subprocess_shell(
            full_cmd,
            cwd=run_cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        text = (out or b"").decode("utf-8", errors="replace").strip()
        err_text = (err or b"").decode("utf-8", errors="replace").strip()
        if err_text and "error" in err_text.lower():
            return f"Cursor CLI error: {err_text[:500]}"
        return text if text else "Cursor CLI returned no output."
    except asyncio.TimeoutError:
        return "Error: Cursor CLI timed out."
    except Exception as e:
        return f"Error running Cursor CLI: {e}"
