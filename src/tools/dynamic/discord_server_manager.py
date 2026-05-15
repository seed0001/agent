"""
discord_server_manager.py — Discord server management dynamic tool for Andrew.

Consolidates the old one-off scripts from the previous agent folder into one
inspectable tool surface. Uses a short-lived discord.py client for each action.

Default guild is Good Vibes unless guild_id is provided.
Destructive actions require confirm=True.
"""
from __future__ import annotations

import asyncio
import os
from typing import Any, Optional

try:
    import discord  # type: ignore
    from dotenv import load_dotenv  # type: ignore
    _IMPORT_OK = True
    _IMPORT_ERR = ""
except Exception as e:  # pragma: no cover
    discord = None  # type: ignore
    load_dotenv = None  # type: ignore
    _IMPORT_OK = False
    _IMPORT_ERR = str(e)

DEFAULT_GUILD_ID = 1469648303862841376  # Good Vibes

TOOL_DEF = {
    "name": "manage_discord_server",
    "description": (
        "Manage Discord server structure for the Creator: list visible guilds, roles/members, "
        "create/edit/delete channels and categories, create/edit/delete roles, "
        "assign/remove roles, set channel permissions, and kick members. "
        "Destructive actions require confirm=true. Per-guild actions default to Good Vibes guild."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "list_guilds",
                    "list_roles", "list_members", "list_channels",
                    "create_channel", "edit_channel", "delete_channel",
                    "create_category", "delete_category",
                    "create_role", "edit_role", "delete_role",
                    "assign_role", "remove_role", "set_permissions",
                    "kick_member",
                ],
                "description": "Discord management action to perform.",
            },
            "guild_id": {"type": "string", "description": "Discord guild/server ID. Defaults to Good Vibes for per-guild actions."},
            "channel_id": {"type": "string", "description": "Target channel ID."},
            "channel_name": {"type": "string", "description": "Channel name for create/edit/find."},
            "channel_type": {"type": "string", "enum": ["text", "voice"], "description": "Channel type for create_channel. Default text."},
            "category_id": {"type": "string", "description": "Category ID for channel placement or target category."},
            "category_name": {"type": "string", "description": "Category name for create/find."},
            "topic": {"type": "string", "description": "Text channel topic."},
            "role_id": {"type": "string", "description": "Target role ID."},
            "role_name": {"type": "string", "description": "Role name for create/edit/find."},
            "color": {"type": "string", "description": "Role color hex like #FF0000."},
            "hoist": {"type": "boolean", "description": "Whether role is displayed separately."},
            "user_id": {"type": "string", "description": "Target Discord user/member ID."},
            "target": {"type": "string", "description": "Fallback target ID or name for member/channel/role lookup."},
            "allow_perms": {"type": "string", "description": "Comma permissions to allow: view,send,manage,connect,speak. Use none for no allow."},
            "deny_perms": {"type": "string", "description": "Comma permissions to deny: view,send,manage,connect,speak. Use none for no deny."},
            "reason": {"type": "string", "description": "Audit-log reason."},
            "confirm": {"type": "boolean", "description": "Required true for destructive actions: delete/kick."},
        },
        "required": ["action"],
    },
}


def _as_int(value: Any, name: str, required: bool = False) -> Optional[int]:
    if value in (None, ""):
        if required:
            raise ValueError(f"Missing required {name}")
        return None
    try:
        return int(str(value).strip())
    except Exception:
        raise ValueError(f"Invalid {name}: {value!r}")


def _truth(value: Any) -> Optional[bool]:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    s = str(value).strip().lower()
    if s in {"true", "1", "yes", "y", "on"}:
        return True
    if s in {"false", "0", "no", "n", "off"}:
        return False
    return None


def _apply_perm_list(overwrite: Any, value: Optional[str], state: bool) -> None:
    if not value or value.lower().strip() == "none":
        return
    perms = {p.strip().lower() for p in value.split(",") if p.strip()}
    if "view" in perms or "read" in perms:
        overwrite.view_channel = state
    if "send" in perms or "write" in perms:
        overwrite.send_messages = state
    if "manage" in perms:
        overwrite.manage_channels = state
    if "connect" in perms:
        overwrite.connect = state
    if "speak" in perms:
        overwrite.speak = state


def _find_channel(guild: Any, channel_id: Optional[int], name: Optional[str], target: Optional[str] = None) -> Any:
    if channel_id:
        return guild.get_channel(channel_id)
    needle = (name or target or "").strip().lower()
    if not needle:
        return None
    for ch in guild.channels:
        if ch.name.lower() == needle or str(ch.id) == needle:
            return ch
    return None


def _find_role(guild: Any, role_id: Optional[int], name: Optional[str], target: Optional[str] = None) -> Any:
    if role_id:
        return guild.get_role(role_id)
    needle = (name or target or "").strip().lower()
    if not needle:
        return None
    for role in guild.roles:
        if role.name.lower() == needle or str(role.id) == needle:
            return role
    return None


