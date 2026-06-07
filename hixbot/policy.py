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
        has_recent_context: bool = False,
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

        if bot_mentioned:
            return ResponseDecision(True, "mentioned")
        if replied_to_bot:
            return ResponseDecision(True, "reply thread")

        key = (guild_id, channel_id)
        last_response = self.channel_last_response_at.get(key, 0)
        if now - last_response < channel_policy.cooldown_seconds:
            return ResponseDecision(False, "cooldown")

        if not channel_policy.remember_enabled:
            return ResponseDecision(False, "context unavailable")
        if not has_recent_context:
            return ResponseDecision(False, "not enough recent context")
        return ResponseDecision(False, "semantic judgment", needs_judgment=True)

    def record_response(self, *, guild_id: int, channel_id: int, now: int) -> None:
        self.channel_last_response_at[(guild_id, channel_id)] = now
