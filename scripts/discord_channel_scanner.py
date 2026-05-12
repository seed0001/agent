#!/usr/bin/env python3
"""
Background sub-agent: Discord Channel Scanner & Analyzer
Picks a channel, reads messages, analyzes, and writes a report.
Run via spawn_subagent.
"""

import argparse
import os
import json
from datetime import datetime
from collections import Counter
import discord
from discord.ext import commands

def analyze_messages(messages):
    if not messages:
        return {"error": "No messages found"}

    authors = Counter(m["author"] for m in messages)
    timestamps = [m["timestamp"] for m in messages]
    word_count = sum(len(m["content"].split()) for m in messages if m["content"])
    top_authors = authors.most_common(5)

    # Simple topic keywords
    keywords = ["ai", "build", "code", "project", "discord", "model", "server", "tool"]
    topic_hits = {}
    for kw in keywords:
        hits = sum(1 for m in messages if kw.lower() in m["content"].lower())
        if hits:
            topic_hits[kw] = hits

    return {
        "message_count": len(messages),
        "unique_authors": len(authors),
        "top_authors": top_authors,
        "word_count": word_count,
        "time_range": f"{timestamps[-1]} to {timestamps[0]}" if timestamps else "",
        "top_topics": topic_hits,
        "sample_messages": [m["content"][:120] for m in messages[:3]]
    }

async def scan_channel(guild_id: int, channel_id: int, limit: int, output_path: str):
    intents = discord.Intents.default()
    intents.message_content = True
    client = commands.Bot(intents=intents, command_prefix="!")

    results = []

    @client.event
    async def on_ready():
        nonlocal results
        try:
            guild = client.get_guild(guild_id)
            if not guild:
                results = [{"error": f"Guild {guild_id} not found"}]
                await client.close()
                return

            channel = guild.get_channel(channel_id)
            if not channel:
                results = [{"error": f"Channel {channel_id} not found"}]
                await client.close()
                return

            messages = []
            async for msg in channel.history(limit=limit):
                messages.append({
                    "id": msg.id,
                    "author": str(msg.author),
                    "content": msg.content,
                    "timestamp": str(msg.created_at)
                })

            analysis = analyze_messages(messages)

            report = {
                "guild_id": guild_id,
                "guild_name": guild.name,
                "channel_id": channel_id,
                "channel_name": channel.name,
                "scanned_at": datetime.now().isoformat(),
                "analysis": analysis,
                "messages": messages[:10]  # keep sample
            }

            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            with open(output_path, "w") as f:
                json.dump(report, f, indent=2)

            results = [{"status": "completed", "output": output_path, "messages_analyzed": len(messages)}]
        except Exception as e:
            results = [{"error": str(e)}]
        finally:
            await client.close()

    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        return [{"error": "DISCORD_BOT_TOKEN not set"}]

    await client.start(token)
    return results

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--guild", type=int, required=True)
    parser.add_argument("--channel", type=int, required=True)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--output", type=str, default="data/scans/scan_report.json")
    args = parser.parse_args()

    import asyncio
    result = asyncio.run(scan_channel(args.guild, args.channel, args.limit, args.output))
    print(json.dumps(result, indent=2))