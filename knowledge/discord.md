# Discord Integration

## Overview

A Discord bot receives messages and sends responses. When someone messages the bot, the owner gets notified on desktop and in the web app.

## Setup

1. Create a Discord application at https://discord.com/developers/applications
2. Add a bot, copy the token
3. Enable Message Content Intent
4. Add to `.env`:
   - `DISCORD_BOT_TOKEN` – bot token
   - `DISCORD_OWNER_ID` – your Discord user ID (for proactive DMs). Right‑click your name → Copy User ID (developer mode must be on)

## Behavior

- **DMs**: Bot responds to all DMs
- **Servers**: Bot responds only when @mentioned
- **Notifications**: Every message triggers a desktop notification and an in‑app toast

## Proactive outreach

Proactive: `send_proactive_message(channel="discord", content="...")` or `channel="web"`. Use when you have something concrete—observation, question, heads-up, call to action. No fluff.

## Direct Discord tools

- `send_discord_message(content="...", target_user_id="...")` sends a direct DM (bypasses proactive policy limits).
- Tier note: direct send is available for `best_friend` and `creator` contexts.
- `post_to_channel(channel_id="...", content="...")` posts directly to any channel ID the bot can access.
- `list_connected_channels(guild_id="optional")` returns connected guilds and visible channels so you can discover targets without manual config.
- `send_discord_attachment(file_path="...", target_channel_id="...", content="optional note")` uploads local files as Discord attachments with direct send semantics.
