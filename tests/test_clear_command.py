"""Regression tests for /clear, runnable without Telegram/API credentials."""

import ast
import asyncio
import logging
from pathlib import Path
from types import SimpleNamespace
import unittest


class FakeTelegramError(Exception):
    pass


class FakeBot:
    def __init__(self, message_id, *, fail_send=False, fail_bulk=False):
        self.message_id = message_id
        self.fail_send = fail_send
        self.fail_bulk = fail_bulk
        self.calls = []
        self.anchor_id = message_id + 1

    async def send_message(self, *, chat_id, text):
        self.calls.append(("send", chat_id, text))
        if self.fail_send:
            raise FakeTelegramError("send failed")
        return SimpleNamespace(message_id=self.anchor_id)

    async def delete_messages(self, *, chat_id, message_ids):
        self.calls.append(("bulk_delete", chat_id, list(message_ids)))
        if self.fail_bulk:
            raise FakeTelegramError("bulk delete failed")

    async def delete_message(self, *, chat_id, message_id):
        self.calls.append(("single_delete", chat_id, message_id))


class FakeMessage:
    def __init__(self, message_id):
        self.message_id = message_id
        self.replies = []

    async def reply_text(self, text):
        self.replies.append(text)


def load_clear_handler():
    """Load the real handler without importing unrelated cloud/API dependencies."""
    source = Path(__file__).resolve().parents[1].joinpath("bot.py").read_text(encoding="utf-8")
    function = next(
        item for item in ast.parse(source).body
        if isinstance(item, ast.AsyncFunctionDef) and item.name == "clear_command"
    )
    # Postponed annotations allow testing this handler in isolation.
    module = ast.Module(
        body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), function],
        type_ignores=[],
    )
    namespace = {
        "ensure_started": lambda ctx: bool(ctx.user_data.get("started")),
        "CLEAR_SCREEN_MAX_MESSAGES": 300,
        "TelegramError": FakeTelegramError,
        "logger": logging.getLogger("test-clear"),
    }
    exec(compile(ast.fix_missing_locations(module), "bot.py", "exec"), namespace)
    return namespace["clear_command"]


class ClearCommandTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.handler = staticmethod(load_clear_handler())

    def create_objects(self, *, started=True, chat_type="private", message_id=105, **kwargs):
        message = FakeMessage(message_id)
        chat = SimpleNamespace(id=52, type=chat_type)
        update = SimpleNamespace(message=message, effective_message=message, effective_chat=chat)
        bot = FakeBot(message_id, **kwargs)
        data = {"started": started, "history": [{"role": "user", "content": "Hi"}],
                "document_text": "my pdf", "weather_city": "Ha Noi"}
        ctx = SimpleNamespace(bot=bot, user_data=data)
        return update, ctx, bot, data.copy()

    def test_clear_preserves_session_and_context_without_start(self):
        update, ctx, bot, original = self.create_objects()
        asyncio.run(self.handler(update, ctx))
        self.assertEqual(ctx.user_data, original)
        self.assertEqual(bot.calls[0][0], "send")
        self.assertTrue(any(call[0] == "bulk_delete" for call in bot.calls))
        removed = [mid for name, _, mids in bot.calls if name == "bulk_delete" for mid in mids]
        self.assertIn(update.message.message_id, removed)
        self.assertNotIn(bot.anchor_id, removed)
        self.assertEqual(update.message.replies, [])

    def test_bulk_failure_falls_back_and_leaves_anchor(self):
        update, ctx, bot, _ = self.create_objects(message_id=4, fail_bulk=True)
        asyncio.run(self.handler(update, ctx))
        removed = [mid for name, _, mid in bot.calls if name == "single_delete"]
        self.assertEqual(removed, [1, 2, 3, 4])
        self.assertNotIn(bot.anchor_id, removed)

    def test_send_failure_does_not_delete_chat(self):
        update, ctx, bot, original = self.create_objects(fail_send=True)
        asyncio.run(self.handler(update, ctx))
        self.assertEqual(len(bot.calls), 1)
        self.assertEqual(ctx.user_data, original)

    def test_private_only(self):
        update, ctx, bot, _ = self.create_objects(chat_type="group")
        asyncio.run(self.handler(update, ctx))
        self.assertEqual(bot.calls, [])
        self.assertIn("chat riêng", update.message.replies[0])


if __name__ == "__main__":
    unittest.main()
