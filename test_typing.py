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

    async def send(self, text):
        self.sent.append(text)


class FakeTypingEvent:
    def __init__(self, chat_id, typing=True):
        self.chat_id = chat_id
        self.typing = typing


class FakeMessage:
    def __init__(self, text="hi", out=False):
        self.text = text
        self.out = out


class FakeMessageEvent:
    def __init__(self, text="hi", out=False):
        self.message = FakeMessage(text, out)


class TypingMirrorTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        relay._dm_channel = FakeChannel()
        relay._target_peer_id = 777
        relay._last_typing_at = 0.0
        relay._typing_task = None
        # Shrink the timings so tests run fast.
        relay.TYPING_INTERVAL = 0.05
        relay.TYPING_STALE_AFTER = 0.15

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


if __name__ == "__main__":
    unittest.main()
