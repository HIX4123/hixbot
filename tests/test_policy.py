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

    def test_general_message_in_cooldown_does_not_need_judgment(self) -> None:
        engine = ResponsePolicyEngine()
        policy = ChannelPolicy(True, True, True, 30)
        engine.record_response(guild_id=1, channel_id=2, now=100)
        decision = engine.decide(
            guild_id=1,
            channel_id=2,
            content="또 할까?",
            author_is_bot=False,
            channel_policy=policy,
            is_muted=False,
            bot_mentioned=False,
            replied_to_bot=False,
            has_recent_context=True,
            now=110,
        )
        self.assertFalse(decision.should_respond)
        self.assertFalse(decision.needs_judgment)
        self.assertEqual(decision.reason, "cooldown")

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

    def test_reply_thread_bypasses_cooldown(self) -> None:
        engine = ResponsePolicyEngine()
        engine.record_response(guild_id=1, channel_id=2, now=100)
        decision = engine.decide(
            guild_id=1,
            channel_id=2,
            content="ㅋㅋ 그건 맞지",
            author_is_bot=False,
            channel_policy=ChannelPolicy(True, True, True, 60),
            is_muted=False,
            bot_mentioned=False,
            replied_to_bot=True,
            now=101,
        )
        self.assertTrue(decision.should_respond)
        self.assertEqual(decision.reason, "reply thread")

    def test_general_message_without_recent_context_stays_quiet_even_with_keywords(self) -> None:
        engine = ResponsePolicyEngine()
        decision = engine.decide(
            guild_id=1,
            channel_id=2,
            content="발로 파티 모집 ㄱㄱ?",
            author_is_bot=False,
            channel_policy=ChannelPolicy(True, True, True, 30),
            is_muted=False,
            bot_mentioned=False,
            replied_to_bot=False,
            has_recent_context=False,
            now=100,
        )
        self.assertFalse(decision.should_respond)
        self.assertFalse(decision.needs_judgment)
        self.assertEqual(decision.reason, "not enough recent context")

    def test_general_message_with_recent_context_needs_semantic_judgment(self) -> None:
        engine = ResponsePolicyEngine()
        decision = engine.decide(
            guild_id=1,
            channel_id=2,
            content="오늘 뭐하지",
            author_is_bot=False,
            channel_policy=ChannelPolicy(True, True, True, 30),
            is_muted=False,
            bot_mentioned=False,
            replied_to_bot=False,
            has_recent_context=True,
            now=100,
        )
        self.assertFalse(decision.should_respond)
        self.assertTrue(decision.needs_judgment)
        self.assertEqual(decision.reason, "semantic judgment")


if __name__ == "__main__":
    unittest.main()
