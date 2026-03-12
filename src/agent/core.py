"""Core agent: Grok 3 client, tool routing, Doctor Mode integration."""
import json
from typing import Any

from openai import AsyncOpenAI

from config.settings import XAI_API_KEY, XAI_BASE_URL, XAI_MODEL
from src.agent.dag import DAGOrchestrator
from src.agent.doctor_mode import DoctorMode, FailureEvent, FailureKind
from src.agent.memory import MemoryStore
from src.tools import system, build, subagents, search

# Lazy sub-agent manager
_subagent_manager: subagents.SubAgentManager | None = None


def _get_subagent_manager() -> subagents.SubAgentManager:
    global _subagent_manager
    if _subagent_manager is None:
        _subagent_manager = subagents.SubAgentManager()
    return _subagent_manager


TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read contents of a file",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write content to a file",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "List directory contents",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "Run a shell command. Use carefully.",
            "parameters": {
                "type": "object",
                "properties": {
                    "cmd": {"type": "string"},
                    "cwd": {"type": "string"},
                    "timeout": {"type": "integer"},
                },
                "required": ["cmd"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_system_info",
            "description": "Get system info (OS, user, cwd)",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": "Search the web for real-time information. Use for current events, facts, news.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "max_results": {"type": "integer", "description": "Max results (default 8)"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_build",
            "description": "Build a web or Python project (npm/pip). Auto-detects web vs python.",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_path": {"type": "string"},
                    "project_type": {"type": "string", "enum": ["auto", "web", "python"]},
                },
                "required": ["project_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "spawn_subagent",
            "description": "Spawn a background sub-agent/daemon to run a script. Returns agent id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {"type": "string", "description": "Brief description of the task"},
                    "script_path": {"type": "string", "description": "Path to Python script to run"},
                    "args": {"type": "array", "items": {"type": "string"}, "description": "Optional script args"},
                },
                "required": ["task", "script_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "subagent_status",
            "description": "Get status of sub-agents (or a specific one by id).",
            "parameters": {
                "type": "object",
                "properties": {"agent_id": {"type": "string"}},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_task_dag",
            "description": "Create a multi-step task DAG for complex work. Steps run in dependency order.",
            "parameters": {
                "type": "object",
                "properties": {
                    "nodes": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "action": {"type": "string"},
                                "depends_on": {"type": "array", "items": {"type": "string"}},
                            },
                            "required": ["id", "action"],
                        },
                    },
                },
                "required": ["nodes"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_next_dag_step",
            "description": "Get the next step to execute in the current task DAG.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "complete_dag_step",
            "description": "Mark a DAG step as done (or failed). Call after executing a step.",
            "parameters": {
                "type": "object",
                "properties": {
                    "node_id": {"type": "string"},
                    "success": {"type": "boolean", "default": True},
                    "result": {"type": "string"},
                    "error": {"type": "string"},
                },
                "required": ["node_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_working_memory",
            "description": "Store info in working memory (active task state). Use for multi-step tasks.",
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {"type": "string"},
                    "value": {"type": "string"},
                },
                "required": ["key", "value"],
            },
        },
    },
]


def _is_tool_error(result: str) -> bool:
    """Check if tool result indicates failure."""
    r = (result or "").strip().lower()
    return r.startswith("error") or "error:" in r or "not found" in r[:100]


