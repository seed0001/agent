#!/usr/bin/env python3
"""
Tool: Full Discord server control for scanning, analyzing, and managing messages.
Provides complete access to list channels, fetch messages, search, and delete.
"""

import discord
from discord.ext import commands
import os
from typing import List, Optional, Dict

async def scan_discord_channel(
    channel_id: int,
    limit: int = 100,
    search_text: Optional[str] = None,
) -> List[Dict]:
    """
    Scans a channel and returns recent messages (optionally filtered by text).
    """
    try:
        intents = discord.Intents.default()
        intents.message_content = True
        client = commands.Bot(intents=intents, command_prefix="!")

        @client.event
        async def on_ready():
            channel = client.get_channel(channel_id)
            if not channel:
                return [{"error": f"Channel {channel_id} not found"}]

            messages = []
            async for msg in channel.history(limit=limit):
                if search_text and search_text.lower() not in msg.content.lower():
                    continue
                messages.append({
                    "id": msg.id,
                    "author": str(msg.author),
                    "content": msg.content,
                    "timestamp": str(msg.created_at),
                    "channel_id": channel_id
                })
            return messages

        token = os.getenv("DISCORD_BOT_TOKEN")
        if not token:
            return [{"error": "DISCORD_BOT_TOKEN not set"}]

        await client.start(token)

    except Exception as e:
        return [{"error": str(e)}]


async def delete_discord_message_full(
    channel_id: int,
    message_id: int,
    reason: Optional[str] = None,
) -> str:
    """Deletes a single message (reuses logic from delete_message.py)."""
    # Same implementation as the dedicated delete tool for consistency
    try:
        intents = discord.Intents.default()
        client = commands.Bot(intents=intents, command_prefix="!")

        @client.event
        async def on_ready():
            channel = client.get_channel(channel_id)
            if not channel:
                return f"Error: Channel {channel_id} not found."
            try:
                message = await channel.fetch_message(message_id)
                await message.delete(reason=reason)
                return f"Deleted message {message_id}."
            except Exception as e:
                return f"Delete failed: {str(e)}"

        token = os.getenv("DISCORD_BOT_TOKEN")
        if not token:
            return "Token missing."

        await client.start(token)

    except Exception as e:
        return f"Error: {str(e)}"


async def list_all_channels(guild_id: int) -> List[Dict]:
    """Returns all channels in the guild for full server overview."""
    # Similar structure to list_guilds + channel listing
    try:
        intents = discord.Intents.default()
        client = commands.Bot(intents=intents, command_prefix="!")

        @client.event
        async def on_ready():
            guild = client.get_guild(guild_id)
            if not guild:
                return [{"error": "Guild not found"}]
            channels = [{"id": c.id, "name": c.name, "type": str(c.type)} for c in guild.channels]
            return channels

        token = os.getenv("DISCORD_BOT_TOKEN")
        if not token:
            return [{"error": "Token missing"}]

        await client.start(token)

    except Exception as e:
        return [{"error": str(e)}]