import traceback

async def run_with_monitoring(
    tool_runner,
    tool_name: str,
    tool_args: dict,
    recent_messages: list,
    user_input: str
) -> str:
    """
    Wraps a tool call to automatically inject context-rich error messages on failure.

    Args:
        tool_runner: An async callable that executes the tool (e.g. self._run_tool).
        tool_name: The name of the tool being called.
        tool_args: The arguments passed to the tool.
        recent_messages: The most recent messages from the chat context.
        user_input: The original user message that triggered the tool call.
    """
    try:
        result = await tool_runner(tool_name, tool_args)
        return result
    except Exception as e:
        error_details = str(e)

        # Format recent chat snippet (last 3 messages)
        chat_snippet = ""
        for msg in recent_messages[-3:]:
            role = msg.get("role", "unknown")
            # Handle different message structures, including content which might be a list or None
            content = msg.get("content", "")
            if isinstance(content, list):
                content_str = " ".join([c.get("text", "") for c in content if c.get("type") == "text"])
            else:
                content_str = str(content or "")

            if content_str:
                chat_snippet += f"[{role}]: {content_str[:200]}\n"

        error_message = (
            "[SYSTEM: Tool Execution Failed]\n"
            "Persona Context: Andrew, direct and honest\n\n"
            f"Original User Message: {user_input}\n\n"
            f"Tool Name: {tool_name}\n"
            f"Arguments: {tool_args}\n\n"
            f"Error Details: {error_details}\n\n"
            "Recent Chat Snippet:\n"
            f"{chat_snippet}"
        )
        return error_message
