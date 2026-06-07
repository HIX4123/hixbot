from __future__ import annotations

import unittest

from hixbot.models import BufferedMessage
from hixbot.prompts import build_persona_update_messages, build_reply_messages, build_response_judge_messages


class PromptTests(unittest.TestCase):
    def test_reply_prompt_includes_global_persona_profile(self) -> None:
        messages = build_reply_messages(
            recent_messages=[],
            wiki_context="관련 Wiki 항목 없음",
            persona_profile="- 리액션은 빠르게, 드립은 짧게.",
            current_author="tester",
            current_message="오늘 뭐함?",
        )
        self.assertIn("Global Hixbot persona profile", messages[1].content)
        self.assertIn("드립은 짧게", messages[1].content)

    def test_reply_prompt_allows_missing_persona_profile(self) -> None:
        messages = build_reply_messages(
            recent_messages=[],
            wiki_context="관련 Wiki 항목 없음",
            current_author="tester",
            current_message="오늘 뭐함?",
        )
        self.assertIn("No learned global persona profile yet.", messages[1].content)

    def test_persona_update_prompt_sets_global_and_privacy_rules(self) -> None:
        batch = [
            BufferedMessage(
                id=1,
                guild_id=10,
                channel_id=20,
                author_id=30,
                author_name="tester",
                content="ㄱㄱ 오늘도 억까 레전드",
                created_at=1000,
            )
        ]
        messages = build_persona_update_messages(
            existing_profile="- 기존 프로필",
            messages=batch,
        )
        content = messages[1].content
        self.assertIn("single global Hixbot persona profile", content)
        self.assertIn("shared across every server", content)
        self.assertIn("Do not store raw message logs", content)
        self.assertIn("sensitive personal information", content)
        self.assertIn("NO_PERSONA_UPDATE", content)

    def test_response_judge_prompt_requires_binary_output(self) -> None:
        batch = [
            BufferedMessage(
                id=1,
                guild_id=10,
                channel_id=20,
                author_id=30,
                author_name="tester",
                content="오늘 뭐 할까",
                created_at=1000,
            )
        ]
        messages = build_response_judge_messages(
            recent_messages=batch,
            persona_profile="- 드립은 짧게",
            current_author="tester",
            current_message="오늘 뭐 할까",
        )
        self.assertIn("RESPOND or STAY_QUIET", messages[1].content)
        self.assertIn("lone remark", messages[1].content)
        self.assertIn("Global Hixbot persona profile", messages[1].content)


if __name__ == "__main__":
    unittest.main()
