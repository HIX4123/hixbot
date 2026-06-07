from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import hixbot.bot as bot_module
from hixbot.bot import HixbotClient
from hixbot.models import ChatResult
from hixbot.policy import ResponsePolicyEngine
from hixbot.storage import SQLiteStore, now_ts


class FakeChat:
    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, messages, *, temperature=0.8, max_tokens=512):
        self.calls += 1
        return ChatResult(provider="fake", content="짧게 끼어들기")


class FakeEmbeddings:
    async def embed(self, text):
        return [0.1]


class FakeRetriever:
    async def search(self, guild_id, query_vector, limit=5):
        return []


class FakeResponseJudge:
    def __init__(self, result: bool) -> None:
        self.result = result
        self.calls = 0

    async def should_respond(self, **kwargs):
        self.calls += 1
        return self.result


class FakeTyping:
    async def __aenter__(self):
        return None

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeChannel:
    def __init__(self, channel_id: int) -> None:
        self.id = channel_id
        self.sent: list[str] = []

    def typing(self):
        return FakeTyping()

    async def send(self, content, **kwargs):
        self.sent.append(content)


def make_message(
    *,
    content: str,
    channel: FakeChannel,
    author_id: int = 10,
    mentions: list[object] | None = None,
):
    return SimpleNamespace(
        guild=SimpleNamespace(id=1),
        channel=channel,
        author=SimpleNamespace(id=author_id, bot=False, display_name=f"user-{author_id}"),
        content=content,
        mentions=mentions or [],
        reference=None,
    )


class BotResponsePolicyTests(unittest.IsolatedAsyncioTestCase):
    def make_client(self, tmp: str, *, judge_result: bool) -> HixbotClient:
        client = object.__new__(HixbotClient)
        client.settings = SimpleNamespace(
            max_context_messages=20,
            max_response_chars=1800,
            response_context_window_seconds=180,
            response_context_min_messages=3,
            response_context_min_authors=2,
            response_judge_max_context_messages=8,
        )
        client.store = SQLiteStore(
            Path(tmp) / "hixbot.sqlite3",
            message_ttl_seconds=600,
            default_cooldown_seconds=45,
        )
        client.user = SimpleNamespace(id=999)
        client.chat = FakeChat()
        client.embeddings = FakeEmbeddings()
        client.retriever = FakeRetriever()
        client.response_judge = FakeResponseJudge(judge_result)
        client.policy_engine = ResponsePolicyEngine()
        return client

    async def test_non_candidate_does_not_call_judge_or_reply_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client = self.make_client(tmp, judge_result=True)
            channel = FakeChannel(2)
            message = make_message(content="혼잣말", channel=channel)

            await HixbotClient.on_message(client, message)

            self.assertEqual(client.response_judge.calls, 0)
            self.assertEqual(client.chat.calls, 0)
            self.assertEqual(channel.sent, [])
            client.store.close()

    async def test_semantic_candidate_responds_when_judge_allows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client = self.make_client(tmp, judge_result=True)
            channel = FakeChannel(2)
            timestamp = now_ts()
            client.store.append_buffer_message(
                guild_id=1,
                channel_id=2,
                author_id=20,
                author_name="user-20",
                content="오늘 뭐 하지?",
                created_at=timestamp,
            )
            client.store.append_buffer_message(
                guild_id=1,
                channel_id=2,
                author_id=21,
                author_name="user-21",
                content="가볍게 한 판?",
                created_at=timestamp,
            )
            message = make_message(content="그럼 뭐 할까", channel=channel, author_id=22)

            with patch.object(
                bot_module,
                "discord",
                SimpleNamespace(AllowedMentions=SimpleNamespace(none=lambda: None)),
            ):
                await HixbotClient.on_message(client, message)

            self.assertEqual(client.response_judge.calls, 1)
            self.assertEqual(client.chat.calls, 1)
            self.assertEqual(channel.sent, ["짧게 끼어들기"])
            client.store.close()

    async def test_semantic_candidate_stays_quiet_when_judge_rejects(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client = self.make_client(tmp, judge_result=False)
            channel = FakeChannel(2)
            timestamp = now_ts()
            client.store.append_buffer_message(
                guild_id=1,
                channel_id=2,
                author_id=20,
                author_name="user-20",
                content="오늘 뭐 하지?",
                created_at=timestamp,
            )
            client.store.append_buffer_message(
                guild_id=1,
                channel_id=2,
                author_id=21,
                author_name="user-21",
                content="가볍게 한 판?",
                created_at=timestamp,
            )
            message = make_message(content="그럼 뭐 할까", channel=channel, author_id=22)

            await HixbotClient.on_message(client, message)

            self.assertEqual(client.response_judge.calls, 1)
            self.assertEqual(client.chat.calls, 0)
            self.assertEqual(channel.sent, [])
            client.store.close()


if __name__ == "__main__":
    unittest.main()
