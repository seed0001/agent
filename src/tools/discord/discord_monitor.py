#!/usr/bin/env python3
"""
Tool: Monitor Discord messages in a specified channel.
"""

import discord
from discord.ext import commands
import os
from typing import List, Dict, Any

async def monitor_discord_channel(
    channel_id: int,
    guild_id: int,
    callback_url: Optional[str] = None
) -> str:
    """
    Monitors messages in a specified Discord channel and optionally sends them to a callback URL.
    
    Args:
        channel_id (int): The ID of the channel to monitor.
        guild_id (int): The ID of the guild (server) where the channel is located.
        callback_url (Optional[str]): A URL to send message data to. Defaults to None.
        
    Returns:
        str: A message confirming the monitoring has started or an error message.
    """
    try:
        # Initialize the Discord client
        intents = discord.Intents.default()
        intents.messages = True
        client = commands.Bot(intents=intents, command_prefix="!")

        @client.event
        async def on_ready():
            print(f"Monitoring channel {channel_id} in guild {guild_id}")

        @client.event
        async def on_message(message):
            if message.channel.id == channel_id:
                message_data = {
                    "author": message.author.name,
                    "content": message.content,
                    "timestamp": message.created_at.isoformat(),
                    "channel_id": message.channel.id
                }
                print(f"New message: {message_data}")
                
                # If a callback URL is provided, send the message data to it
                if callback_url:
                    # Placeholder for HTTP request logic
                    # In a real implementation, you would use a library like `requests` or `aiohttp`
                    print(f"Sending message data to callback URL: {callback_url}")

        # Start the client
        token = os.getenv("DISCORD_BOT_TOKEN")
        if not token:
            return "Error: DISCORD_BOT_TOKEN environment variable not set."

        await client.start(token)

    except Exception as e:
        return f"Error monitoring channel: {str(e)}"
