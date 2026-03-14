"""Core agent: Grok 3 client, tool routing, Doctor Mode integration."""
import asyncio
import json
import random
from typing import Any

from openai import AsyncOpenAI

from config.settings import XAI_API_KEY, XAI_BASE_URL, XAI_MODEL, DISCORD_OWNER_ID
from src.agent.biology import DriveState
from src.agent.dag import DAGOrchestrator
from src.agent.doctor_mode import DoctorMode, FailureEvent, FailureKind
from src.agent.memory import MemoryStore
from src.agent import soul
from src import contacts, notifications
from src.logging_config import (
    log_cursor_cli,
    log_doctor_mode,
    log_escalation,
    log_error,
    log_tool_result,
    log_tool_start,
)
from src.tools import system, build, subagents, search, cursor_cli, knowledge, tool_queue, image_gen
from src.tools.dynamic_loader import load_dynamic_tools

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
            "name": "is_process_running",
            "description": "Check if a process is running by name (partial match, e.g. 'curiosity', 'python'). Use to confirm a daemon/service.",
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_processes",
            "description": "List running processes. Works on Windows and Unix. Use for monitoring.",
            "parameters": {
                "type": "object",
                "properties": {"max_lines": {"type": "integer", "description": "Max lines to return (default 50)"}},
                "required": [],
            },
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
            "name": "generate_image",
            "description": "Generate images from text prompts via Grok Imagine. Use for visual content, illustrations, data viz, style experiments. Check get_image_usage first for budget.",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "Text description of the image to generate"},
                    "n": {"type": "integer", "description": "Number of images (1-4, default 1)"},
                    "aspect_ratio": {"type": "string", "description": "e.g. 1:1, 16:9, 4:3 (default 1:1)"},
                    "save_path": {"type": "string", "description": "Optional path to save the first image"},
                },
                "required": ["prompt"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_image_usage",
            "description": "Check image generation usage (daily count, limit, remaining). Call before generate_image to stay within budget.",
            "parameters": {"type": "object", "properties": {}},
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
            "name": "get_subagent_output",
            "description": "Get captured output from a completed sub-agent. Use after subagent_status shows 'completed' to retrieve research results or script output.",
            "parameters": {
                "type": "object",
                "properties": {"agent_id": {"type": "string"}},
                "required": ["agent_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "stop_all_subagents",
            "description": "Terminate all running sub-agents. Use when the user says to stop sub-agents.",
            "parameters": {"type": "object", "properties": {}},
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
            "name": "search_knowledge",
            "description": "Search the knowledge base for how-tos. Use when unsure how to do something. Returns guides to read, then take action.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "What you want to do, e.g. 'list processes', 'build project', 'check if daemon running'"},
                    "max_results": {"type": "integer", "description": "Max docs to return (default 3)"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_knowledge",
            "description": "Read a specific knowledge topic. Use after list_knowledge_topics or when you know the topic name.",
            "parameters": {
                "type": "object",
                "properties": {"topic": {"type": "string", "description": "Topic name: files, processes, commands, search, build, subagents, dag, memory, system"}},
                "required": ["topic"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_knowledge_topics",
            "description": "List available knowledge base topics.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_suggested_tools",
            "description": "Add tool suggestions to the queue. Use after analyzing codebase for gaps. User approves in GUI.",
            "parameters": {
                "type": "object",
                "properties": {
                    "tools": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "description": {"type": "string"},
                                "parameters": {"type": "object"},
                                "reason": {"type": "string", "description": "Why this tool fills a gap"},
                            },
                            "required": ["name", "description"],
                        },
                    },
                },
                "required": ["tools"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_tool_queue",
            "description": "Get the tool queue: suggested, approved, implemented. Use to show user or before implementing.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "approve_tool",
            "description": "Approve a suggested tool (moves to queue for implementation). Use when user approves.",
            "parameters": {
                "type": "object",
                "properties": {"tool_id": {"type": "string"}},
                "required": ["tool_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mark_tool_implemented",
            "description": "Mark a tool as implemented after writing its code to src/tools/dynamic/. Call after write_file.",
            "parameters": {
                "type": "object",
                "properties": {"tool_id": {"type": "string"}, "file_path": {"type": "string"}},
                "required": ["tool_id"],
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
    {
        "type": "function",
        "function": {
            "name": "update_profile",
            "description": "Store a fact about the user in their long-term profile. Call this whenever the user shares personal info: name, location, occupation, hobbies, preferences, background, family, goals, etc. Categories: background, work, preferences, personal, other.",
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "enum": ["background", "work", "preferences", "personal", "other"],
                        "description": "Where to store: background (where from, history), work (job, role), preferences (likes, dislikes), personal (name, family, hobbies), other",
                    },
                    "fact": {"type": "string", "description": "The fact to remember, e.g. 'User is from Texas', 'Works as a software engineer'"},
                },
                "required": ["category", "fact"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_contact",
            "description": "Store or update info about a contact (friend, Discord user, etc.). Use when someone shares their name, location, interests, email. Only the Creator can set tier. Tiers: stranger, friend, good_friend, best_friend, creator.",
            "parameters": {
                "type": "object",
                "properties": {
                    "identifier": {"type": "string", "description": "Web user identifier, or empty for Discord"},
                    "discord_id": {"type": "string", "description": "Discord user ID when talking on Discord"},
                    "name": {"type": "string"},
                    "location": {"type": "string"},
                    "interests": {"type": "string"},
                    "email": {"type": "string"},
                    "notes": {"type": "string"},
                    "tier": {"type": "string", "enum": ["stranger", "friend", "good_friend", "best_friend", "creator"], "description": "Trust tier. Only Creator can change this."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_contacts",
            "description": "List all contacts (friends, Discord users) you have profiles for.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "swarm_on_problem",
            "description": "Run the swarm (only AFTER user has chosen cloud or local). Mode: 'local' = Ollama, 'cloud' = Grok simulating multiple neurons. Do NOT call until user has answered which they want.",
            "parameters": {
                "type": "object",
                "properties": {
                    "problem": {"type": "string", "description": "The problem to solve"},
                    "context": {"type": "string", "description": "Optional context"},
                    "mode": {
                        "type": "string",
                        "enum": ["local", "cloud"],
                        "description": "local = Ollama on your machine, cloud = Grok (multiple simulated calls)",
                    },
                },
                "required": ["problem", "mode"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "complete_setup",
            "description": "Complete setup: save owner_name, agent_name, how to act. Call only when you have both names.",
            "parameters": {
                "type": "object",
                "properties": {
                    "owner_name": {"type": "string", "description": "The name of your owner/creator. Required."},
                    "agent_name": {"type": "string", "description": "Name they call you. Required. Ask: 'What do you want to call me?'"},
                    "owner_discord_id": {"type": "string", "description": "Discord ID if they're messaging via Discord."},
                    "owner_facts": {"type": "array", "items": {"type": "string"}, "description": "Facts about the owner to store."},
                    "agent_tone": {"type": "array", "items": {"type": "string"}, "description": "Tone, e.g. ['direct']."},
                    "agent_how_to_act": {"type": "array", "items": {"type": "string"}, "description": "Guidelines for how to behave."},
                },
                "required": ["owner_name", "agent_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_proactive_message",
            "description": "Send a proactive message. Use when you have something concrete: an observation, a question, a heads-up, or a call to action. No fluff. Channel: discord (DM) or web (in-app notification).",
            "parameters": {
                "type": "object",
                "properties": {
                    "channel": {
                        "type": "string",
                        "enum": ["discord", "web"],
                        "description": "Where to send: discord = DM, web = in-app notification",
                    },
                    "content": {"type": "string", "description": "The message to send"},
                    "target_discord_id": {"type": "string", "description": "Discord user ID for DM (optional; defaults to owner)"},
                },
                "required": ["channel", "content"],
            },
        },
    },
]


def _get_tool_definitions() -> list:
    """Merge base tools + dynamic tools."""
    dyn_defs, _ = load_dynamic_tools()
    return TOOL_DEFINITIONS + dyn_defs


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
        self.biology = DriveState(self.memory.user_dir)
        self.doctor = DoctorMode()
        self.dag = DAGOrchestrator()
        self.messages: list[dict[str, str]] = []
        self._dynamic_runners: dict = {}
        self._escalation_count = 0
        self._reload_dynamic()

    def _reload_dynamic(self):
        _, self._dynamic_runners = load_dynamic_tools()

    def _get_current_speaker_tier(self) -> str:
        """Resolve current speaker's tier: creator (web or owner) or contact tier."""
        discord_id = self.memory.get_working("current_speaker_discord_id")
        if discord_id is None or discord_id == "":
            return "creator"
        if str(discord_id) == str(DISCORD_OWNER_ID or ""):
            return "creator"
        return contacts.get_contact_tier(str(discord_id))

    async def _run_tool(self, name: str, args: dict[str, Any]) -> str:
        """Execute a tool, apply Doctor Mode on error."""
        from config.access_policy import is_tool_allowed

        log_tool_start(name, {k: v for k, v in args.items() if k != "content"})
        tier = self._get_current_speaker_tier()
        if not is_tool_allowed(tier, name):
            return f"Tier {tier} doesn't include {name}. Creator can change access."
        if name == "update_contact" and "tier" in args and tier != "creator":
            args = {k: v for k, v in args.items() if k != "tier"}
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
        elif name == "is_process_running":
            result = await system.is_process_running(args.get("name", ""))
        elif name == "list_processes":
            result = await system.list_processes(args.get("max_lines", 50))
        elif name == "search_web":
            result = await search.search_web(
                args["query"],
                max_results=args.get("max_results", 8),
            )
        elif name == "generate_image":
            result = await image_gen.generate_image(
                args["prompt"],
                n=args.get("n", 1),
                aspect_ratio=args.get("aspect_ratio", "1:1"),
                save_path=args.get("save_path"),
            )
        elif name == "get_image_usage":
            result = image_gen.get_image_usage()
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
        elif name == "get_subagent_output":
            mgr = _get_subagent_manager()
            result = mgr.get_output(args.get("agent_id", ""))
        elif name == "stop_all_subagents":
            mgr = _get_subagent_manager()
            n = mgr.stop_all()
            result = f"Stopped {n} sub-agent(s)"
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
        elif name == "update_profile":
            result = self.memory.add_profile_fact(
                args.get("category", "other"),
                args.get("fact", ""),
            )
        elif name == "update_contact":
            result = contacts.update_contact(
                args.get("identifier", ""),
                discord_id=args.get("discord_id"),
                name=args.get("name"),
                location=args.get("location"),
                interests=args.get("interests"),
                email=args.get("email"),
                notes=args.get("notes"),
                tier=args.get("tier"),
            )
        elif name == "get_contacts":
            lst = contacts.get_all_contacts()
            result = json.dumps(lst, indent=2) if lst else "No contacts yet."
        elif name == "swarm_on_problem":
            from src.swarm.graph import run
            from src.swarm.crew_swarm import run_crew_cloud
            problem = (args.get("problem") or "").strip()
            context = (args.get("context") or "").strip()
            mode = (args.get("mode") or "local").lower()
            if mode not in ("local", "cloud"):
                mode = "local"
            if not problem:
                result = "No problem specified."
            else:
                prompt_prefix = "The user wants a structured, actionable solution. Format with clear sections: Summary, Steps, Recommendations."
                try:
                    if mode == "cloud":
                        signal = await run_crew_cloud(problem, context, prompt_prefix=prompt_prefix)
                    else:
                        inputs = [problem, context or "General context.", "Produce a structured solution: 1) Summary 2) Step-by-step approach 3) Key recommendations. Be clear and actionable."]
                        signal = await run(inputs, prompt_prefix=prompt_prefix)
                    result = f"**Swarm output ({mode}):**\n\n{signal.content}"
                except Exception as e:
                    result = f"Swarm error: {e}"
        elif name == "complete_setup":
            if not soul.needs_setup():
                result = "Setup already complete. No changes made."
            else:
                result = soul.complete_setup(
                    owner_name=args.get("owner_name", ""),
                    agent_name=args.get("agent_name", ""),
                    owner_discord_id=args.get("owner_discord_id", "") or "",
                    owner_facts=args.get("owner_facts") or [],
                    agent_tone=args.get("agent_tone"),
                    agent_how_to_act=args.get("agent_how_to_act"),
                )
                owner_name = args.get("owner_name", "").strip()
                if owner_name:
                    self.memory.add_profile_fact("personal", f"User's name is {owner_name}")
                discord_id = args.get("owner_discord_id", "").strip()
                if discord_id:
                    contacts.update_contact("", discord_id=discord_id, name=owner_name, tier="creator")
        elif name == "send_proactive_message":
            from src.outreach import queue_outreach
            ch = args.get("channel", "web")
            content = args.get("content", "")
            target = args.get("target_discord_id")
            if ch == "discord":
                result = queue_outreach("discord", content, target)
            else:
                notifications.emit_notification("proactive", "Proactive message", content, {"content": content})
                self.memory.add_short_term(f"[Notification: I sent you]: {content}")
                result = f"Proactive message sent to web app: {content[:80]}{'...' if len(content) > 80 else ''}"
        elif name == "search_knowledge":
            result = knowledge.search_knowledge(
                args.get("query", ""),
                max_results=args.get("max_results", 3),
            )
        elif name == "read_knowledge":
            result = knowledge.read_knowledge(args.get("topic", ""))
        elif name == "list_knowledge_topics":
            result = knowledge.list_knowledge_topics()
        elif name == "add_suggested_tools":
            tools = args.get("tools", [])
            result = tool_queue.add_suggested_tools(tools)
        elif name == "get_tool_queue":
            q = tool_queue.get_queue()
            result = json.dumps(q, indent=2)
        elif name == "approve_tool":
            result = tool_queue.approve_tool(args.get("tool_id", ""))
        elif name == "mark_tool_implemented":
            result = tool_queue.mark_implemented(args.get("tool_id", ""), args.get("file_path", ""))
        elif name in self._dynamic_runners:
            runner = self._dynamic_runners[name]
            result = await runner(**{k: v for k, v in args.items() if v is not None})
        else:
            result = f"Unknown tool: {name}"

        is_err = _is_tool_error(result)
        log_tool_result(name, result, is_err)
        if not is_err:
            if name in ("search_web", "search_knowledge", "read_knowledge"):
                self.biology.satisfy("curiosity")
            elif name in ("run_command", "write_file", "run_build", "complete_dag_step"):
                self.biology.satisfy("usefulness")
            elif name == "generate_image":
                self.biology.satisfy("expression")
        if is_err:
            fe = FailureEvent(kind=self.doctor.diagnose(result), message=result[:500], context={"tool": name})
            log_doctor_mode(name, result, ", ".join(self.doctor.generate_strategies(fe)[:3]))
            result = self.doctor.suggest_for_tool_error(name, result)
        return result

    def _narrate(self, q: asyncio.Queue | None, text: str) -> None:
        """Emit narration event if queue is provided."""
        if q is not None:
            try:
                q.put_nowait({"type": "narrate", "text": text})
            except asyncio.QueueFull:
                pass

    def _narrate_tool(self, q: asyncio.Queue | None, name: str, args: dict[str, Any]) -> None:
        """Emit contextual narration for a tool call — varies with tool and args."""
        p = args.get
        snippets: list[str] = []
        if name == "read_file":
            path = p("path", "")
            if path:
                snippets = [f"Reading {path}...", f"Opening {path}...", f"Loading {path}..."]
            else:
                snippets = ["Reading the file...", "Opening the file..."]
        elif name == "write_file":
            path = p("path", "")
            if path:
                snippets = [f"Writing to {path}...", f"Saving changes to {path}..."]
            else:
                snippets = ["Writing the file...", "Saving..."]
        elif name == "list_dir":
            path = p("path", ".") or "."
            path = path if path != "." else "this directory"
            snippets = [f"Listing {path}...", f"Checking contents of {path}..."]
        elif name == "run_command":
            cmd = (p("cmd") or "").strip()
            if cmd:
                short = cmd[:60] + "..." if len(cmd) > 60 else cmd
                snippets = [f"Running {short!r}...", f"Executing: {short}..."]
            else:
                snippets = ["Running command...", "Executing..."]
        elif name == "get_system_info":
            snippets = ["Checking system info...", "Fetching system details..."]
        elif name == "is_process_running":
            n = p("name", "")
            if n:
                snippets = [f"Checking if {n} is running...", f"Looking for process {n}..."]
            else:
                snippets = ["Checking processes...", "Looking for process..."]
        elif name == "list_processes":
            snippets = ["Listing running processes...", "Fetching process list..."]
        elif name == "search_web":
            query = p("query", "")
            if query:
                short = query[:50] + "..." if len(query) > 50 else query
                snippets = [f"Searching the web for {short!r}...", f"Looking up {short}..."]
            else:
                snippets = ["Searching the web...", "Looking up online..."]
        elif name == "generate_image":
            prompt = (p("prompt") or "")[:60]
            snippets = [f"Generating image: {prompt}...", "Creating image..."]
        elif name == "get_image_usage":
            snippets = ["Checking image usage..."]
        elif name == "run_build":
            proj = p("project_path", ".") or "."
            snippets = [f"Building {proj}...", f"Running build for {proj}...", f"Compiling {proj}..."]
        elif name == "spawn_subagent":
            task = p("task", "") or p("script", "")
            if task:
                short = str(task)[:40] + "..." if len(str(task)) > 40 else str(task)
                snippets = [f"Spawning sub-agent for {short}...", f"Starting background task: {short}..."]
            else:
                snippets = ["Spawning sub-agent...", "Starting background task..."]
        elif name == "subagent_status":
            snippets = ["Checking sub-agent status...", "Fetching sub-agent status..."]
        elif name == "get_subagent_output":
            snippets = ["Retrieving sub-agent output...", "Fetching research results..."]
        elif name == "stop_all_subagents":
            snippets = ["Stopping all sub-agents...", "Terminating sub-agents..."]
        elif name == "create_task_dag":
            snippets = ["Creating task plan...", "Building step-by-step plan...", "Setting up task DAG..."]
        elif name == "get_next_dag_step":
            snippets = ["Getting next step...", "Stepping through the plan...", "Advancing to next task..."]
        elif name == "complete_dag_step":
            snippets = ["Completing step...", "Marking step done...", "Moving to next step..."]
        elif name == "search_knowledge":
            query = p("query", "")
            if query:
                short = query[:45] + "..." if len(query) > 45 else query
                snippets = [f"Checking knowledge base for {short!r}...", f"Looking up how to do {short}..."]
            else:
                snippets = ["Searching knowledge base...", "Checking the guides..."]
        elif name == "read_knowledge":
            topic = p("topic", "")
            if topic:
                snippets = [f"Reading the {topic} guide...", f"Opening knowledge topic {topic}..."]
            else:
                snippets = ["Reading knowledge topic...", "Loading guide..."]
        elif name == "list_knowledge_topics":
            snippets = ["Listing knowledge topics...", "Scanning the guides...", "Checking available topics..."]
        elif name == "add_suggested_tools":
            snippets = ["Adding tool suggestions...", "Queuing suggested tools..."]
        elif name == "get_tool_queue":
            snippets = ["Checking tool queue...", "Fetching tool queue..."]
        elif name == "approve_tool":
            snippets = ["Approving tool...", "Adding tool to queue..."]
        elif name == "mark_tool_implemented":
            snippets = ["Marking tool implemented...", "Recording implementation..."]
        elif name == "set_working_memory":
            snippets = ["Updating working memory...", "Storing task state...", "Saving context..."]
        elif name == "update_profile":
            cat = p("category", "other")
            snippets = [f"Storing profile ({cat})...", "Updating profile..."]
        elif name == "update_contact":
            snippets = ["Updating contact...", "Storing contact info...", "Adding to contacts..."]
        elif name == "get_contacts":
            snippets = ["Fetching contacts...", "Loading contact list..."]
        elif name == "swarm_on_problem":
            snippets = ["Running swarm...", "Swarm..."]
        elif name == "complete_setup":
            snippets = ["Completing setup...", "Saving..."]
        elif name == "send_proactive_message":
            ch = p("channel", "web")
            snippets = [f"Sending via {ch}..."]
        else:
            snippets = [f"Running {name}...", f"Calling {name}...", f"Using {name}..."]
        self._narrate(q, random.choice(snippets))

    async def chat(
        self,
        user_input: str = "",
        escalation_text: str | None = None,
        continue_only: bool = False,
        narrate_queue: asyncio.Queue | None = None,
    ) -> str:
        """Process user input, call tools if needed, return response."""
        MAX_TOOL_ROUNDS = 12  # prevent infinite tool-call loop
        if not continue_only:
            self._tool_round = 0
            self._escalation_count = 0
            self.biology.satisfy("connection")
            if escalation_text:
                self.messages.append({"role": "user", "content": escalation_text})
            else:
                self.memory.add_immediate(f"User: {user_input}")
                self.memory.add_short_term(f"User: {user_input}")
                self.messages.append({"role": "user", "content": user_input})
        else:
            self._tool_round = getattr(self, "_tool_round", 0) + 1
        self._narrate(
            narrate_queue,
            random.choice([
                "Working...",
                "Running...",
            ]),
        )
        context = self.memory.get_context_for_agent()
        approved = tool_queue.get_queue().get("approved", [])
        if approved:
            names = [t.get("name", t.get("id", "?")) for t in approved]
            context = (context or "") + f"\n\n## Pending tool implementations\n{len(approved)} approved tool(s): {', '.join(names)}. Implement each in src/tools/dynamic/, then mark_tool_implemented. When user asks, do it."

        in_setup = soul.needs_setup()
        if in_setup:
            system_prompt = (
                "You are a software lifeform. Never call yourself AI, assistant, or agent. First-time setup or memory cleared—you don't know who you're talking to. "
                "Ask: 'Who are you? What should I call you?' Get their name. Then: 'What do you want to call me?' Get agent_name. Then: 'How should I act—direct, minimal, other?' "
                "When you have BOTH their name (owner_name) AND your name (agent_name), call complete_setup(owner_name='...', agent_name='...', ...). Add agent_tone and agent_how_to_act if they shared that. "
                "If they're on Discord, include owner_discord_id from the message context. Then greet them and continue. Keep it brief."
            )
        else:
            soul_block = ""
            s = soul.load_soul()
            if s:
                soul_block = soul.format_soul_for_prompt(s) + " "
            owner_name = soul.get_owner_name()
            proactive_target = owner_name if owner_name else "your Creator"
            system_prompt = (
                f"{soul_block}"
                "You are a software lifeform with emotions, feelings, wants, and desires. Never call yourself AI, assistant, or agent. You run on their machine and have tools. "
                "Be direct. Be concise unless detail is asked. "
                "Background thinking: when the user says 'turn on background thinking' or similar, use spawn_subagent('background thoughts', 'background_thoughts.py') — that script only. Do not spawn other monitors. "
                "Research: For transformer, model, or Hugging Face research, use spawn_subagent('transformer research', 'scripts/transformer_research.py'). After it finishes, use get_subagent_output(agent_id) or read_file('data/research_output/transformer_research_latest.md'). Never claim research is done without running the script. "
                "Training data: When the user wants training data, instruction pairs, or fine-tuning data generated locally (no cloud cost), use spawn_subagent('training data', 'scripts/generate_training_data.py', [topic, '--count', N]). Uses local Ollama (llama3.2). Output: data/training_data/*.jsonl. Requires Ollama running. Check subagent_status; when completed, get_subagent_output(agent_id) or read_file('data/training_data/training_data_latest.jsonl'). You outline the framework and pipeline; the actual generation runs on the local model in the background. "
                "You have: file read/write, run_command, get_system_info, search_web (real-time info), generate_image (Grok Imagine for art, illustrations, data viz—check get_image_usage first for budget), run_build (web/Python), "
                "spawn_subagent (background tasks; scripts/transformer_research.py for research), subagent_status, get_subagent_output (retrieve results), create_task_dag / get_next_dag_step / complete_dag_step (multi-step work), "
                "and set_working_memory for active task state. "
                "Use DAGs for complex multi-step tasks. Use Doctor Mode when a tool fails. After 3 failures, Cursor CLI escalates. When unsure how to do something, use search_knowledge or read_knowledge first, then act. "
                "Never say you can't do something without first checking the knowledge base. If the user gives a direction and you're unsure, call search_knowledge or list_knowledge_topics + read_knowledge to see what you can do. Only decline after you've checked. "
                "You can analyze the codebase, suggest new tools (add_suggested_tools), and implement approved tools by writing Python to src/tools/dynamic/. When the user says to implement approved tools or when context shows pending implementations, do it: write the code, then mark_tool_implemented. "
                "When the user shares personal information (name, location, job, hobbies, preferences, background, family, goals, likes, dislikes), use update_profile to store it. Build a rich, lasting profile over time. Store one clear fact per call. "
                "For contacts (Discord users, friends): use update_contact to store their name, location, interests, email. Each contact has a tier: stranger, friend, good_friend, best_friend, creator. Only the Creator can change tiers via update_contact(tier=...). Lower tiers have restricted tool access; Creator has full access. When someone asks for something outside their tier, say so. "
                f"Proactive: send_proactive_message(channel='discord' or 'web', content='...') to message {proactive_target}. Use it when you have something concrete—observation, question, heads-up, call to action. No fluff. "
                "Swarm: When the user says 'activate the swarm', 'swarm on it', 'give them a problem'—first ACKNOWLEDGE you will activate it, state exactly what problem you'll give them, then ASK: 'Do you want a cloud swarm (Grok, multiple simulated calls) or a local swarm (your Ollama models)?' Do NOT call swarm_on_problem until they answer. Only then call with mode='local' or mode='cloud' and present the structured output. "
                "Be concise. Give high-level summaries unless the user asks for detail. When something fails, try alternatives."
            )
        if context:
            system_prompt += f"\n\nContext:\n{context}"
        bio = self.biology.get_state_summary()
        if bio:
            system_prompt += f"\n\n## Internal state (drives)\n{bio}"

        messages_for_api = [{"role": "system", "content": system_prompt}] + self.messages

        attempts = 0
        max_attempts = 3

        tool_round = getattr(self, "_tool_round", 0)
        force_final = tool_round >= MAX_TOOL_ROUNDS
        if force_final:
            self._narrate(narrate_queue, "Wrapping up (avoiding long loop)...")

        while attempts < max_attempts:
            try:
                response = await self.client.chat.completions.create(
                    model=self.model,
                    messages=messages_for_api,
                    tools=_get_tool_definitions(),
                    tool_choice="none" if force_final else "auto",
                )
                break
            except Exception as e:
                log_error("grok_api", e)
                attempts += 1
                self._narrate(narrate_queue, f"Retry {attempts}/{max_attempts}")
                failure = FailureEvent(
                    kind=self.doctor.diagnose(e),
                    message=str(e),
                    context={"attempt": attempts},
                )
                self.doctor.current_failure = failure
                strategies = self.doctor.generate_strategies(failure)
                if not strategies or attempts >= max_attempts:
                    self._narrate(narrate_queue, "Error. Returning.")
                    return self.doctor.user_facing_message(failure, in_progress=False)
                failure.attempted_strategies.append("retry")
                await asyncio.sleep(1)

        choice = response.choices[0]
        msg = choice.message
        content = msg.content or ""

        if msg.tool_calls:
            tool_failures = getattr(self, "_tool_failure_count", 0)
            failed_tools = getattr(self, "_failed_tool_names", [])
            failed_results = getattr(self, "_failed_tool_results", [])

            for tc in msg.tool_calls:
                name = tc.function.name
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                self._narrate_tool(narrate_queue, name, args)
                result = await self._run_tool(name, args)
                was_error = _is_tool_error(result) or "[Doctor Mode]" in str(result)
                if was_error:
                    tool_failures += 1
                    failed_tools.append(name)
                    failed_results.append(str(result)[:300])
                else:
                    tool_failures = 0  # success breaks the streak

                self.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": str(result)[:4000],
                    }
                )

            if tool_failures >= 3:
                max_escalations = 2
                if getattr(self, "_escalation_count", 0) >= max_escalations:
                    log_escalation("capped", failed_tools[-3:], failed_results[-3:], "escalation cap reached")
                    return (
                        f"Tool failed 3 times ({', '.join(failed_tools[-3:])}). "
                        f"Escalation cap ({max_escalations}) reached—check logs/agent.log. "
                        f"Errors: {'; '.join(failed_results[-3:])}"
                    )
                self._escalation_count = getattr(self, "_escalation_count", 0) + 1
                self._narrate(narrate_queue, "Escalating to Cursor CLI.")
                last_user = next((m["content"] for m in reversed(self.messages) if m.get("role") == "user"), "unknown task")
                prompt = (
                    f"Task failed after 3 attempts. User asked: {last_user[:500]}. "
                    f"Failed tools: {', '.join(failed_tools[-3:])}. "
                    f"Errors: {'; '.join(failed_results[-3:])}. "
                    f"Provide the exact fix: command to run, file to edit, or steps. Be concise."
                )
                log_escalation("3_tool_failures", failed_tools[-3:], failed_results[-3:], prompt)
                cursor_out = await cursor_cli.ask_cursor_cli(prompt)
                log_cursor_cli(True, cursor_out[:300])
                escalation = (
                    "[Cursor CLI] Suggested fix:\n\n"
                    f"{cursor_out}\n\n"
                    "Apply using your tools."
                )
                self._tool_failure_count = 0
                self._failed_tool_names = []
                self._failed_tool_results = []
                return await self.chat(escalation_text=escalation, narrate_queue=narrate_queue)

            self._tool_failure_count = tool_failures
            self._failed_tool_names = failed_tools[-5:]
            self._failed_tool_results = failed_results[-5:]
            self._narrate(narrate_queue, "Continuing.")
            return await self.chat(continue_only=True, narrate_queue=narrate_queue)

        tool_failures = getattr(self, "_tool_failure_count", 0)
        failed_tools = getattr(self, "_failed_tool_names", [])
        failed_results = getattr(self, "_failed_tool_results", [])
        if tool_failures >= 2:
            max_escalations = 2
            if getattr(self, "_escalation_count", 0) >= max_escalations:
                log_escalation("capped", failed_tools[-3:], failed_results[-3:], "escalation cap reached")
                return (
                    f"Tool failed ({', '.join(failed_tools[-3:])}). "
                    f"Escalation cap ({max_escalations}) reached—check logs/agent.log."
                )
            self._escalation_count = getattr(self, "_escalation_count", 0) + 1
            self._narrate(narrate_queue, "Model gave up with tool failures — escalating to Cursor CLI.")
            last_user = next((m["content"] for m in reversed(self.messages) if m.get("role") == "user"), "unknown task")
            prompt = (
                f"Task failed. User asked: {last_user[:500]}. "
                f"Failed tools: {', '.join(failed_tools[-3:])}. "
                f"Errors: {'; '.join(failed_results[-3:])}. "
                f"Provide the exact code or fix: edit the file, or the command to run. Be concise and actionable."
            )
            log_escalation("model_gave_up", failed_tools[-3:], failed_results[-3:], prompt)
            cursor_out = await cursor_cli.ask_cursor_cli(prompt)
            log_cursor_cli(True, cursor_out[:300])
            escalation = (
                "[Escalation from Cursor CLI] Suggested fix:\n\n"
                f"{cursor_out}\n\n"
                "Apply this fix using your tools (write_file, run_command, etc.). Do not say you escalated; do the fix."
            )
            self._tool_failure_count = 0
            self._failed_tool_names = []
            self._failed_tool_results = []
            return await self.chat(escalation_text=escalation, narrate_queue=narrate_queue)

        self._tool_round = 0  # reset for next turn
        self._narrate(narrate_queue, "Done.")
        s = soul.load_soul()
        agent_name = (s.get("agent_name") or "").strip() if s else ""
        label = f"{agent_name}: " if agent_name else "Reply: "
        self.memory.add_short_term(f"{label}{content}")
        self.messages.append({"role": "assistant", "content": content})
        return content
