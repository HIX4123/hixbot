from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from hixbot.storage import SQLiteStore


class SQLiteStoreTests(unittest.TestCase):
    def make_store(self, tmp: str, ttl: int = 10) -> SQLiteStore:
        return SQLiteStore(
            Path(tmp) / "hixbot.sqlite3",
            message_ttl_seconds=ttl,
            default_cooldown_seconds=45,
        )

    def test_channel_policy_defaults_and_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(tmp)
            default = store.resolve_channel_policy(1, 2)
            self.assertTrue(default.observe_enabled)
            self.assertEqual(default.cooldown_seconds, 45)
            updated = store.set_channel_policy(
                1,
                2,
                respond_enabled=False,
                cooldown_seconds=120,
            )
            self.assertTrue(updated.observe_enabled)
            self.assertFalse(updated.respond_enabled)
            self.assertEqual(updated.cooldown_seconds, 120)
            store.close()

    def test_recent_messages_expire(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(tmp, ttl=5)
            store.append_buffer_message(
                guild_id=1,
                channel_id=2,
                author_id=3,
                author_name="user",
                content="hello",
                created_at=100,
            )
            self.assertEqual(len(store.recent_messages(1, 2, limit=10, at=101)), 1)
            deleted = store.purge_expired_messages(at=106)
            self.assertEqual(deleted, 1)
            self.assertEqual(store.recent_messages(1, 2, limit=10, at=106), [])
            store.close()

    def test_unsummarized_watermark(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(tmp)
            first = store.append_buffer_message(
                guild_id=1,
                channel_id=2,
                author_id=3,
                author_name="a",
                content="one",
            )
            second = store.append_buffer_message(
                guild_id=1,
                channel_id=2,
                author_id=4,
                author_name="b",
                content="two",
            )
            self.assertEqual([item.id for item in store.unsummarized_messages(1)], [first, second])
            store.mark_summarized(1, first)
            self.assertEqual([item.id for item in store.unsummarized_messages(1)], [second])
            store.close()

    def test_learn_message_buffer_expires(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(tmp)
            store.append_learn_buffer_message(
                guild_id=1,
                channel_id=2,
                message_id=3,
                author_id=4,
                author_name="user",
                content="old message",
                created_at=100,
                ttl_seconds=-1,
            )
            self.assertEqual(store.purge_expired_learn_messages(), 1)
            store.close()


if __name__ == "__main__":
    unittest.main()
