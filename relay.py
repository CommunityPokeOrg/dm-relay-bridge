"""Relay DMs from one allowlisted Discord user to a Telegram chat and back.

Discord side: a discord.py bot that listens for DMs from exactly one
allowlisted Discord user ID.
Telegram side: a Telethon *user* session that posts each allowed DM to a
configured Telegram chat (sent as your own Telegram account) and relays
incoming replies from that chat back into the same Discord DM.

Everything is configured via environment variables — see .env.example.
"""

import asyncio
import logging
import os
import sys
import time

import discord
from dotenv import load_dotenv
from telethon import TelegramClient, events, utils

load_dotenv()  # config is loaded from a .env file

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("dm-relay")

DISCORD_LIMIT = 2000
TELEGRAM_LIMIT = 4096
RECONNECT_DELAY = 5
# Discord's typing indicator expires after ~10s, so re-trigger on an interval
# while Telegram keeps sending typing updates (they arrive every ~5s).
TYPING_INTERVAL = 8
TYPING_STALE_AFTER = 10


def env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        sys.exit(f"Missing required environment variable: {name}")
    return value


def split_text(text: str, limit: int):
    """Split text into chunks <= limit, preferring to break at newlines/spaces."""
    chunks = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = text.rfind(" ", 0, limit)
        if cut < limit // 2:
            cut = limit
        chunks.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    if text:
        chunks.append(text)
    return chunks


# --- Configuration -----------------------------------------------------------

BOT_TOKEN = env("BOT_TOKEN")
try:
    USER_ID = int(env("USER_ID"))
except ValueError:
    sys.exit("USER_ID must be a numeric Discord user ID")

TG_API_ID = int(env("TG_API_ID"))
TG_API_HASH = env("TG_API_HASH")
TG_SESSION = env("TG_SESSION")  # e.g. ./tg_session
TG_TARGET = env("TG_TARGET")    # @username, phone, numeric id, or 'me'
if TG_TARGET.lstrip("-").isdigit():
    TG_TARGET = int(TG_TARGET)

# --- Clients -----------------------------------------------------------------

intents = discord.Intents(dm_messages=True, message_content=True)
bot = discord.Client(intents=intents)
tg = TelegramClient(TG_SESSION, TG_API_ID, TG_API_HASH)

_dm_channel = None

# Typing-mirroring state.
_target_peer_id = None      # resolved marked peer id of TG_TARGET
_last_typing_at = 0.0       # monotonic time of the last target typing update
_typing_task = None         # task re-triggering the Discord typing indicator


async def discord_dm_channel():
    global _dm_channel
    if _dm_channel is None:
        user = await bot.fetch_user(USER_ID)
        _dm_channel = user.dm_channel or await user.create_dm()
    return _dm_channel


# --- Discord -> Telegram -----------------------------------------------------


@bot.event
async def on_message(message: discord.Message):
    # Only DMs from the single allowlisted user are relayed.
    if message.guild is not None or message.author.id != USER_ID:
        return
    text = message.content or ""
    if message.attachments:
        urls = "\n".join(a.url for a in message.attachments)
        text = f"{text}\n{urls}".strip()
    if not text:
        return
    try:
        for chunk in split_text(text, TELEGRAM_LIMIT):
            await tg.send_message(TG_TARGET, chunk)
        log.info("Discord -> Telegram: %d chars", len(text))
    except Exception:
        log.exception("Failed to relay DM to Telegram")
        await message.channel.send("[relay] failed to deliver your message to Telegram")


# --- Telegram -> Discord -----------------------------------------------------


async def _mirror_typing():
    """Keep the Discord DM typing indicator alive while the target types."""
    try:
        channel = await discord_dm_channel()
        while time.monotonic() - _last_typing_at < TYPING_STALE_AFTER:
            await channel.typing()
            await asyncio.sleep(TYPING_INTERVAL)
    except asyncio.CancelledError:
        pass
    except Exception:
        log.exception("Failed to mirror typing indicator to Discord")


@tg.on(events.UserUpdate)
async def on_telegram_typing(event):
    # Only typing actions from the configured target chat matter.
    if not event.typing or event.chat_id != _target_peer_id:
        return
    global _last_typing_at, _typing_task
    _last_typing_at = time.monotonic()
    if _typing_task is None or _typing_task.done():
        _typing_task = asyncio.create_task(_mirror_typing())


def _stop_typing():
    """Stop mirroring once the reply has been relayed."""
    global _last_typing_at
    _last_typing_at = 0.0
    if _typing_task is not None and not _typing_task.done():
        _typing_task.cancel()


@tg.on(events.NewMessage(chats=TG_TARGET))
async def on_telegram_message(event):
    msg = event.message
    # Ignore our own outgoing messages and non-text content.
    if msg.out or not msg.text:
        return
    _stop_typing()
    try:
        channel = await discord_dm_channel()
        for chunk in split_text(msg.text, DISCORD_LIMIT):
            await channel.send(chunk)
        log.info("Telegram -> Discord: %d chars", len(msg.text))
    except Exception:
        log.exception("Failed to relay Telegram message to Discord")


# --- Entrypoint --------------------------------------------------------------


async def run():
    await tg.start()  # first run prompts for phone/login code interactively
    me = await tg.get_me()
    log.info("Telegram: logged in as %s (id=%s)", me.username or me.first_name, me.id)
    target = await tg.get_entity(TG_TARGET)  # resolve so a bad target fails fast
    global _target_peer_id
    _target_peer_id = utils.get_peer_id(target)
    log.info("Telegram target: %s (peer id=%s)", TG_TARGET, _target_peer_id)
    await asyncio.gather(
        bot.start(BOT_TOKEN),
        tg.run_until_disconnected(),
    )


def main():
    while True:
        try:
            asyncio.run(run())
        except KeyboardInterrupt:
            break
        except Exception:
            log.exception("Relay crashed; restarting in %ds", RECONNECT_DELAY)
            try:
                asyncio.run(asyncio.sleep(RECONNECT_DELAY))
            except KeyboardInterrupt:
                break


if __name__ == "__main__":
    main()
