"""System access tools: files, processes, clipboard, system info."""
import asyncio
import os
import platform
import shutil
import subprocess
from pathlib import Path


async def read_file(path: str) -> str:
    """Read file contents."""
    p = Path(path).expanduser()
    if not p.exists():
        return f"Error: File not found: {path}"
    if not p.is_file():
        return f"Error: Not a file: {path}"
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return f"Error reading file: {e}"


async def write_file(path: str, content: str) -> str:
    """Write content to file."""
    p = Path(path).expanduser()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"Written to {path}"
    except Exception as e:
        return f"Error writing file: {e}"


async def list_dir(path: str) -> str:
    """List directory contents."""
    p = Path(path).expanduser() if path else Path.cwd()
    if not p.exists():
        return f"Error: Path not found: {path or '.'}"
    if not p.is_dir():
        return f"Error: Not a directory: {p}"
    try:
        items = list(p.iterdir())
        lines = [f"  {x.name}{'/' if x.is_dir() else ''}" for x in sorted(items, key=lambda x: (not x.is_dir(), x.name))]
        return "\n".join(lines) if lines else "(empty)"
    except Exception as e:
        return f"Error listing: {e}"


async def run_command(cmd: str, cwd: str | None = None, timeout: int = 60) -> str:
    """Run shell command. Use carefully."""
    try:
        result = await asyncio.wait_for(
            asyncio.create_subprocess_shell(
                cmd,
                cwd=cwd or os.getcwd(),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            ),
            timeout=timeout,
        )
        out = (result.stdout or b"").decode("utf-8", errors="replace").strip()
        err = (result.stderr or b"").decode("utf-8", errors="replace").strip()
        if err:
            return f"stdout:\n{out}\n\nstderr:\n{err}\n\nexit: {result.returncode}"
        return f"{out}\n\nexit: {result.returncode}"
    except asyncio.TimeoutError:
        return "Error: Command timed out"
    except Exception as e:
        return f"Error: {e}"


async def get_system_info() -> str:
    """Get basic system info."""
    return (
        f"OS: {platform.system()} {platform.release()}\n"
        f"Machine: {platform.machine()}\n"
        f"User: {os.environ.get('USERNAME', os.environ.get('USER', 'unknown'))}\n"
        f"CWD: {os.getcwd()}"
    )
