from __future__ import annotations

from .models import BufferedMessage
from .prompts import build_persona_update_messages
from .providers import ChatProvider
from .storage import SQLiteStore


class PersonaProfileUpdater:
    def __init__(self, *, store: SQLiteStore, chat: ChatProvider) -> None:
        self.store = store
        self.chat = chat

    async def update_from_messages(self, messages: list[BufferedMessage]) -> bool:
        if not messages:
            return False
        current = self.store.get_persona_profile()
        result = await self.chat.complete(
            build_persona_update_messages(
                existing_profile=current.profile_markdown if current else None,
                messages=messages,
            ),
            temperature=0.35,
            max_tokens=700,
        )
        profile = result.content.strip()
        if profile == "NO_PERSONA_UPDATE":
            return False
        self.store.save_persona_profile(
            profile,
            message_count_delta=len(messages),
        )
        return True