def _find_member(guild: Any, user_id: Optional[int], target: Optional[str] = None) -> Any:
    if user_id:
        m = guild.get_member(user_id)
        if m:
            return m
    needle = (target or "").strip().lower()
    if not needle:
        return None
    for m in guild.members:
        names = {str(m.id).lower(), m.name.lower(), str(m).lower()}
        if getattr(m, "nick", None):
            names.add(m.nick.lower())
        if needle in names:
            return m
    return None


async def _run_discord_action(**kwargs: Any) -> str:
    if not _IMPORT_OK:
        return f"Discord manager unavailable: {_IMPORT_ERR}. Install discord.py and python-dotenv."

    load_dotenv()
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        return "Discord manager error: DISCORD_BOT_TOKEN is not set."

    action = kwargs.get("action")
    if action == "list_guilds":
        guild_id = None
    else:
        guild_id = _as_int(kwargs.get("guild_id") or DEFAULT_GUILD_ID, "guild_id", True)
    confirm = bool(kwargs.get("confirm", False))

    destructive = {"delete_channel", "delete_category", "delete_role", "kick_member"}
    if action in destructive and not confirm:
        return f"Refusing destructive action '{action}' without confirm=true."

    intents = discord.Intents.default()
    intents.message_content = True
    intents.members = True
    intents.guilds = True
    client = discord.Client(intents=intents)
    done: asyncio.Future[str] = asyncio.get_running_loop().create_future()

    async def finish(msg: str) -> None:
        if not done.done():
            done.set_result(msg)
        await client.close()

    @client.event
    async def on_ready():  # type: ignore
        try:
            if action == "list_guilds":
                lines = [
                    f"Discord bot: {client.user} id={getattr(client.user, 'id', 'unknown')}",
                    f"Visible guilds: {len(client.guilds)}",
                ]
                for guild in sorted(client.guilds, key=lambda g: g.name.lower()):
                    lines.append(
                        f"- {guild.name} id={guild.id} channels={len(guild.channels)} "
                        f"members={getattr(guild, 'member_count', 'unknown')}"
                    )
                await finish("\n".join(lines))
                return

            guild = client.get_guild(guild_id)
            if not guild:
                await finish(f"Guild not found: {guild_id}")
                return

            if action == "list_channels":
                lines = [f"Channels in {guild.name} ({guild.id}):"]
                for ch in sorted(guild.channels, key=lambda c: (getattr(c, 'position', 0), c.name)):
                    kind = ch.__class__.__name__.replace('Channel','').lower()
                    parent = f" | category={ch.category.name}" if getattr(ch, 'category', None) else ""
                    lines.append(f"- {ch.name} ({kind}) id={ch.id}{parent}")
                await finish("\n".join(lines))
                return

            if action == "list_roles":
                lines = [f"Roles in {guild.name} ({guild.id}):"]
                for role in sorted(guild.roles, key=lambda r: r.position, reverse=True):
                    lines.append(f"- {role.name} id={role.id} pos={role.position} managed={role.managed}")
                await finish("\n".join(lines))
                return

            if action == "list_members":
                lines = [f"Members in {guild.name} ({guild.id}):"]
                for m in guild.members:
                    roles = [r.name for r in m.roles if not r.is_default()]
                    lines.append(f"- {m.display_name} ({m}) id={m.id} roles={roles}")
                await finish("\n".join(lines[:250]) + (f"\n... truncated at 250 of {len(guild.members)}" if len(guild.members) > 250 else ""))
                return

            reason = kwargs.get("reason") or "Managed by Andrew"

            if action == "create_category":
                name = kwargs.get("category_name") or kwargs.get("channel_name") or kwargs.get("target")
                if not name:
                    await finish("Missing category_name")
                    return
                cat = await guild.create_category(str(name), reason=reason)
                await finish(f"Created category: {cat.name} id={cat.id}")
                return

            if action == "create_channel":
                name = kwargs.get("channel_name") or kwargs.get("target")
                if not name:
                    await finish("Missing channel_name")
                    return
                category = None
                category_id = _as_int(kwargs.get("category_id"), "category_id")
                if category_id:
                    category = guild.get_channel(category_id)
                    if not isinstance(category, discord.CategoryChannel):
                        await finish(f"Invalid category_id: {category_id}")
                        return
                ctype = (kwargs.get("channel_type") or "text").lower()
                if ctype == "voice":
                    ch = await guild.create_voice_channel(str(name), category=category, reason=reason)
                else:
                    ch = await guild.create_text_channel(str(name), category=category, topic=kwargs.get("topic"), reason=reason)
                await finish(f"Created {ctype} channel: {ch.name} id={ch.id}")
                return

            channel = _find_channel(guild, _as_int(kwargs.get("channel_id"), "channel_id"), kwargs.get("channel_name"), kwargs.get("target"))
            role = _find_role(guild, _as_int(kwargs.get("role_id"), "role_id"), kwargs.get("role_name"), kwargs.get("target"))
            member = _find_member(guild, _as_int(kwargs.get("user_id"), "user_id"), kwargs.get("target"))

            if action == "edit_channel":
                if not channel:
                    await finish("Channel not found")
                    return
                edit_kwargs = {}
                if kwargs.get("channel_name"):
                    edit_kwargs["name"] = kwargs.get("channel_name")
                if kwargs.get("topic") is not None and hasattr(channel, "topic"):
                    edit_kwargs["topic"] = kwargs.get("topic")
                category_id = _as_int(kwargs.get("category_id"), "category_id")
                if category_id:
                    cat = guild.get_channel(category_id)
                    if isinstance(cat, discord.CategoryChannel):
                        edit_kwargs["category"] = cat
                    else:
                        await finish("Invalid category_id")
                        return
                if not edit_kwargs:
                    await finish("No channel edits specified")
                    return
                await channel.edit(**edit_kwargs, reason=reason)
                await finish(f"Edited channel: {channel.name} id={channel.id}")
                return

            if action in {"delete_channel", "delete_category"}:
                target_channel = channel or guild.get_channel(_as_int(kwargs.get("category_id"), "category_id") or 0)
                if not target_channel:
                    await finish("Channel/category not found")
                    return
                name, cid = target_channel.name, target_channel.id
                await target_channel.delete(reason=reason)
                await finish(f"Deleted channel/category: {name} id={cid}")
                return

            if action == "create_role":
                name = kwargs.get("role_name") or kwargs.get("target")
                if not name:
                    await finish("Missing role_name")
                    return
                colour = discord.Color.from_str(kwargs.get("color")) if kwargs.get("color") else discord.Color.default()
                new_role = await guild.create_role(name=str(name), color=colour, hoist=bool(kwargs.get("hoist", False)), reason=reason)
                await finish(f"Created role: {new_role.name} id={new_role.id}")
                return

            if action == "edit_role":
                if not role:
                    await finish("Role not found")
                    return
                edit_kwargs = {}
                new_name = kwargs.get("role_name")
                if new_name:
                    edit_kwargs["name"] = new_name
                if kwargs.get("color"):
                    edit_kwargs["color"] = discord.Color.from_str(kwargs.get("color"))
                hoist = _truth(kwargs.get("hoist"))
                if hoist is not None:
                    edit_kwargs["hoist"] = hoist
                if not edit_kwargs:
                    await finish("No role edits specified")
                    return
                await role.edit(**edit_kwargs, reason=reason)
                await finish(f"Edited role: {role.name} id={role.id}")
                return

            if action == "delete_role":
                if not role:
                    await finish("Role not found")
                    return
                if role.is_default() or role.managed:
                    await finish("Cannot delete default or managed role")
                    return
                name, rid = role.name, role.id
                await role.delete(reason=reason)
                await finish(f"Deleted role: {name} id={rid}")
                return

            if action in {"assign_role", "remove_role"}:
                if not member or not role:
                    await finish("Member or role not found")
                    return
                if action == "assign_role":
                    await member.add_roles(role, reason=reason)
                    await finish(f"Assigned role {role.name} to {member.display_name}")
                else:
                    await member.remove_roles(role, reason=reason)
                    await finish(f"Removed role {role.name} from {member.display_name}")
                return

            if action == "set_permissions":
                if not channel or not role:
                    await finish("Channel or role not found")
                    return
                overwrite = discord.PermissionOverwrite()
                _apply_perm_list(overwrite, kwargs.get("allow_perms"), True)
                _apply_perm_list(overwrite, kwargs.get("deny_perms"), False)
                await channel.set_permissions(role, overwrite=overwrite, reason=reason)
                await finish(f"Set permissions on {channel.name} for role {role.name}")
                return

            if action == "kick_member":
                if not member:
                    await finish("Member not found")
                    return
                name = str(member)
                await member.kick(reason=reason)
                await finish(f"Kicked member: {name} | reason={reason}")
                return

            await finish(f"Unknown action: {action}")
        except Exception as e:
            await finish(f"Discord manager error during {action}: {type(e).__name__}: {e}")

    try:
        await client.start(token)
        return await asyncio.wait_for(done, timeout=5)
    except asyncio.TimeoutError:
        try:
            await client.close()
        except Exception:
            pass
        return f"Discord manager timed out during {action}"
    except Exception as e:
        try:
            await client.close()
        except Exception:
            pass
        return f"Discord manager failed during {action}: {type(e).__name__}: {e}"


async def run(**kwargs: Any) -> str:
    return await _run_discord_action(**kwargs)


if __name__ == "__main__":
    print(asyncio.run(run(action="list_channels")))
