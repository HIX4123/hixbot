from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from hixbot.learning import LearnRunConfig, LearnRunner
from hixbot.models import ChatResult, ChatTurn, LearnSourceMessage
from hixbot.storage import SQLiteStore
from hixbot.wiki import WikiManager


class FakeChat:
    def __init__(self, store: SQLiteStore | None = None, stop_guild_id: int | None = None) -> None:
        self.store = store
        self.stop_guild_id = stop_guild_id
        self.calls = 0

    async def complete(
        self,
        messages: list[ChatTurn],
        *,
        temperature: float = 0.8,
        max_tokens: int = 512,
    ) -> ChatResult:
        self.calls += 1
        if self.store is not None and self.stop_guild_id is not None and self.calls == 1:
            self.store.request_learn_stop(self.stop_guild_id)
        return ChatResult(provider="fake", content="과거 대화에서 게임 모집 이야기가 반복됐다.")


class FakeIndexer:
    def __init__(self) -> None:
        self.indexed = 0

    async def index_chunk(self, chunk) -> int:
        self.indexed += 1
        return 1


class FakePersonaUpdater:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0
        self.message_ids: list[list[int]] = []

    async def update_from_messages(self, messages) -> bool:
        self.calls += 1
        self.message_ids.append([message.id for message in messages])
        if self.fail:
            raise RuntimeError("persona boom")
        return True


class FakeChannel:
    def __init__(
        self,
        channel_id: int,
        message_ids: list[int],
        *,
        bot_ids: set[int] | None = None,
        empty_ids: set[int] | None = None,
    ) -> None:
        self.id = channel_id
        self.name = f"channel-{channel_id}"
        bot_ids = bot_ids or set()
        empty_ids = empty_ids or set()
        self.messages = [
            LearnSourceMessage(
                id=message_id,
                guild_id=1,
                channel_id=channel_id,
                author_id=message_id + 100,
                author_name=f"user-{message_id}",
                content="" if message_id in empty_ids else f"message {message_id}",
                created_at=1000 + message_id,
                author_is_bot=message_id in bot_ids,
            )
            for message_id in message_ids
        ]
        self.fetch_after_calls: list[int | None] = []

    async def fetch_messages_after(
        self,
        after_message_id: int | None,
        *,
        limit: int,
    ) -> list[LearnSourceMessage]:
        self.fetch_after_calls.append(after_message_id)
        return [
            message
            for message in self.messages
            if after_message_id is None or message.id > after_message_id
        ][:limit]