class AssistiveAgent:
    """Main agent: Grok 3 + memory + Doctor Mode."""

    def __init__(self, user_id: str = "default"):
        self.client = AsyncOpenAI(api_key=XAI_API_KEY, base_url=XAI_BASE_URL)
        self.model = XAI_MODEL
        self.memory = MemoryStore(user_id=user_id)
        self.doctor = DoctorMode()
        self.dag = DAGOrchestrator()
        self.messages: list[dict[str, str]] = []

    async def _run_tool(self, name: str, args: dict[str, Any]) -> str:
        """Execute a tool, apply Doctor Mode on error."""
        result: str
        if name == "read_file":
            result = await system.read_file(args["path"])
        elif name == "write_file":
            result = await system.write_file(args["path"], args["content"])
        elif name == "list_dir":
            result = await system.list_dir(args.get("path", ""))
        elif name == "run_command":
            result = await system.run_command(
                args["cmd"],
                cwd=args.get("cwd"),
                timeout=args.get("timeout", 60),
            )
        elif name == "get_system_info":
            result = await system.get_system_info()
        elif name == "search_web":
            result = await search.search_web(
                args["query"],
                max_results=args.get("max_results", 8),
            )
        elif name == "run_build":
            result = await build.run_build(
                args["project_path"],
                args.get("project_type", "auto"),
            )
        elif name == "spawn_subagent":
            mgr = _get_subagent_manager()
            aid = mgr.spawn(
                args["task"],
                args["script_path"],
                args.get("args") or [],
            )
            result = f"Spawned sub-agent {aid} for task: {args['task']}"
        elif name == "subagent_status":
            mgr = _get_subagent_manager()
            result = mgr.status(args.get("agent_id"))
        elif name == "create_task_dag":
            self.dag = DAGOrchestrator()
            for n in args.get("nodes", []):
                self.dag.add_node(
                    n["id"],
                    n["action"],
                    n.get("depends_on") or [],
                )
            self.dag.build_order()
            self.memory.set_working("active_dag", "yes")
            result = f"Created DAG with {len(self.dag.nodes)} nodes. Order: {self.dag.execution_order}"
        elif name == "get_next_dag_step":
            next_id = self.dag.get_next_node()
            if not next_id:
                result = "No more steps. DAG complete or empty."
            else:
                node = self.dag.nodes[next_id]
                result = f"Next step: {next_id} - {node.action}"
        elif name == "complete_dag_step":
            nid = args["node_id"]
            if args.get("success", True):
                self.dag.mark_done(nid, args.get("result"))
                result = f"Step {nid} marked done."
            else:
                self.dag.mark_failed(nid, args.get("error", "Unknown error"))
                result = f"Step {nid} marked failed: {args.get('error', '')}"
            if not self.dag.get_next_node():
                self.memory.set_working("active_dag", None)
        elif name == "set_working_memory":
            self.memory.set_working(args["key"], args["value"])
            result = f"Working memory '{args['key']}' set."
        else:
            result = f"Unknown tool: {name}"

        if _is_tool_error(result):
            result = self.doctor.suggest_for_tool_error(name, result)
        return result

    async def chat(self, user_input: str) -> str:
        """Process user input, call tools if needed, return response."""
        import asyncio

        self.memory.add_immediate(f"User: {user_input}")
        self.memory.add_short_term(f"User: {user_input}")

        self.messages.append({"role": "user", "content": user_input})
        context = self.memory.get_context_for_agent()
        system_prompt = (
            "You are an assistive operating agent. You help the user with tasks on their system. "
            "You have: file read/write, run_command, get_system_info, search_web (real-time info), run_build (web/Python), "
            "spawn_subagent (background tasks), create_task_dag / get_next_dag_step / complete_dag_step (multi-step work), "
            "and set_working_memory for active task state. "
            "Use DAGs for complex multi-step tasks. Use Doctor Mode suggestions when a tool fails. "
            "Be clear, calm, and user-friendly. When something fails, try alternative approaches. "
            "Explain what you're doing when helpful."
        )
        if context:
            system_prompt += f"\n\nContext:\n{context}"

        messages_for_api = [{"role": "system", "content": system_prompt}] + self.messages

        attempts = 0
        max_attempts = 3

        while attempts < max_attempts:
            try:
                response = await self.client.chat.completions.create(
                    model=self.model,
                    messages=messages_for_api,
                    tools=TOOL_DEFINITIONS if TOOL_DEFINITIONS else None,
                    tool_choice="auto" if TOOL_DEFINITIONS else None,
                )
                break
            except Exception as e:
                attempts += 1
                failure = FailureEvent(
                    kind=self.doctor.diagnose(e),
                    message=str(e),
                    context={"attempt": attempts},
                )
                self.doctor.current_failure = failure
                strategies = self.doctor.generate_strategies(failure)
                if not strategies or attempts >= max_attempts:
                    return self.doctor.user_facing_message(failure, in_progress=False)
                failure.attempted_strategies.append("retry")
                await asyncio.sleep(1)

        choice = response.choices[0]
        msg = choice.message
        content = msg.content or ""

        if msg.tool_calls:
            for tc in msg.tool_calls:
                name = tc.function.name
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                result = await self._run_tool(name, args)
                self.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": str(result)[:4000],
                    }
                )
            return await self.chat("")  # Continue with tool results

        self.memory.add_short_term(f"Assistant: {content}")
        self.messages.append({"role": "assistant", "content": content})
        return content
