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

import discord
from dotenv import load_dotenv
from telethon import TelegramClient, events

load_dotenv()  # config is loaded from a .env file

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("dm-relay")

DISCORD_LIMIT = 2000
TELEGRAM_LIMIT = 4096
RECONNECT_DELAY = 5


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


@tg.on(events.NewMessage(chats=TG_TARGET))
async def on_telegram_message(event):
    msg = event.message
    # Ignore our own outgoing messages and non-text content.
    if msg.out or not msg.text:
        return
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
    await tg.get_entity(TG_TARGET)  # resolve once so a bad target fails fast
    log.info("Telegram target: %s", TG_TARGET)
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
