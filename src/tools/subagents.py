"""Sub-agent / daemon manager - spawn background workers for tasks."""
import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


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
        # Fire and forget - actual spawn would be async
        asyncio.create_task(self._run_agent(aid, script_path, args or []))
        return aid

    async def _run_agent(self, aid: str, script_path: str, args: list[str]):
        try:
            proc = await asyncio.create_subprocess_exec(
                "python", script_path, *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            self.agents[aid].process = proc
            out, _ = await proc.communicate()
            text = out.decode("utf-8", errors="replace")
            self.agents[aid].output.append(text)
            self.agents[aid].status = "completed"
        except Exception as e:
            self.agents[aid].output.append(str(e))
            self.agents[aid].status = "failed"

    def status(self, aid: str | None = None) -> str:
        """Get status of one or all sub-agents."""
        if aid:
            a = self.agents.get(aid)
            return f"{a.id}: {a.status}" if a else "Unknown agent"
        if not self.agents:
            return "No sub-agents running"
        return "\n".join(f"{a.id}: {a.task} - {a.status}" for a in self.agents.values())
