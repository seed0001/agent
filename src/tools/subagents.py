"""Sub-agent / daemon manager - spawn background workers for tasks."""
import asyncio
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from config.settings import PROJECT_ROOT
from src.tools.training_env import env_for_child, script_command

# Set by app on startup: called when a subagent completes (aid, task, status)
_completion_callback: Callable[[str, str, str], None] | None = None


def set_completion_callback(cb: Callable[[str, str, str], None] | None) -> None:
    global _completion_callback
    _completion_callback = cb


@dataclass
class SubAgent:
    """A running sub-agent/daemon."""
    id: str
    task: str
    started_at: datetime = field(default_factory=datetime.now)
    process: asyncio.subprocess.Process | None = None
    status: str = "running"
    output: list[str] = field(default_factory=list)


class SubAgentManager:
    """Spawns and tracks sub-agent processes."""

    def __init__(self):
        self.agents: dict[str, SubAgent] = {}
        self._counter = 0

    def spawn(self, task: str, script_path: str, args: list[str] | None = None) -> str:
        """Spawn a sub-agent. Returns agent id."""
        self._counter += 1
        aid = f"sub_{self._counter}"
        agent = SubAgent(id=aid, task=task)
        self.agents[aid] = agent
        try:
            from src.logging_config import log_subagent_spawn
            log_subagent_spawn(aid, task, script_path)
        except Exception:
            pass
        asyncio.create_task(self._run_agent(aid, script_path, args or []))
        return aid

    async def _run_agent(self, aid: str, script_path: str, args: list[str]):
        p = Path(script_path)
        if not p.is_absolute():
            p = PROJECT_ROOT / script_path
        resolved = str(p.resolve())
        import os
        env = env_for_child(script_command(resolved, args), os.environ.copy())
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable, resolved, *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=env,
            )
            self.agents[aid].process = proc
            out, _ = await proc.communicate()
            text = out.decode("utf-8", errors="replace")
            self.agents[aid].output.append(text)
            self.agents[aid].status = "completed"
            try:
                from src.logging_config import log_subagent_status
                log_subagent_status(aid, "completed", "")
            except Exception:
                pass
            try:
                if _completion_callback:
                    _completion_callback(aid, self.agents[aid].task, "completed")
            except Exception:
                pass
        except Exception as e:
            self.agents[aid].output.append(str(e))
            self.agents[aid].status = "failed"
            try:
                from src.logging_config import log_subagent_status
                log_subagent_status(aid, "failed", str(e)[:100])
            except Exception:
                pass
            try:
                if _completion_callback:
                    _completion_callback(aid, self.agents[aid].task, "failed")
            except Exception:
                pass

    def status(self, aid: str | None = None) -> str:
        """Get status of one or all sub-agents."""
        if aid:
            a = self.agents.get(aid)
            return f"{a.id}: {a.status}" if a else "Unknown agent"
        if not self.agents:
            return "No sub-agents running"
        return "\n".join(f"{a.id}: {a.task} - {a.status}" for a in self.agents.values())

    def get_output(self, aid: str) -> str:
        """Get captured stdout/stderr from a completed sub-agent."""
        a = self.agents.get(aid)
        if not a:
            return f"Unknown agent: {aid}"
        if not a.output:
            return f"{a.id}: no output captured (status={a.status})"
        return "\n".join(a.output)

    def stop_all(self) -> int:
        """Terminate all running sub-agents. Returns count stopped."""
        stopped = 0
        for a in list(self.agents.values()):
            if a.process and a.process.returncode is None:
                try:
                    a.process.terminate()
                    stopped += 1
                    a.status = "stopped"
                except ProcessLookupError:
                    pass
        return stopped
