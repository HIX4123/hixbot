from __future__ import annotations

import logging

from .models import BufferedMessage
from .prompts import build_response_judge_messages
from .providers import ChatProvider


LOGGER = logging.getLogger(__name__)


class ResponseJudge:
    def __init__(self, *, chat: ChatProvider) -> None:
        self.chat = chat

    async def should_respond(
        self,
        *,
        recent_messages: list[BufferedMessage],
        persona_profile: str | None,
        current_author: str,
        current_message: str,
    ) -> bool:
        try:
            result = await self.chat.complete(
                build_response_judge_messages(
                    recent_messages=recent_messages,
                    persona_profile=persona_profile,
                    current_author=current_author,
                    current_message=current_message,
                ),
                temperature=0.0,
                max_tokens=8,
            )
        except Exception as exc:
            LOGGER.warning("Response judge failed: %s", exc)
            return False
        verdict = result.content.strip().upper()
        return verdict == "RESPOND"
