# Proactive Outreach

Andrew can initiate contact without a direct prompt, but every proactive send
goes through `src.proactive_outreach`.

## What Triggers It

- Background thinking runs when biology drives say connection or expression is
  high enough.
- `background_thoughts.run_once()` generates a concrete first-person message.
- If the quality filter accepts it and the Creator has not been active in the
  last 30 minutes, it calls `maybe_queue_proactive_outreach(...)`.
- The normal `send_proactive_message` tool also goes through the same policy.

## Policy

Config lives at `data/profiles/default/proactive_outreach.json`.

Defaults:

- `enabled=true`
- `allowed_tiers=["good_friend", "best_friend", "creator"]`
- `max_per_contact_per_day=2`
- `cooldown_minutes=180`
- `preferred_channel="discord"`
- `fallback_to_creator_web=true`

Contacts marked `do_not_contact=true`, contacts with notes containing "do not
contact", blocked contact IDs, and tiers outside `allowed_tiers` are blocked.

## Recipient Selection

When no explicit Discord target is supplied, Andrew scores eligible contacts by:

- tier priority (`creator` > `best_friend` > `good_friend`)
- overlap between the proactive message and contact interests/notes/profile
- most recently updated contact as a tie-breaker

If no eligible contact is available and Creator fallback is enabled, Andrew sends
the message as a web notification for the Creator.

## Logging

Every queued or blocked decision is written as JSONL to:

`C:\Users\aztre\Desktop\agent\andrew's projects\journal\outreach_log.txt`

Entries include timestamp, status, recipient, tier, channel, trigger reason,
message, and block/queue reason. This is the Creator audit trail.

## Tools

### Autonomous Proactive Outreach (subject to caps/cooldowns)

- `send_proactive_message(channel, content, target_discord_id?)`
  - For Andrew's own ideas, observations, thoughts.
  - Subject to daily caps, cooldowns, tier restrictions.
  - Use when Andrew decides to reach out on his own.
- `get_proactive_outreach_status()`
  - Shows settings, send counters, cooldown state, journal path, and recent log
    entries.
- `configure_proactive_outreach(...)`
  - Creator oversight tool for enabling/disabling outreach and changing limits,
    allowed tiers, blocked contacts, channel, and fallback behavior.

### Direct Discord Messaging (bypasses caps)

- `send_discord_message(content, target_user_id?, target_channel_id?)`
  - For Creator-directed sends: when Travis tells Andrew to send a message.
  - Bypasses daily caps, cooldowns, and proactive policy restrictions.
  - Can target a specific user (DM) or a channel (post in server).
  - Use when the Creator explicitly instructs Andrew to send something.

**Rule:** If the Creator says "send X to Y" or "post this in channel Z", use
`send_discord_message`. If Andrew decides on his own to reach out, use
`send_proactive_message`.
