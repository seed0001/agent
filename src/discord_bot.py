"""
Discord bot: receives messages, runs agent, sends response.
Splits long replies into chunks, attaches TTS voice to each reply.
Emits notifications for web/desktop when messages arrive.
Consumes outreach queue for proactive messages.
"""
import asyncio
import io

from config.settings import DISCORD_OWNER_ID, DISCORD_BOT_TOKEN

DISCORD_MAX_LEN = 1900  # leave buffer under 2000

# Will be set by app on startup
_agent_ref = None
_discord_client = None


def set_agent(agent):
    global _agent_ref
    _agent_ref = agent


async def _run_discord_bot():
    """Run the Discord bot (called from lifespan)."""
    if not DISCORD_BOT_TOKEN:
        print("Discord: DISCORD_BOT_TOKEN not set, skipping Discord bot.")
        return

    try:
        import discord
        from discord.ext import commands
    except ImportError:
        print("Discord: discord.py not installed. Run: pip install discord.py")
        return

    global _discord_client

    intents = discord.Intents.default()
    intents.message_content = True
    intents.messages = True
    intents.dm_messages = True
    intents.guild_messages = True

    bot = commands.Bot(command_prefix="!", intents=intents)
    _discord_client = bot

    from src import contacts
    from src import notifications
    from src.outreach import get_outreach_queue, OutreachMessage

    @bot.event
    async def on_ready():
        print(f"Discord bot ready: {bot.user}")

    @bot.event
    async def on_message(message):
        if message.author.bot:
            return
        if not _agent_ref:
            return
        # Only respond to DMs or @mentions in servers
        if message.guild and bot.user not in message.mentions:
            return

        author_name = message.author.display_name or str(message.author)
        author_id = str(message.author.id)
        content = (message.content or "").strip()
        if message.guild and bot.user in message.mentions:
            content = content.replace(f"<@{bot.user.id}>", "").strip()
        if not content:
            return

        # Notify owner: desktop + web
        notifications.emit_notification(
            "discord_message",
            f"Discord: {author_name}",
            content[:200],
            {"author": author_name, "author_id": author_id, "content": content},
        )
        # Store in memory so when Creator replies (e.g. in web), agent knows what they're responding to
        try:
            _agent_ref.memory.add_short_term(f"[Discord – {author_name} said]: {content}")
        except Exception:
            pass
        notifications.show_desktop_notification(
            f"Discord from {author_name}",
            content[:200],
        )

        # Load contact context
        contact = contacts.get_contact("", discord_id=author_id)
        contact_ctx = contacts.format_contact_for_context(contact)
        ctx_prefix = ""
        if contact_ctx:
            ctx_prefix = f"[Contact profile: {contact_ctx}]\n\n"
        from src.agent.soul import get_context_for_speaker
        speaker_ctx = get_context_for_speaker(is_web=False, discord_id=author_id, author_name=author_name)
        if speaker_ctx:
            ctx_prefix += speaker_ctx
        user_msg = f"{ctx_prefix}Message from {author_name} (Discord, discord_id={author_id}) who just said: {content}"

        _agent_ref.memory.set_working("current_speaker_discord_id", author_id)

        narrate_queue = asyncio.Queue()

        async def run_agent():
            try:
                return await _agent_ref.chat(user_msg, narrate_queue=narrate_queue)
            except Exception as e:
                return f"Sorry, I hit an error: {e}"

        agent_task = asyncio.create_task(run_agent())
        status_msg = None

        async def edit_status_loop():
            nonlocal status_msg
            status_msg = await message.reply("🔄 Processing...")
            while not agent_task.done():
                try:
                    item = await asyncio.wait_for(narrate_queue.get(), timeout=0.3)
                except asyncio.TimeoutError:
                    continue
                if item and item.get("type") == "narrate":
                    text = (item.get("text") or "")[:1900]
                    try:
                        await status_msg.edit(content=f"🔄 {text}")
                    except Exception:
                        pass
            while True:
                try:
                    item = narrate_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if item and item.get("type") == "narrate":
                    text = (item.get("text") or "")[:1900]
                    try:
                        await status_msg.edit(content=f"🔄 {text}")
                    except Exception:
                        pass

        edit_task = asyncio.create_task(edit_status_loop())
        reply = await agent_task
        await edit_task
        reply = (reply or "…").strip()

        # Edit status to "Complete" before sending final reply
        if status_msg:
            try:
                await status_msg.edit(content="✅ Complete.")
            except Exception:
                pass

        # Split long messages into chunks
        def chunk_text(text: str, max_len: int = DISCORD_MAX_LEN) -> list[str]:
            if len(text) <= max_len:
                return [text] if text else []
            chunks = []
            while text:
                if len(text) <= max_len:
                    chunks.append(text)
                    break
                cut = text.rfind("\n", 0, max_len + 1)
                if cut <= 0:
                    cut = text.rfind(" ", 0, max_len + 1)
                if cut <= 0:
                    cut = max_len
                chunks.append(text[:cut].strip())
                text = text[cut:].strip()
            return [c for c in chunks if c]

        chunks = chunk_text(reply)

        # Generate voice (TTS) for full reply
        voice_bytes = None
        if reply:
            try:
                from src.voice.tts import synthesize
                from src.user_settings import get_tts_voice

                voice_bytes = await synthesize(reply, voice=get_tts_voice())
            except Exception:
                pass

        # Send text chunks; attach voice to first message
        try:
            for i, chunk in enumerate(chunks):
                files = []
                if i == 0 and voice_bytes and len(voice_bytes) < 25 * 1024 * 1024:  # 25MB limit
                    files.append(discord.File(io.BytesIO(voice_bytes), filename="reply.mp3"))
                if files:
                    await message.reply(chunk, files=files)
                else:
                    await message.reply(chunk)
        except Exception:
            for chunk in chunks:
                try:
                    await message.channel.send(chunk)
                except Exception:
                    pass

    await bot.start(DISCORD_BOT_TOKEN)


