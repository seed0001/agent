"""
Low-level Cursor CLI invocation. Two surfaces:

- ``ask_cursor_cli(prompt, ...)`` — read-only planning/explanation. Used by the
  Doctor Mode escalation path in ``src/agent/core.py`` to ask Cursor for a fix
  plan when Andrew's own tools have failed repeatedly. **Does not edit files.**
- ``run_cursor_cli(prompt, write=False, ...)`` — structured invocation that
  returns ``{ok, text, raw_json, returncode, error}``. Pass ``write=True`` to
  add ``--force`` (file-mutating) for callers that have already verified the
  speaker is allowed to mutate. Higher-level edit/verify tooling lives in
  ``src/tools/cursor_editor.py``.

Reference: https://cursor.com/docs/cli/reference/parameters
"""
from __future__ import annotations

import asyncio
import json
import os
import shlex
import shutil
from dataclasses import dataclass, field
from typing import Any

from config.settings import CURSOR_API_KEY, CURSOR_CLI_CMD, PROJECT_ROOT


_CURSOR_NOT_FOUND_MSG = (
    "Cursor headless CLI not found. The binary you want is `cursor-agent` "
    "(or its `agent` alias) — not the `cursor` IDE launcher. "
    "Install on Windows: irm 'https://cursor.com/install?win32=true' | iex "
    "Then close and reopen this app/terminal so PATH refreshes. "
    "You can also pin the path by setting CURSOR_CLI_CMD in .env to the full "
    "path of cursor-agent (e.g. C:\\\\Users\\\\you\\\\AppData\\\\Local\\\\cursor-agent\\\\cursor-agent.exe)."
)


# Names that mean "the Cursor IDE launcher", which we must never invoke
# headlessly because it just pops a GUI window. Match on basename, lowercase.
_GUI_LAUNCHER_NAMES = {"cursor", "cursor.exe", "cursor.cmd", "cursor.bat"}


@dataclass
class CursorRun:
    """Result of one Cursor CLI invocation."""

    ok: bool
    text: str
    raw_json: dict[str, Any] | None = None
    returncode: int | None = None
    error: str = ""
    duration_ms: int | None = None
    session_id: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "text": self.text,
            "returncode": self.returncode,
            "error": self.error,
            "duration_ms": self.duration_ms,
            "session_id": self.session_id,
            "raw_json": self.raw_json,
            **self.extra,
        }


def _is_gui_launcher(path: str) -> bool:
    """Reject the Cursor IDE launcher — running it would open a window, not run headless."""
    if not path:
        return True
    base = os.path.basename(path).lower()
    if base in _GUI_LAUNCHER_NAMES:
        return True
    # Defensive: even if the basename was renamed, if the resolved path lives in
    # a typical IDE install dir we treat it as the launcher.
    low = path.lower()
    if "cursor" in low and ("\\programs\\cursor" in low or "/programs/cursor" in low or "\\cursor\\cursor" in low):
        # cursor-agent typically lives in its own dir (cursor-agent\\cursor-agent.exe),
        # which won't match these specific patterns.
        if "cursor-agent" not in low:
            return True
    return False


def _resolve_binary() -> str | None:
    """
    Find the headless Cursor CLI. Order:
      1. CURSOR_CLI_CMD from .env — may be a bare name or absolute path.
      2. cursor-agent on PATH (the canonical headless binary name).
      3. agent on PATH (legacy alias some installs ship).

    The IDE launcher (cursor / cursor.exe) is explicitly rejected — invoking it
    here would open a Cursor window instead of running the headless agent.
    """
    candidates: list[str] = []
    if CURSOR_CLI_CMD:
        # Allow either an absolute path or a bare name resolved via PATH.
        if os.path.isabs(CURSOR_CLI_CMD) and os.path.isfile(CURSOR_CLI_CMD):
            candidates.append(CURSOR_CLI_CMD)
        else:
            resolved = shutil.which(CURSOR_CLI_CMD)
            if resolved:
                candidates.append(resolved)
    for name in ("cursor-agent", "agent"):
        resolved = shutil.which(name)
        if resolved:
            candidates.append(resolved)

    for c in candidates:
        if not _is_gui_launcher(c):
            return c
    return None


def _build_argv(
    binary: str,
    prompt: str,
    *,
    write: bool,
    output_format: str,
) -> list[str]:
    """Build a safe argv list (no shell). prompt is passed as a positional arg."""
    argv = [binary, "-p", prompt, "--output-format", output_format]
    if write:
        # ``--force`` (alias ``--yolo``) is required for file mutations in headless mode.
        argv.append("--force")
    return argv


