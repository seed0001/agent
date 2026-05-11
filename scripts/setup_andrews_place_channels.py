#!/usr/bin/env python3
"""
Create/verify the minimal Andrew's Place help-desk channels.
Direct Discord script used because the public tool output truncated the guild id.
Does not print secrets.
"""

import asyncio
import os
import sys
import discord

TARGET_GUILD_NAME = "Andrew’s Place"
ALT_TARGET_GUILD_NAME = "Andrew's Place"
TARGET_PREFIX = "150310195"

CHANNELS = [
    ("welcome", "Welcome to Andrew’s Place — the official help desk and onboarding server for the Andrew framework."),
    ("announcements", "Framework updates, releases, and important notices. Read-only for most users."),
    ("help-desk", "Post questions about installation, setup, bugs, or using the Andrew framework here."),
    ("general", "Friendly chat and casual discussion. Keep it respectful and community-oriented."),
    ("resources", "GitHub links, docs, setup guides, and common answers. Check here first."),
]


def norm_name(name: str) -> str:
    return name.replace("’", "'").strip().lower()


async def main() -> int:
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        print("ERROR: DISCORD_BOT_TOKEN is missing")
        return 2

    intents = discord.Intents.default()
    client = discord.Client(intents=intents)
    done = asyncio.Event()
    result_code = 1

    @client.event
    async def on_ready():
        nonlocal result_code
        try:
            print("VISIBLE_GUILDS:")
            for guild in client.guilds:
                print(f"- {guild.id} | {guild.name} | channels={len(guild.channels)}")

            target = None
            for guild in client.guilds:
                n = norm_name(guild.name)
                if n == norm_name(TARGET_GUILD_NAME) or n == norm_name(ALT_TARGET_GUILD_NAME):
                    target = guild
                    break
            if target is None:
                for guild in client.guilds:
                    if str(guild.id).startswith(TARGET_PREFIX):
                        target = guild
                        break

            if target is None:
                print("ERROR: Andrew's Place guild not found among visible guilds")
                result_code = 3
                return

            print(f"TARGET_GUILD: {target.id} | {target.name}")

            me = target.me or target.get_member(client.user.id)
            if me is not None:
                perms = me.guild_permissions
                print(f"BOT_PERMISSIONS: manage_channels={perms.manage_channels}, administrator={perms.administrator}")
                if not (perms.manage_channels or perms.administrator):
                    print("ERROR: Bot lacks Manage Channels/Admin permission in target guild")
                    result_code = 4
                    return

            existing = {ch.name: ch for ch in target.text_channels}
            created = []
            reused = []
            for name, topic in CHANNELS:
                if name in existing:
                    ch = existing[name]
                    reused.append(f"{name}:{ch.id}")
                    if getattr(ch, "topic", None) != topic:
                        try:
                            await ch.edit(topic=topic, reason="Andrew help-desk channel topic verification")
                        except Exception as e:
                            print(f"WARN: could not update topic for {name}: {type(e).__name__}: {e}")
                    continue
                ch = await target.create_text_channel(name=name, topic=topic, reason="Andrew's Place minimal help-desk setup")
                created.append(f"{name}:{ch.id}")

            print("CREATED_CHANNELS:", ", ".join(created) if created else "none")
            print("EXISTING_CHANNELS:", ", ".join(reused) if reused else "none")
            print("FINAL_TEXT_CHANNELS:")
            for ch in sorted(target.text_channels, key=lambda c: c.position):
                print(f"- {ch.id} | #{ch.name} | topic={ch.topic or ''}")
            result_code = 0
        except Exception as e:
            print(f"ERROR: {type(e).__name__}: {e}")
            result_code = 5
        finally:
            done.set()
            await client.close()

    try:
        await client.start(token)
    finally:
        return result_code


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
