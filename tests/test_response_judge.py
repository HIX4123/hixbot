from __future__ import annotations

import unittest

from hixbot.models import BufferedMessage, ChatResult
from hixbot.response_judge import ResponseJudge


class FakeChat:
    def __init__(self, content: str | None = None, *, fail: bool = False) -> None:
        self.content = content or "STAY_QUIET"
        self.fail = fail
        self.calls = 0
        self.last_temperature: float | None = None
        self.last_max_tokens: int | None = None

    async def complete(self, messages, *, temperature=0.8, max_tokens=512):
        self.calls += 1
        self.last_temperature = temperature
        self.last_max_tokens = max_tokens
        if self.fail:
            raise RuntimeError("judge failed")
        return ChatResult(provider="fake", content=self.content)


def make_context() -> list[BufferedMessage]:
    return [
        BufferedMessage(
            id=1,
            guild_id=1,
            channel_id=2,
            author_id=10,
            author_name="a",
            content="오늘 뭐 하지?",
            created_at=100,
        ),
        BufferedMessage(
            id=2,
            guild_id=1,
            channel_id=2,
            author_id=11,
            author_name="b",
            content="가볍게 한 판?",
            created_at=101,
        ),
    ]


class ResponseJudgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_respond_verdict_returns_true(self) -> None:
        chat = FakeChat("RESPOND")
        judge = ResponseJudge(chat=chat)
        result = await judge.should_respond(
            recent_messages=make_context(),
            persona_profile="- 짧게 드립친다.",
            current_author="a",
            current_message="그럼 뭐 할까",
        )
        self.assertTrue(result)
        self.assertEqual(chat.last_temperature, 0.0)
        self.assertEqual(chat.last_max_tokens, 8)

    async def test_stay_quiet_and_malformed_verdict_return_false(self) -> None:
        quiet = ResponseJudge(chat=FakeChat("STAY_QUIET"))
        malformed = ResponseJudge(chat=FakeChat("아마 답해도 됨"))
        self.assertFalse(
            await quiet.should_respond(
                recent_messages=make_context(),
                persona_profile=None,
                current_author="a",
                current_message="음",
            )
        )
        self.assertFalse(
            await malformed.should_respond(
                recent_messages=make_context(),
                persona_profile=None,
                current_author="a",
                current_message="음",
            )
        )

    async def test_provider_failure_returns_false(self) -> None:
        judge = ResponseJudge(chat=FakeChat(fail=True))
        with self.assertLogs("hixbot.response_judge", level="WARNING"):
            result = await judge.should_respond(
                recent_messages=make_context(),
                persona_profile=None,
                current_author="a",
                current_message="음",
            )
        self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()
