from __future__ import annotations

from dataclasses import dataclass, field

from .models import ChannelPolicy, ResponseDecision


@dataclass
class ResponsePolicyEngine:
    channel_last_response_at: dict[tuple[int, int], int] = field(default_factory=dict)

    def decide(
        self,
        *,
        guild_id: int,
        channel_id: int,
        content: str,
        author_is_bot: bool,
        channel_policy: ChannelPolicy,
        is_muted: bool,
        bot_mentioned: bool,
        replied_to_bot: bool,
        now: int,
    ) -> ResponseDecision:
        if author_is_bot:
            return ResponseDecision(False, "author is bot")
        if not content.strip():
            return ResponseDecision(False, "empty message")
        if not channel_policy.observe_enabled:
            return ResponseDecision(False, "observation disabled")
        if not channel_policy.respond_enabled:
            return ResponseDecision(False, "response disabled")
        if is_muted:
            return ResponseDecision(False, "muted")

        bypass_cooldown = bot_mentioned or replied_to_bot
        key = (guild_id, channel_id)
        last_response = self.channel_last_response_at.get(key, 0)
        if not bypass_cooldown and now - last_response < channel_policy.cooldown_seconds:
            return ResponseDecision(False, "cooldown")

        if bot_mentioned:
            return ResponseDecision(True, "mentioned")
        if replied_to_bot:
            return ResponseDecision(True, "reply thread")
        if self._looks_interactive(content):
            return ResponseDecision(True, "interactive message")
        return ResponseDecision(False, "not worth interrupting")

    def record_response(self, *, guild_id: int, channel_id: int, now: int) -> None:
        self.channel_last_response_at[(guild_id, channel_id)] = now

    @staticmethod
    def _looks_interactive(content: str) -> bool:
        stripped = content.strip()
        if len(stripped) < 4:
            return False
        if stripped.endswith("?") or stripped.endswith("？"):
            return True
        korean_triggers = (
            "할까",
            "하실",
            "하냐",
            "하자",
            "갈까",
            "ㄱㄱ",
            "추천",
            "어때",
            "누구",
            "몇시",
            "모집",
            "파티",
        )
        lowered = stripped.lower()
        return any(trigger in lowered for trigger in korean_triggers)