async def _outreach_consumer():
    """Background task: send proactive Discord messages from outreach queue."""
    if not DISCORD_OWNER_ID:
        return
    try:
        import discord
    except ImportError:
        return

    from src.outreach import OutreachMessage, get_outreach_queue

    q = get_outreach_queue()
    while True:
        try:
            msg = await asyncio.wait_for(q.get(), timeout=300.0)
        except asyncio.TimeoutError:
            continue
        if not isinstance(msg, OutreachMessage) or msg.channel != "discord":
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                pass
            continue
        client = _discord_client
        if not client:
            await asyncio.sleep(5)
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                pass
            continue
        if not client.is_ready:
            await asyncio.sleep(2)
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                pass
            continue
        user_id = msg.target_user_id or DISCORD_OWNER_ID
        content = (msg.content or "").strip()
        if not content:
            continue
        from src.logging_config import log_outreach_attempt, log_outreach_failure, log_outreach_success
        from src import notifications
        log_outreach_attempt("discord", user_id, content[:80])
        try:
            user = await client.fetch_user(int(user_id))
            voice_bytes = None
            try:
                from src.voice.tts import synthesize
                from src.user_settings import get_tts_voice

                voice_bytes = await synthesize(content, voice=get_tts_voice())
            except Exception:
                pass
            first = True
            remainder = content
            while remainder:
                chunk = remainder[:DISCORD_MAX_LEN]
                if len(remainder) > DISCORD_MAX_LEN:
                    cut = remainder.rfind("\n", 0, DISCORD_MAX_LEN + 1)
                    if cut <= 0:
                        cut = remainder.rfind(" ", 0, DISCORD_MAX_LEN + 1)
                    if cut <= 0:
                        cut = DISCORD_MAX_LEN
                    chunk = remainder[:cut].strip()
                    remainder = remainder[cut:].strip()
                else:
                    remainder = ""
                files = []
                if first and voice_bytes and len(voice_bytes) < 25 * 1024 * 1024:
                    files.append(discord.File(io.BytesIO(voice_bytes), filename="reply.mp3"))
                first = False
                if files:
                    await user.send(chunk, files=files)
                else:
                    await user.send(chunk)
            log_outreach_success("discord", user_id)
            # Store in memory so when Creator replies, agent knows what they're responding to
            try:
                _agent_ref.memory.add_short_term(f"[Proactive message I sent you via Discord]: {content}")
            except Exception:
                pass
        except Exception as e:
            log_outreach_failure("discord", user_id, str(e))
            notifications.emit_notification(
                "delivery_failed",
                "Discord DM failed",
                f"Could not deliver to {user_id}: {str(e)[:100]}. Falling back to web.",
                {"channel": "discord", "target": user_id, "error": str(e), "content_preview": content[:100]},
            )
            try:
                notifications.show_desktop_notification(
                    "Discord DM failed — check web app",
                    f"Error: {str(e)[:80]}. Message delivered via web instead.",
                )
            except Exception:
                pass
            try:
                notifications.emit_notification("proactive", "Proactive (Discord failed)", content[:200], {"content": content})
                _agent_ref.memory.add_short_term(f"[Proactive I sent you via web (Discord failed)]: {content}")
            except Exception:
                pass


def start_discord_task():
    """Start Discord bot and outreach consumer. Returns the task."""
    async def _run():
        consumer = asyncio.create_task(_outreach_consumer())
        try:
            await _run_discord_bot()
        finally:
            consumer.cancel()
            try:
                await consumer
            except asyncio.CancelledError:
                pass

    return asyncio.create_task(_run())
