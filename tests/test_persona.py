from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from hixbot.models import BufferedMessage, ChatResult
from hixbot.persona import PersonaProfileUpdater
from hixbot.storage import SQLiteStore


class FakeChat:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls = 0

    async def complete(self, messages, *, temperature=0.8, max_tokens=512):
        self.calls += 1
        return ChatResult(provider="fake", content=self.content)


class PersonaProfileUpdaterTests(unittest.IsolatedAsyncioTestCase):
    def make_store(self, tmp: str) -> SQLiteStore:
        return SQLiteStore(
            Path(tmp) / "hixbot.sqlite3",
            message_ttl_seconds=10,
            default_cooldown_seconds=45,
        )

    def make_messages(self) -> list[BufferedMessage]:
        return [
            BufferedMessage(
                id=1,
                guild_id=10,
                channel_id=20,
                author_id=30,
                author_name="tester",
                content="오늘도 억까 레전드 ㄱㄱ",
                created_at=1000,
            )
        ]

    async def test_updates_global_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(tmp)
            updater = PersonaProfileUpdater(store=store, chat=FakeChat("- 드립은 짧고 빠르게."))
            updated = await updater.update_from_messages(self.make_messages())
            profile = store.get_persona_profile()
            self.assertTrue(updated)
            self.assertIsNotNone(profile)
            self.assertEqual(profile.profile_markdown, "- 드립은 짧고 빠르게.")
            self.assertEqual(profile.message_count, 1)
            store.close()

    async def test_no_update_marker_does_not_save_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(tmp)
            updater = PersonaProfileUpdater(store=store, chat=FakeChat("NO_PERSONA_UPDATE"))
            updated = await updater.update_from_messages(self.make_messages())
            self.assertFalse(updated)
            self.assertIsNone(store.get_persona_profile())
            store.close()


if __name__ == "__main__":
    unittest.main()
