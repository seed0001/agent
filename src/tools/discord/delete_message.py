#!/usr/bin/env python3
"""
Tool: Delete a Discord message by ID.
"""

import discord
from discord.ext import commands
import os
from typing import Optional

async def delete_discord_message(
    channel_id: int,
    message_id: int,
    reason: Optional[str] = None,
) -> str:
    """
    Deletes a specific Discord message.
    
    Args:
        channel_id (int): The ID of the channel containing the message.
        message_id (int): The ID of the message to delete.
        reason (Optional[str]): Audit log reason. Defaults to None.
        
    Returns:
        str: Confirmation or error message.
    """
    try:
        intents = discord.Intents.default()
        client = commands.Bot(intents=intents, command_prefix="!")

        @client.event
        async def on_ready():
            channel = client.get_channel(channel_id)
            if not channel:
                return f"Error: Channel with ID {channel_id} not found."

            try:
                message = await channel.fetch_message(message_id)
                await message.delete(reason=reason)
                return f"Message {message_id} deleted successfully from channel {channel_id}."
            except discord.NotFound:
                return f"Error: Message {message_id} not found."
            except discord.Forbidden:
                return "Error: Missing permissions to delete the message."

        token = os.getenv("DISCORD_BOT_TOKEN")
        if not token:
            return "Error: DISCORD_BOT_TOKEN environment variable not set."

        await client.start(token)

    except Exception as e:
        return f"Error deleting message: {str(e)}"