"""Adapter for Anthropic API using AsyncOpenAI interface."""
import json
from typing import Any

from anthropic import AsyncAnthropic


class MockMessage:
    def __init__(self, content: str, tool_calls: list = None):
        self.content = content
        self.tool_calls = tool_calls or []

class MockChoice:
    def __init__(self, message: MockMessage, finish_reason: str = "stop"):
        self.message = message
        self.finish_reason = finish_reason

class MockUsage:
    def __init__(self, prompt_tokens: int, completion_tokens: int, total_tokens: int):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.total_tokens = total_tokens

class MockCompletion:
    def __init__(self, choices: list[MockChoice], usage: MockUsage, id: str):
        self.choices = choices
        self.usage = usage
        self.id = id


class AsyncAnthropicAdapter:
    """A wrapper around AsyncAnthropic that mocks the AsyncOpenAI interface.
    
    Designed specifically to support text-based tool calling (supports_tools=False)
    to avoid complex translation of tool schemas and tool_result blocks.
    """
    
    def __init__(self, api_key: str, base_url: str):
        # We strip trailing /v1 because anthropic sdk adds it or expects base url without it
        clean_url = base_url.replace("/v1", "").strip() if base_url else None
        self._client = AsyncAnthropic(api_key=api_key, base_url=clean_url)
        self.chat = self.Chat(self._client)

    class Chat:
        def __init__(self, client):
            self.completions = self.Completions(client)

        class Completions:
            def __init__(self, client):
                self._client = client

            async def create(self, model: str, messages: list[dict], **kwargs):
                system_prompt = ""
                anthropic_messages = []
                
                # Separate system prompt and convert messages
                for msg in messages:
                    role = msg.get("role")
                    content = msg.get("content", "")
                    
                    if role == "system":
                        system_prompt += content + "\n\n"
                    elif role in ("user", "assistant"):
                        # Anthropic doesn't allow empty content
                        if not content:
                            content = " "
                        anthropic_messages.append({"role": role, "content": content})
                    elif role == "tool":
                        # We are using supports_tools=False, so this shouldn't happen natively.
                        # But if it does, we inject it as a user message.
                        anthropic_messages.append({"role": "user", "content": f"Tool result: {content}"})
                
                # Consolidate consecutive messages of the same role (Anthropic requirement)
                consolidated = []
                for msg in anthropic_messages:
                    if consolidated and consolidated[-1]["role"] == msg["role"]:
                        consolidated[-1]["content"] += "\n\n" + msg["content"]
                    else:
                        consolidated.append(msg)
                        
                # Ensure first message is 'user'
                if consolidated and consolidated[0]["role"] == "assistant":
                    consolidated.insert(0, {"role": "user", "content": " "})

                create_kwargs = {
                    "model": model,
                    "max_tokens": kwargs.get("max_tokens") or kwargs.get("max_completion_tokens") or 4096,
                    "system": system_prompt.strip(),
                    "messages": consolidated,
                }
                
                if "temperature" in kwargs:
                    create_kwargs["temperature"] = kwargs["temperature"]
                
                response = await self._client.messages.create(**create_kwargs)
                
                # Extract text
                output_text = ""
                for block in response.content:
                    if block.type == "text":
                        output_text += block.text
                
                # Build mock OpenAI completion
                msg_obj = MockMessage(content=output_text)
                choice = MockChoice(message=msg_obj, finish_reason=response.stop_reason or "stop")
                
                usage = MockUsage(
                    prompt_tokens=response.usage.input_tokens,
                    completion_tokens=response.usage.output_tokens,
                    total_tokens=response.usage.input_tokens + response.usage.output_tokens,
                )
                
                return MockCompletion(choices=[choice], usage=usage, id=response.id)