async def run_cursor_cli(
    prompt: str,
    *,
    cwd: str | os.PathLike | None = None,
    write: bool = False,
    output_format: str = "json",
    timeout: int = 180,
) -> CursorRun:
    """
    Invoke Cursor CLI in headless print mode and return a structured result.

    write=False  → CLI runs without --force; it can read/search but will not
                   mutate files (returns its plan as text).
    write=True   → CLI runs with --force; it may create/modify/delete files in
                   ``cwd``. Caller is responsible for tier/path checks.
    """
    binary = _resolve_binary()
    if not binary:
        # Diagnostic dump so the user can see what was on PATH when we looked.
        from src.logging_config import log_cursor_cli as _log

        diag = {
            "CURSOR_CLI_CMD": CURSOR_CLI_CMD or "(unset)",
            "which_cursor_agent": shutil.which("cursor-agent") or "(missing)",
            "which_agent": shutil.which("agent") or "(missing)",
            "which_cursor": shutil.which("cursor") or "(missing)",
        }
        _log(False, f"resolver_failed | {diag}")
        return CursorRun(
            ok=False,
            text="",
            error=_CURSOR_NOT_FOUND_MSG + f" | diagnostic: {diag}",
        )

    run_cwd = str(cwd) if cwd else str(PROJECT_ROOT)
    argv = _build_argv(binary, prompt[:8000], write=write, output_format=output_format)

    env = os.environ.copy()
    if CURSOR_API_KEY:
        env["CURSOR_API_KEY"] = CURSOR_API_KEY

    from src.logging_config import log_cursor_cli, log_error

    log_cursor_cli(
        True,
        f"argv={shlex.join(argv[:2])} ... write={write} fmt={output_format} prompt_len={len(prompt)}",
    )

    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=run_cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        out_bytes, err_bytes = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        log_cursor_cli(True, f"timeout after {timeout}s")
        return CursorRun(ok=False, text="", error=f"Cursor CLI timed out after {timeout}s")
    except Exception as e:
        log_error("cursor_cli.run", e)
        return CursorRun(ok=False, text="", error=f"Cursor CLI launch failed: {e}")

    out = (out_bytes or b"").decode("utf-8", errors="replace").strip()
    err = (err_bytes or b"").decode("utf-8", errors="replace").strip()
    rc = proc.returncode

    if output_format == "json":
        parsed: dict[str, Any] | None = None
        try:
            parsed = json.loads(out) if out else None
        except json.JSONDecodeError:
            # Some CLI versions emit a leading banner before JSON; try last line.
            for line in reversed(out.splitlines()):
                line = line.strip()
                if line.startswith("{") and line.endswith("}"):
                    try:
                        parsed = json.loads(line)
                        break
                    except json.JSONDecodeError:
                        continue

        if parsed:
            text = (
                parsed.get("result")
                or parsed.get("output")
                or parsed.get("text")
                or out
            )
            ok = bool(rc == 0 and (parsed.get("ok", True) is not False))
            return CursorRun(
                ok=ok,
                text=str(text)[:20000],
                raw_json=parsed,
                returncode=rc,
                error="" if ok else (err or parsed.get("error", "") or "Cursor CLI returned an error"),
                duration_ms=parsed.get("duration_ms") or parsed.get("duration"),
                session_id=parsed.get("session_id") or parsed.get("sessionId", ""),
            )

        # JSON requested but unparseable. Fall through to text handling.
        log_cursor_cli(True, f"json_parse_failed rc={rc} stderr={err[:200]}")
        return CursorRun(
            ok=rc == 0,
            text=out[:20000],
            returncode=rc,
            error=err if rc != 0 else "",
        )

    # output_format == "text" or other
    ok = rc == 0 and not (err and "error" in err.lower())
    return CursorRun(
        ok=ok,
        text=out[:20000],
        returncode=rc,
        error=err if not ok else "",
    )


async def ask_cursor_cli(prompt: str, cwd: str | None = None, timeout: int = 120) -> str:
    """
    Backward-compatible wrapper used by the Doctor Mode escalation path.
    Read-only (no --force); returns a plain text fix plan.
    """
    res = await run_cursor_cli(
        prompt,
        cwd=cwd,
        write=False,
        output_format="text",
        timeout=timeout,
    )
    if not res.ok and res.error:
        return f"Cursor CLI error: {res.error[:500]}"
    return res.text or "Cursor CLI returned no output."
