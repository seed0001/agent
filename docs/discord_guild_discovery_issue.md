# Discord Guild Discovery Issue

**Date:** 2026-05-11  
**Reporter:** Travis  
**Tool:** list_connected_channels (no parameters)  

## Observed Behavior
Every execution of the list_connected_channels tool returns **only one guild**:
- Guild Name: Fusion AI
- Guild ID: 1387520068367159368
- Channels: 45 (full list includes categories like Information, Text Channels, Voice Channels, Travis builder, Chris builder, etc., and various text/voice channels)

No other guilds (e.g., AI.Engineer, Good Vibes, or others the bot is reportedly connected to) appear in the output.

## Context from Conversation
- Coder previously stated that "Good Vibes" was hard-coded into the tool or connection logic.
- Multiple fresh tool calls (including immediate re-runs) produce identical limited output.
- The bot is expected to be connected to multiple Discord servers, but the discovery mechanism is not surfacing them.
- User instruction: Do not rely on prior memory or assumptions; re-execute the tool and report raw output each time.

## Impact
This prevents full server control, channel scanning across all guilds, and management of other communities as requested. The tool is not returning the complete set of connected guilds despite the bot's reported multi-server presence.

## Recommended Fix
- Investigate the underlying Discord client connection and guild caching/fetching code.
- Ensure list_connected_channels queries all available guilds without hard-coded filters or incomplete session state.
- Add explicit guild_id parameter support or a force-rescan option if needed.
- Verify bot invites and permissions are active across all target servers.

Provide the updated tool code or connection handler for review once resolved.