class LearnRunnerTests(unittest.IsolatedAsyncioTestCase):
    def make_store(self, tmp: str) -> SQLiteStore:
        return SQLiteStore(
            Path(tmp) / "hixbot.sqlite3",
            message_ttl_seconds=10,
            default_cooldown_seconds=45,
        )

    def make_runner(
        self,
        *,
        store: SQLiteStore,
        wiki: WikiManager,
        chat: FakeChat,
        indexer: FakeIndexer,
        persona_updater: FakePersonaUpdater | None = None,
    ) -> LearnRunner:
        return LearnRunner(
            store=store,
            chat=chat,
            wiki=wiki,
            indexer=indexer,
            config=LearnRunConfig(
                batch_messages=2,
                sleep_seconds=0,
                history_ttl_seconds=10,
            ),
            persona_updater=persona_updater,
        )

    async def test_stop_then_start_resumes_after_saved_cursor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(tmp)
            wiki = WikiManager(Path(tmp))
            channel = FakeChannel(10, [1, 2, 3, 4, 5])

            first_runner = self.make_runner(
                store=store,
                wiki=wiki,
                chat=FakeChat(store, stop_guild_id=1),
                indexer=FakeIndexer(),
            )
            await first_runner.run_guild(1, [channel])
            self.assertEqual(store.get_learn_last_message_id(1, 10), 2)
            self.assertEqual(store.get_learn_job(1).status, "idle")

            second_runner = self.make_runner(
                store=store,
                wiki=wiki,
                chat=FakeChat(),
                indexer=FakeIndexer(),
            )
            await second_runner.run_guild(1, [channel])
            self.assertIn(2, channel.fetch_after_calls)
            self.assertEqual(store.get_learn_last_message_id(1, 10), 5)
            self.assertEqual(store.get_learn_job(1).status, "completed")
            store.close()

    async def test_failed_job_start_resumes_after_last_successful_cursor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(tmp)
            wiki = WikiManager(Path(tmp))
            channel = FakeChannel(10, [1, 2, 3])
            store.start_or_resume_learn_job(1)
            store.mark_learn_channel_progress(1, 10, 2)
            store.mark_learn_job_failed(1, "previous failure")

            runner = self.make_runner(
                store=store,
                wiki=wiki,
                chat=FakeChat(),
                indexer=FakeIndexer(),
            )
            await runner.run_guild(1, [channel])
            self.assertEqual(channel.fetch_after_calls[0], 2)
            self.assertEqual(store.get_learn_last_message_id(1, 10), 3)
            self.assertEqual(store.get_learn_job(1).status, "completed")
            store.close()

    async def test_completed_job_start_reads_only_new_messages(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(tmp)
            wiki = WikiManager(Path(tmp))
            channel = FakeChannel(10, [4, 5])
            store.start_or_resume_learn_job(1)
            store.mark_learn_channel_progress(1, 10, 3, completed=True)
            store.mark_learn_job_completed(1)

            runner = self.make_runner(
                store=store,
                wiki=wiki,
                chat=FakeChat(),
                indexer=FakeIndexer(),
            )
            await runner.run_guild(1, [channel])
            self.assertEqual(channel.fetch_after_calls[0], 3)
            self.assertEqual(store.get_learn_last_message_id(1, 10), 5)
            store.close()

    def test_running_start_does_not_reset_existing_progress(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(tmp)
            store.start_or_resume_learn_job(1)
            store.mark_learn_channel_progress(1, 10, 99)
            _, already_running = store.start_or_resume_learn_job(1)
            self.assertTrue(already_running)
            self.assertEqual(store.get_learn_last_message_id(1, 10), 99)
            store.close()

    def test_skipped_channel_status_is_set_not_repeatedly_accumulated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(tmp)
            store.start_or_resume_learn_job(1)
            store.set_learn_skipped_channels(1, 2)
            store.set_learn_skipped_channels(1, 2)
            self.assertEqual(store.get_learn_job(1).skipped_channels, 2)
            store.close()

    async def test_bot_and_empty_messages_are_skipped_but_cursor_advances(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(tmp)
            wiki = WikiManager(Path(tmp))
            channel = FakeChannel(10, [1, 2, 3, 4], bot_ids={1}, empty_ids={2})
            chat = FakeChat()
            runner = self.make_runner(
                store=store,
                wiki=wiki,
                chat=chat,
                indexer=FakeIndexer(),
            )
            await runner.run_guild(1, [channel])
            self.assertEqual(chat.calls, 1)
            self.assertEqual(store.get_learn_last_message_id(1, 10), 4)
            self.assertEqual(store.get_learn_job(1).processed_messages, 2)
            store.close()

    async def test_no_summary_updates_cursor_without_wiki_summary(self) -> None:
        class NoSummaryChat(FakeChat):
            async def complete(
                self,
                messages: list[ChatTurn],
                *,
                temperature: float = 0.8,
                max_tokens: int = 512,
            ) -> ChatResult:
                self.calls += 1
                return ChatResult(provider="fake", content="NO_SUMMARY")

        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(tmp)
            wiki = WikiManager(Path(tmp))
            indexer = FakeIndexer()
            channel = FakeChannel(10, [1, 2])
            runner = self.make_runner(
                store=store,
                wiki=wiki,
                chat=NoSummaryChat(),
                indexer=indexer,
            )
            await runner.run_guild(1, [channel])
            self.assertEqual(store.get_learn_last_message_id(1, 10), 2)
            self.assertEqual(store.get_learn_job(1).wiki_summaries, 0)
            self.assertEqual(indexer.indexed, 0)
            store.close()

    async def test_history_learning_updates_persona_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(tmp)
            wiki = WikiManager(Path(tmp))
            persona_updater = FakePersonaUpdater()
            channel = FakeChannel(10, [1, 2])
            runner = self.make_runner(
                store=store,
                wiki=wiki,
                chat=FakeChat(),
                indexer=FakeIndexer(),
                persona_updater=persona_updater,
            )
            await runner.run_guild(1, [channel])
            self.assertEqual(persona_updater.calls, 1)
            self.assertEqual(persona_updater.message_ids, [[1, 2]])
            store.close()

    async def test_persona_update_failure_does_not_fail_history_learning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(tmp)
            wiki = WikiManager(Path(tmp))
            channel = FakeChannel(10, [1, 2])
            runner = self.make_runner(
                store=store,
                wiki=wiki,
                chat=FakeChat(),
                indexer=FakeIndexer(),
                persona_updater=FakePersonaUpdater(fail=True),
            )
            with self.assertLogs("hixbot.learning", level="WARNING"):
                await runner.run_guild(1, [channel])
            self.assertEqual(store.get_learn_last_message_id(1, 10), 2)
            self.assertEqual(store.get_learn_job(1).status, "completed")
            store.close()


if __name__ == "__main__":
    unittest.main()
