# Issue Summary: Silent Tool Failures

## The Problem
Previously, when the system executed a tool (e.g., reading a file, searching the web), if that tool failed or threw an exception, the system often failed silently or logged an error internally without injecting feedback back into the context. This left the Language Model (the "agent") waiting for a response that never arrived, or completely unaware of the failure, breaking the back-and-forth flow and causing confusion.

## Desired Behavior
We want a uniform mechanism that guarantees every tool call provides feedback, even on failure. Specifically:
1. **Automatic Error Injection**: Every tool call failure must inject a system-level error message back into the conversation context.
2. **Context-Rich Context**: The injected error message must include:
   - A persona header ("Andrew, direct and honest") to maintain voice consistency.
   - The original user message that triggered the action.
   - The tool name and arguments.
   - The exact error details.
   - A snippet of the recent chat context (the last 2-3 messages).

## Implementation Plan
To achieve this, we took the following steps:
1. **Created `tool_monitor.py`**: Added a new wrapper function `run_with_monitoring` in `src/tools/dynamic/tool_monitor.py`. This function takes the tool executor, tool name, arguments, recent messages, and original user input. It wraps the execution in a `try...except` block and formats the required context-rich error message on failure.
2. **Integrated into Main Execution**: Updated `src/agent/core.py` to use `run_with_monitoring` for all tool executions (`_run_tool` calls). This ensures all tools, regardless of type, pass through the monitor.
3. **Verification**: Ran the test suite and confirmed that failing tools now properly generate the formatted system message without breaking the agent loop.
