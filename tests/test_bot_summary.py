from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from hixbot.bot import HixbotClient
from hixbot.models import ChatResult
from hixbot.storage import SQLiteStore


class NoSummaryChat:
    async def complete(self, messages, *, temperature=0.8, max_tokens=512):
        return ChatResult(provider="fake", content="NO_SUMMARY")


class FakeWiki:
    def __init__(self) -> None:
        self.calls = 0

    def append_summary(self, guild_id, summary, *, channel_ids):
        self.calls += 1


class FakeIndexer:
    async def reindex_guild(self, guild_id):
        return 0


class FakePersonaUpdater:
    def __init__(self) -> None:
        self.calls = 0
        self.message_ids: list[list[int]] = []

    async def update_from_messages(self, messages):
        self.calls += 1
        self.message_ids.append([message.id for message in messages])
        return True


class BotSummaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_summary_batch_updates_persona_even_without_wiki_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStore(
                Path(tmp) / "hixbot.sqlite3",
                message_ttl_seconds=60,
                default_cooldown_seconds=45,
            )
            first = store.append_buffer_message(
                guild_id=1,
                channel_id=2,
                author_id=3,
                author_name="a",
                content="ㄱㄱ?",
            )
            second = store.append_buffer_message(
                guild_id=1,
                channel_id=2,
                author_id=4,
                author_name="b",
                content="억까 레전드",
            )
            client = object.__new__(HixbotClient)
            client.store = store
            client.settings = SimpleNamespace(min_summary_messages=2)
            client.chat = NoSummaryChat()
            client.wiki = FakeWiki()
            client.indexer = FakeIndexer()
            client.persona_updater = FakePersonaUpdater()

            await HixbotClient._summarize_guild(client, 1)

            self.assertEqual(client.persona_updater.calls, 1)
            self.assertEqual(client.persona_updater.message_ids, [[first, second]])
            self.assertEqual(client.wiki.calls, 0)
            self.assertEqual(store.unsummarized_messages(1), [])
            store.close()


if __name__ == "__main__":
    unittest.main()
