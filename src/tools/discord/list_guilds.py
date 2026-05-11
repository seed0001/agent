#!/usr/bin/env python3
"""
Tool: List connected Discord guilds with full, untruncated guild IDs.
This fixes the truncation issue in list_connected_channels.
"""

import discord
from discord.ext import commands
import os
from typing import List, Dict

async def list_full_guilds() -> List[Dict]:
    """
    Returns a list of connected guilds with complete guild IDs and basic info.
    """
    try:
        intents = discord.Intents.default()
        client = commands.Bot(intents=intents, command_prefix="!")

        guilds_data = []

        @client.event
        async def on_ready():
            for guild in client.guilds:
                guilds_data.append({
                    "guild_id": str(guild.id),  # Full ID as string, no truncation
                    "guild_name": guild.name,
                    "member_count": guild.member_count,
                    "channel_count": len(guild.channels)
                })
            await client.close()

        token = os.getenv("DISCORD_BOT_TOKEN")
        if not token:
            return [{"error": "DISCORD_BOT_TOKEN not set"}]

        await client.start(token)
        return guilds_data

    except Exception as e:
        return [{"error": str(e)}]