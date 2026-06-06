from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


ChatRole = Literal["system", "user", "assistant"]
LearnJobStatus = Literal["idle", "running", "stop_requested", "completed", "failed"]


@dataclass(frozen=True)
class ChatTurn:
    role: ChatRole
    content: str
    name: str | None = None


@dataclass(frozen=True)
class ChatResult:
    provider: str
    content: str


@dataclass(frozen=True)
class HealthStatus:
    name: str
    ok: bool
    detail: str


@dataclass(frozen=True)
class ChannelPolicy:
    observe_enabled: bool
    respond_enabled: bool
    remember_enabled: bool
    cooldown_seconds: int


@dataclass(frozen=True)
class BufferedMessage:
    id: int
    guild_id: int
    channel_id: int
    author_id: int
    author_name: str
    content: str
    created_at: int


@dataclass(frozen=True)
class LearnJob:
    guild_id: int
    status: LearnJobStatus
    current_channel_id: int | None
    processed_messages: int
    wiki_summaries: int
    skipped_channels: int
    last_error: str | None
    started_at: int | None
    stopped_at: int | None
    completed_at: int | None
    updated_at: int


@dataclass(frozen=True)
class LearnChannelProgress:
    guild_id: int
    channel_id: int
    last_processed_message_id: int | None
    completed_at: int | None
    last_error: str | None
    updated_at: int


@dataclass(frozen=True)
class LearnSourceMessage:
    id: int
    guild_id: int
    channel_id: int
    author_id: int
    author_name: str
    content: str
    created_at: int
    author_is_bot: bool


@dataclass(frozen=True)
class PersonaProfile:
    profile_markdown: str
    message_count: int
    updated_at: int


@dataclass(frozen=True)
class WikiChunk:
    id: str
    guild_id: int
    heading: str
    text: str
    path: str


@dataclass(frozen=True)
class RetrievedChunk:
    chunk: WikiChunk
    score: float


@dataclass(frozen=True)
class ResponseDecision:
    should_respond: bool
    reason: str
