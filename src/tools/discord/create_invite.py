#!/usr/bin/env python3
"""
Tool: Create a single-use Discord invite link with expiry for a specified guild/channel.
"""

import discord
from discord.ext import commands
import os
from typing import Optional

async def create_discord_invite(
    guild_id: int,
    channel_id: Optional[int] = None,
    max_uses: int = 1,
    max_age: int = 3600,  # in seconds
    reason: Optional[str] = None,
) -> str:
    """
    Creates a new Discord invite link in the specified guild/channel.
    Args:
        guild_id (int): The ID of the guild (server) where the invite will be created.
        channel_id (Optional[int]): The ID of the channel to create the invite for. Defaults to None for system channel or first text channel.
        max_uses (int): Maximum number of uses for the invite link.
        max_age (int): Maximum time in seconds before the invite expires.
        reason (Optional[str]): Optional audit log reason.
    Returns:
        str: The generated invite URL or an error message.
    """
    try:
        intents = discord.Intents.default()
        client = commands.Bot(intents=intents, command_prefix="!")

        @client.event
        async def on_ready():
            guild = client.get_guild(guild_id)
            if not guild:
                print(f"Error: Guild with ID {guild_id} not found.")
                await client.close()
                return

            target_channel = None
            if channel_id:
                target_channel = guild.get_channel(channel_id)
            if not target_channel:
                # Fallback: first text channel
                for ch in guild.text_channels:
                    target_channel = ch
                    break
            if not target_channel:
                print(f"Error: No suitable text channel found in guild {guild.name}.")
                await client.close()
                return

            invite = await target_channel.create_invite(max_uses=max_uses, max_age=max_age, reason=reason)
            print(f"Invite created: {invite.url}")
            await client.close()

        token = os.getenv("DISCORD_BOT_TOKEN")
        if not token:
            print("Error: DISCORD_BOT_TOKEN environment variable not set.")
            return

        await client.start(token)

    except Exception as e:
        print(f"Error creating invite: {str(e)}")


if __name__ == "__main__":
    import sys
    import asyncio

    if len(sys.argv) < 2:
        print("Usage: create_invite.py <guild_id> [channel_id] [max_uses] [max_age_secs]")
        sys.exit(1)

    guild_id = int(sys.argv[1])
    channel_id = int(sys.argv[2]) if len(sys.argv) > 2 else None
    max_uses = int(sys.argv[3]) if len(sys.argv) > 3 else 1
    max_age = int(sys.argv[4]) if len(sys.argv) > 4 else 3600

    asyncio.run(create_discord_invite(guild_id, channel_id, max_uses, max_age))
