from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from hixbot.bot import HixbotClient
from hixbot.policy import ResponsePolicyEngine
from hixbot.storage import SQLiteStore, now_ts


class FakeTyping:
    async def __aenter__(self):
        return None

    async def __aexit__(self, exc_type, exc, traceback):
        return None


class FakeChannel:
    def __init__(self) -> None:
        self.id = 20
        self.sent: list[str] = []

    def typing(self):
        return FakeTyping()

    async def send(self, content, **kwargs):
        self.sent.append(content)


class FakeResponseJudge:
    def __init__(self, result: bool) -> None:
        self.result = result
        self.calls = 0

    async def should_respond(self, **kwargs):
        self.calls += 1
        return self.result


class FakeMessage:
    def __init__(self, *, content: str, channel: FakeChannel) -> None:
        self.guild = SimpleNamespace(id=10)
        self.channel = channel
        self.content = content
        self.author = SimpleNamespace(id=30, display_name="tester", bot=False)
        self.mentions = []
        self.reference = None


class BotResponseFlowTests(unittest.IsolatedAsyncioTestCase):
    def make_store(self, tmp: str) -> SQLiteStore:
        return SQLiteStore(
            Path(tmp) / "hixbot.sqlite3",
            message_ttl_seconds=60,
            default_cooldown_seconds=45,
        )

    def make_client(self, store: SQLiteStore, judge: FakeResponseJudge) -> HixbotClient:
        client = object.__new__(HixbotClient)
        client.user = SimpleNamespace(id=999)
        client.store = store
        client.settings = SimpleNamespace(
            max_response_chars=1800,
            response_context_window_seconds=180,
            response_context_min_messages=3,
            response_context_min_authors=2,
            response_judge_max_context_messages=8,
        )
        client.policy_engine = ResponsePolicyEngine()
        client.response_judge = judge
        client.reply_calls = 0

        async def fake_build_reply(message, guild_id, channel_id, content):
            client.reply_calls += 1
            return "응답"

        client._build_reply = fake_build_reply
        return client

    def seed_context(self, store: SQLiteStore) -> None:
        timestamp = now_ts()
        store.append_buffer_message(
            guild_id=10,
            channel_id=20,
            author_id=31,
            author_name="a",
            content="오늘 뭐 할까",
            created_at=timestamp,
        )
        store.append_buffer_message(
            guild_id=10,
            channel_id=20,
            author_id=32,
            author_name="b",
            content="발로 한 판?",
            created_at=timestamp,
        )

    async def test_semantic_candidate_is_not_judged_without_recent_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(tmp)
            channel = FakeChannel()
            judge = FakeResponseJudge(True)
            client = self.make_client(store, judge)

            await HixbotClient.on_message(client, FakeMessage(content="나도 감", channel=channel))

            self.assertEqual(judge.calls, 0)
            self.assertEqual(client.reply_calls, 0)
            self.assertEqual(channel.sent, [])
            store.close()

    async def test_judge_false_stays_quiet(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(tmp)
            self.seed_context(store)
            channel = FakeChannel()
            judge = FakeResponseJudge(False)
            client = self.make_client(store, judge)

            await HixbotClient.on_message(client, FakeMessage(content="나도 감", channel=channel))

            self.assertEqual(judge.calls, 1)
            self.assertEqual(client.reply_calls, 0)
            self.assertEqual(channel.sent, [])
            store.close()

    async def test_judge_true_builds_and_sends_reply(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(tmp)
            self.seed_context(store)
            channel = FakeChannel()
            judge = FakeResponseJudge(True)
            client = self.make_client(store, judge)

            discord_stub = SimpleNamespace(
                AllowedMentions=SimpleNamespace(none=lambda: None)
            )
            with patch("hixbot.bot.discord", discord_stub):
                await HixbotClient.on_message(client, FakeMessage(content="나도 감", channel=channel))

            self.assertEqual(judge.calls, 1)
            self.assertEqual(client.reply_calls, 1)
            self.assertEqual(channel.sent, ["응답"])
            store.close()


if __name__ == "__main__":
    unittest.main()
