"""System access tools: files, processes, clipboard, system info."""
import asyncio
import os
import platform
import shutil
import subprocess
import tempfile
from pathlib import Path

from src.tools.training_env import env_for_child


def _resolve(path: str) -> Path:
    """Expand ``~`` and resolve to an absolute path.

    Resolution is done with ``strict=False`` so non-existent parents are
    still normalized (important for write_file on a brand-new path).
    Returns the absolute Path; the caller decides what to do with it.
    """
    return Path(path).expanduser().resolve(strict=False)


def _log_write(abs_path: Path, ok: bool, detail: str) -> None:
    """Best-effort write-to-agent-log. Never raises."""
    try:
        from src.logging_config import log_tool_result
        log_tool_result(
            "write_file",
            f"{'OK' if ok else 'FAIL'} {abs_path} :: {detail}",
            is_error=not ok,
        )
    except Exception:
        pass


async def read_file(path: str) -> str:
    """Read file contents.

    Always echoes the resolved absolute path in error messages so the caller
    can see exactly where we looked — fixes the "I said it's there but you
    can't find it" confusion.
    """
    abs_p = _resolve(path)
    if not abs_p.exists():
        return f"Error: File not found at {abs_p} (you passed: {path!r})"
    if not abs_p.is_file():
        return f"Error: Not a file: {abs_p}"
    try:
        return abs_p.read_text(encoding="utf-8", errors="replace")
    except PermissionError as e:
        return f"Error: Permission denied reading {abs_p}: {e}"
    except OSError as e:
        return f"Error reading {abs_p}: {e}"


async def write_file(path: str, content: str) -> str:
    """Write content atomically and verify the result.

    Behavior:
    - Resolves ``path`` to an absolute path before doing anything.
    - Creates parent directories as needed.
    - Writes to a sibling temp file in the same directory, fsyncs, then
      atomically replaces the destination. A crash mid-write leaves the
      destination either fully old or fully new — never partial.
    - After the replace, re-stats the destination to confirm both that the
      file exists AND that its byte count matches what we wrote.
    - The success message includes the absolute path AND the byte count, so
      callers always have unambiguous information to report back.
    - Specific exception handling: PermissionError, IsADirectoryError,
      OSError with errno, etc. each return distinct, actionable messages.
    """
    if content is None:
        # Don't pretend we wrote something. Empty string is a valid empty
        # file — None is a programming error worth surfacing.
        return "Error: write_file called with content=None (use empty string for an empty file)"
    if not isinstance(content, str):
        return f"Error: write_file content must be str, got {type(content).__name__}"

    abs_p = _resolve(path)
    expected_bytes = len(content.encode("utf-8"))

    # Refuse to overwrite a directory with a file — Path.write_text would
    # raise IsADirectoryError but the message would be opaque.
    if abs_p.exists() and abs_p.is_dir():
        msg = f"Error: refusing to overwrite directory at {abs_p}"
        _log_write(abs_p, False, msg)
        return msg

    try:
        abs_p.parent.mkdir(parents=True, exist_ok=True)
    except PermissionError as e:
        msg = f"Error: permission denied creating parent of {abs_p}: {e}"
        _log_write(abs_p, False, msg)
        return msg
    except OSError as e:
        msg = f"Error: could not create parent of {abs_p}: {e}"
        _log_write(abs_p, False, msg)
        return msg

    # Atomic write: temp in the same dir (so os.replace is a true rename
    # within the same filesystem), fsync, then replace.
    tmp_path: Path | None = None
    try:
        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{abs_p.name}.", suffix=".tmp", dir=str(abs_p.parent)
        )
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
                f.write(content)
                f.flush()
                try:
                    os.fsync(f.fileno())
                except OSError:
                    # Some filesystems (network, virtual) don't support fsync.
                    # Not fatal — atomic replace still gives us all-or-nothing.
                    pass
        except Exception:
            # Make sure the temp file doesn't linger on a write failure.
            try:
                tmp_path.unlink()
            except OSError:
                pass
            raise
        os.replace(tmp_path, abs_p)
        tmp_path = None  # ownership transferred
    except PermissionError as e:
        msg = f"Error: permission denied writing {abs_p}: {e}"
        _log_write(abs_p, False, msg)
        return msg
    except IsADirectoryError as e:
        msg = f"Error: target is a directory at {abs_p}: {e}"
        _log_write(abs_p, False, msg)
        return msg
    except FileNotFoundError as e:
        msg = f"Error: parent directory missing for {abs_p}: {e}"
        _log_write(abs_p, False, msg)
        return msg
    except OSError as e:
        # Catches No space left on device, Read-only filesystem, etc.
        errno_label = f"errno={e.errno}" if e.errno is not None else "no errno"
        msg = f"Error: OS error writing {abs_p} ({errno_label}): {e}"
        _log_write(abs_p, False, msg)
        return msg
    finally:
        # Belt-and-suspenders cleanup if the temp leaked.
        if tmp_path is not None:
            try:
                tmp_path.unlink()
            except OSError:
                pass

    # Verify: the file we just wrote must exist with the byte count we expect.
    # If either fails, something pathological happened (AV quarantine, etc.)
    # and we MUST tell the truth instead of returning a misleading success.
    try:
        actual_bytes = abs_p.stat().st_size
    except OSError as e:
        msg = f"Error: write to {abs_p} reported success but stat failed: {e}"
        _log_write(abs_p, False, msg)
        return msg

    if actual_bytes != expected_bytes:
        msg = (
            f"Error: write to {abs_p} did not verify: expected "
            f"{expected_bytes} bytes, found {actual_bytes}"
        )
        _log_write(abs_p, False, msg)
        return msg

    # Success — report the absolute path AND the byte count so the caller
    # has unambiguous information to relay to the user.
    success = f"Written and verified: {abs_p} ({actual_bytes} bytes)"
    _log_write(abs_p, True, f"{actual_bytes} bytes")
    return success


