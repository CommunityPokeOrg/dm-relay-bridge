"""Tests for the Telegram -> Discord typing-indicator mirroring.

Run with:  python -m unittest test_typing -v
"""

import asyncio
import os
import unittest

# relay.py reads required config from the environment at import time.
os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("USER_ID", "1234")
os.environ.setdefault("TG_API_ID", "12345")
os.environ.setdefault("TG_API_HASH", "test-hash")
os.environ.setdefault("TG_SESSION", os.path.join(os.path.dirname(__file__), "test_session"))
os.environ.setdefault("TG_TARGET", "@someone")

import relay  # noqa: E402


class FakeChannel:
    def __init__(self):
        self.typing_calls = 0
        self.sent = []

    async def typing(self):
        self.typing_calls += 1

    async def send(self, text=None, file=None):
        if file is not None:
            self.sent.append(("file", file.filename, os.path.getsize(file.fp.name)))
        else:
            self.sent.append(text)


class FakeAttachment:
    def __init__(self, filename="pic.png", data=b"fake-bytes"):
        self.filename = filename
        self._data = data

    async def save(self, path):
        with open(path, "wb") as f:
            f.write(self._data)


class FakeDiscordMessage:
    def __init__(self, text="", attachments=(), author_id=1234, channel=None):
        self.content = text
        self.attachments = list(attachments)
        self.guild = None
        self.author = type("A", (), {"id": author_id})()
        self.channel = channel or FakeChannel()


class FakeTG:
    def __init__(self):
        self.sent_files = []
        self.sent_messages = []

    async def send_file(self, target, path, caption=None):
        with open(path, "rb") as f:
            self.sent_files.append((target, os.path.basename(path), f.read(), caption))

    async def send_message(self, target, text):
        self.sent_messages.append((target, text))


class FakeTypingEvent:
    def __init__(self, chat_id, typing=True):
        self.chat_id = chat_id
        self.typing = typing


class FakeMessage:
    def __init__(self, text="", out=False, media=False, media_size=10):
        self.text = text
        self.out = out
        self.media = object() if media else None
        self._media_size = media_size

    async def download_media(self, file=None):
        path = os.path.join(file, "tg_photo.jpg")
        with open(path, "wb") as f:
            f.write(b"x" * self._media_size)
        return path


class FakeMessageEvent:
    def __init__(self, text="", out=False, media=False, media_size=10):
        self.message = FakeMessage(text, out, media, media_size)


class TypingMirrorTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        relay._dm_channel = FakeChannel()
        relay._target_peer_id = 777
        relay._last_typing_at = 0.0
        relay._typing_task = None
        # Shrink the timings so tests run fast.
        relay.TYPING_INTERVAL = 0.05
        relay.TYPING_STALE_AFTER = 0.15
        relay.DISCORD_FILE_LIMIT = 10 * 1024 * 1024

    async def asyncTearDown(self):
        if relay._typing_task is not None and not relay._typing_task.done():
            relay._typing_task.cancel()
            try:
                await relay._typing_task
            except asyncio.CancelledError:
                pass
        relay._typing_task = None

    async def test_typing_update_triggers_indicator(self):
        await relay.on_telegram_typing(FakeTypingEvent(chat_id=777))
        await asyncio.sleep(0.2)
        self.assertGreaterEqual(relay._dm_channel.typing_calls, 2)

    async def test_repeated_updates_keep_indicator_alive(self):
        for _ in range(4):
            await relay.on_telegram_typing(FakeTypingEvent(chat_id=777))
            await asyncio.sleep(0.06)
        # ~0.24s of updates with the indicator re-triggered each interval.
        self.assertGreaterEqual(relay._dm_channel.typing_calls, 3)

    async def test_indicator_stops_without_updates(self):
        await relay.on_telegram_typing(FakeTypingEvent(chat_id=777))
        await asyncio.sleep(0.25)
        calls = relay._dm_channel.typing_calls
        await asyncio.sleep(0.1)
        self.assertEqual(relay._dm_channel.typing_calls, calls)

    async def test_other_chat_typing_is_ignored(self):
        await relay.on_telegram_typing(FakeTypingEvent(chat_id=999))
        await asyncio.sleep(0.1)
        self.assertEqual(relay._dm_channel.typing_calls, 0)

    async def test_non_typing_update_is_ignored(self):
        await relay.on_telegram_typing(FakeTypingEvent(chat_id=777, typing=False))
        await asyncio.sleep(0.1)
        self.assertEqual(relay._dm_channel.typing_calls, 0)

    async def test_relayed_reply_stops_indicator(self):
        await relay.on_telegram_typing(FakeTypingEvent(chat_id=777))
        await asyncio.sleep(0.06)
        await relay.on_telegram_message(FakeMessageEvent(text="reply"))
        self.assertEqual(relay._last_typing_at, 0.0)
        await asyncio.sleep(0.02)  # let the cancellation land
        self.assertTrue(relay._typing_task.done())
        calls = relay._dm_channel.typing_calls
        await asyncio.sleep(0.12)
        self.assertEqual(relay._dm_channel.typing_calls, calls)
        self.assertEqual(relay._dm_channel.sent, ["reply"])

    async def test_telegram_media_uploaded_to_discord(self):
        await relay.on_telegram_message(FakeMessageEvent(media=True))
        self.assertEqual(relay._dm_channel.sent, [("file", "tg_photo.jpg", 10)])

    async def test_telegram_media_and_text(self):
        await relay.on_telegram_message(FakeMessageEvent(text="look", media=True))
        self.assertEqual(relay._dm_channel.sent[0], "look")
        self.assertEqual(relay._dm_channel.sent[1][0], "file")

    async def test_telegram_oversized_media_sends_note(self):
        relay.DISCORD_FILE_LIMIT = 5
        await relay.on_telegram_message(FakeMessageEvent(media=True, media_size=100))
        sent = relay._dm_channel.sent
        self.assertEqual(len(sent), 1)
        self.assertIsInstance(sent[0], str)
        self.assertIn("exceeds", sent[0])
        self.assertIn("tg_photo.jpg", sent[0])

    async def test_telegram_media_none_is_skipped(self):
        class NoMedia(FakeMessage):
            async def download_media(self, file=None):
                return None
        event = type("E", (), {"message": NoMedia(media=True)})()
        await relay.on_telegram_message(event)
        self.assertEqual(relay._dm_channel.sent, [])


class DiscordToTelegramTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._orig_tg = relay.tg
        relay.tg = FakeTG()

    async def asyncTearDown(self):
        relay.tg = self._orig_tg

    async def test_text_only_relayed_as_message(self):
        await relay.on_message(FakeDiscordMessage(text="hello"))
        self.assertEqual(relay.tg.sent_messages, [("@someone", "hello")])
        self.assertEqual(relay.tg.sent_files, [])

    async def test_attachment_sent_as_file_with_caption(self):
        msg = FakeDiscordMessage(text="cap", attachments=[FakeAttachment("a.png", b"1")])
        await relay.on_message(msg)
        self.assertEqual(len(relay.tg.sent_files), 1)
        target, name, data, caption = relay.tg.sent_files[0]
        self.assertEqual((name, data, caption), ("a.png", b"1", "cap"))
        self.assertEqual(relay.tg.sent_messages, [])

    async def test_attachment_without_text(self):
        await relay.on_message(FakeDiscordMessage(attachments=[FakeAttachment()]))
        self.assertEqual(len(relay.tg.sent_files), 1)
        self.assertIsNone(relay.tg.sent_files[0][3])

    async def test_long_text_sent_as_message_not_caption(self):
        long_text = "y" * (relay.TG_CAPTION_LIMIT + 1)
        msg = FakeDiscordMessage(text=long_text, attachments=[FakeAttachment()])
        await relay.on_message(msg)
        self.assertIsNone(relay.tg.sent_files[0][3])
        self.assertEqual(relay.tg.sent_messages, [("@someone", long_text)])

    async def test_caption_only_on_first_file(self):
        msg = FakeDiscordMessage(
            text="cap",
            attachments=[FakeAttachment("a.png"), FakeAttachment("b.png")],
        )
        await relay.on_message(msg)
        self.assertEqual([f[3] for f in relay.tg.sent_files], ["cap", None])

    async def test_wrong_author_ignored(self):
        await relay.on_message(FakeDiscordMessage(text="hi", author_id=9999))
        self.assertEqual(relay.tg.sent_messages, [])
        self.assertEqual(relay.tg.sent_files, [])

    async def test_guild_message_ignored(self):
        msg = FakeDiscordMessage(text="hi")
        msg.guild = object()
        await relay.on_message(msg)
        self.assertEqual(relay.tg.sent_messages, [])

    async def test_empty_message_ignored(self):
        await relay.on_message(FakeDiscordMessage(text=""))
        self.assertEqual(relay.tg.sent_messages, [])
        self.assertEqual(relay.tg.sent_files, [])


if __name__ == "__main__":
    unittest.main()
