from __future__ import annotations

import unittest

from hixbot.models import ChannelPolicy
from hixbot.policy import ResponsePolicyEngine


class ResponsePolicyEngineTests(unittest.TestCase):
    def test_ignores_bot_authors(self) -> None:
        engine = ResponsePolicyEngine()
        decision = engine.decide(
            guild_id=1,
            channel_id=2,
            content="같이 할까?",
            author_is_bot=True,
            channel_policy=ChannelPolicy(True, True, True, 30),
            is_muted=False,
            bot_mentioned=False,
            replied_to_bot=False,
            now=100,
        )
        self.assertFalse(decision.should_respond)
        self.assertEqual(decision.reason, "author is bot")

    def test_responds_to_interactive_message_then_respects_cooldown(self) -> None:
        engine = ResponsePolicyEngine()
        policy = ChannelPolicy(True, True, True, 30)
        first = engine.decide(
            guild_id=1,
            channel_id=2,
            content="발로 파티 모집 ㄱㄱ?",
            author_is_bot=False,
            channel_policy=policy,
            is_muted=False,
            bot_mentioned=False,
            replied_to_bot=False,
            now=100,
        )
        self.assertTrue(first.should_respond)
        engine.record_response(guild_id=1, channel_id=2, now=100)
        second = engine.decide(
            guild_id=1,
            channel_id=2,
            content="또 할까?",
            author_is_bot=False,
            channel_policy=policy,
            is_muted=False,
            bot_mentioned=False,
            replied_to_bot=False,
            now=110,
        )
        self.assertFalse(second.should_respond)
        self.assertEqual(second.reason, "cooldown")

    def test_mentions_bypass_cooldown(self) -> None:
        engine = ResponsePolicyEngine()
        engine.record_response(guild_id=1, channel_id=2, now=100)
        decision = engine.decide(
            guild_id=1,
            channel_id=2,
            content="hixbot 뭐해",
            author_is_bot=False,
            channel_policy=ChannelPolicy(True, True, True, 60),
            is_muted=False,
            bot_mentioned=True,
            replied_to_bot=False,
            now=101,
        )
        self.assertTrue(decision.should_respond)
        self.assertEqual(decision.reason, "mentioned")


if __name__ == "__main__":
    unittest.main()
