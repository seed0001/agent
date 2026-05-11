#!/usr/bin/env python3
"""
Tool: Create a Discord channel in a specified guild.
"""

import discord
from discord.ext import commands
import os
from typing import Optional

async def create_discord_channel(
    guild_id: int,
    channel_name: str,
    channel_type: str = "text",
    category_id: Optional[int] = None,
    reason: Optional[str] = None,
) -> str:
    """
    Creates a new Discord channel in the specified guild.
    
    Args:
        guild_id (int): The ID of the guild (server) where the channel will be created.
        channel_name (str): The name of the new channel.
        channel_type (str): The type of channel ("text" or "voice"). Defaults to "text".
        category_id (Optional[int]): The ID of the category to create the channel under. Defaults to None.
        reason (Optional[str]): The reason for creating the channel. Defaults to None.
        
    Returns:
        str: A message confirming the channel creation or an error message.
    """
    try:
        # Initialize the Discord client
        intents = discord.Intents.default()
        client = commands.Bot(intents=intents, command_prefix="!")

        @client.event
        async def on_ready():
            guild = client.get_guild(guild_id)
            if not guild:
                return f"Error: Guild with ID {guild_id} not found."

            # Determine the channel type
            channel_type_enum = (
                discord.ChannelType.text
                if channel_type.lower() == "text"
                else discord.ChannelType.voice
            )

            # Create the channel
            if category_id:
                category = guild.get_channel(category_id)
                if not category:
                    return f"Error: Category with ID {category_id} not found."
                channel = await guild.create_text_channel(
                    name=channel_name, 
                    category=category, 
                    reason=reason
                )
            else:
                channel = await guild.create_text_channel(
                    name=channel_name, 
                    reason=reason
                )

            return f"Channel '{channel_name}' created successfully in guild '{guild.name}'."

        # Start the client
        token = os.getenv("DISCORD_BOT_TOKEN")
        if not token:
            return "Error: DISCORD_BOT_TOKEN environment variable not set."

        await client.start(token)

    except Exception as e:
        return f"Error creating channel: {str(e)}"