async def list_dir(path: str) -> str:
    """List directory contents. Always echoes the resolved absolute path."""
    abs_p = _resolve(path) if path else Path.cwd().resolve()
    if not abs_p.exists():
        return f"Error: Path not found at {abs_p} (you passed: {path!r})"
    if not abs_p.is_dir():
        return f"Error: Not a directory: {abs_p}"
    try:
        items = list(abs_p.iterdir())
        lines = [
            f"  {x.name}{'/' if x.is_dir() else ''}"
            for x in sorted(items, key=lambda x: (not x.is_dir(), x.name))
        ]
        body = "\n".join(lines) if lines else "(empty)"
        return f"# {abs_p}\n{body}"
    except PermissionError as e:
        return f"Error: permission denied listing {abs_p}: {e}"
    except OSError as e:
        return f"Error listing {abs_p}: {e}"


async def verify_file_exists(path: str) -> str:
    """Confirm a file exists at ``path`` and report its size.

    Use this whenever you've just written a file and want to be sure before
    telling the user it's there. Cheap to call.
    """
    abs_p = _resolve(path)
    if not abs_p.exists():
        return f"NOT FOUND: {abs_p} (you passed: {path!r})"
    if abs_p.is_dir():
        try:
            n = sum(1 for _ in abs_p.iterdir())
        except OSError:
            n = -1
        return f"DIRECTORY: {abs_p} ({n} entries)" if n >= 0 else f"DIRECTORY: {abs_p}"
    try:
        size = abs_p.stat().st_size
    except OSError as e:
        return f"EXISTS but stat failed for {abs_p}: {e}"
    return f"EXISTS: {abs_p} ({size} bytes)"


async def run_command(cmd: str, cwd: str | None = None, timeout: int = 60) -> str:
    """Run shell command. Use carefully."""
    try:
        proc = await asyncio.create_subprocess_shell(
            cmd,
            cwd=cwd or os.getcwd(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env_for_child(cmd),
        )
        out_bytes, err_bytes = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        out = (out_bytes or b"").decode("utf-8", errors="replace").strip()
        err = (err_bytes or b"").decode("utf-8", errors="replace").strip()
        if err:
            return f"stdout:\n{out}\n\nstderr:\n{err}\n\nexit: {proc.returncode}"
        return f"{out}\n\nexit: {proc.returncode}"
    except asyncio.TimeoutError:
        return "Error: Command timed out"
    except Exception as e:
        return f"Error: {e}"


async def list_processes(max_lines: int = 50) -> str:
    """List running processes. Works on Windows and Unix."""
    try:
        if platform.system() == "Windows":
            proc = await asyncio.create_subprocess_shell(
                'powershell -NoProfile -Command "Get-Process | Select-Object Name, Id, CPU, WorkingSet | Sort-Object CPU -Descending | Format-Table -AutoSize"',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        else:
            proc = await asyncio.create_subprocess_shell(
                "ps aux",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        out, err = await proc.communicate()
        text = (out or b"").decode("utf-8", errors="replace").strip()
        if err:
            err_text = (err or b"").decode("utf-8", errors="replace").strip()
            if err_text and "Error" in err_text:
                # Fallback: tasklist on Windows
                if platform.system() == "Windows":
                    proc2 = await asyncio.create_subprocess_shell(
                        "tasklist /FO TABLE",
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    out2, _ = await proc2.communicate()
                    text = (out2 or b"").decode("utf-8", errors="replace").strip()
                else:
                    return f"Error: {err_text}"
        lines = text.splitlines()
        if max_lines and len(lines) > max_lines:
            lines = lines[:max_lines]
            text = "\n".join(lines) + f"\n... ({max_lines} of many shown)"
        return text
    except Exception as e:
        return f"Error listing processes: {e}"


async def is_process_running(name: str) -> str:
    """Check if a process is running by name (case-insensitive, partial match). Returns PID(s) or 'not found'."""
    try:
        if platform.system() == "Windows":
            cmd = f'powershell -NoProfile -Command "Get-Process | Where-Object {{ $_.ProcessName -like \'*{name}*\' }} | Select-Object Name, Id | Format-Table -AutoSize"'
        else:
            cmd = f"pgrep -fl {name}" if name else "echo 'provide name'"
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await proc.communicate()
        text = (out or b"").decode("utf-8", errors="replace").strip()
        if not text or "Error" in (err or b"").decode("utf-8", errors="replace"):
            return f"Process matching '{name}': not found"
        return f"Process matching '{name}':\n{text}"
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
