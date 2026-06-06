from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Protocol, Sequence

from .models import BufferedMessage, LearnSourceMessage
from .persona import PersonaProfileUpdater
from .prompts import build_history_learn_messages
from .providers import ChatProvider
from .retriever import WikiIndexer
from .storage import SQLiteStore
from .wiki import WikiManager


LOGGER = logging.getLogger(__name__)


class LearnChannelUnavailable(Exception):
    """Raised when a Discord history channel can no longer be read."""


class LearnChannelSource(Protocol):
    id: int
    name: str

    async def fetch_messages_after(
        self,
        after_message_id: int | None,
        *,
        limit: int,
    ) -> list[LearnSourceMessage]:
        ...


@dataclass(frozen=True)
class LearnRunConfig:
    batch_messages: int
    sleep_seconds: int
    history_ttl_seconds: int
    max_messages_per_channel: int | None = None


class LearnRunner:
    def __init__(
        self,
        *,
        store: SQLiteStore,
        chat: ChatProvider,
        wiki: WikiManager,
        indexer: WikiIndexer,
        config: LearnRunConfig,
        persona_updater: PersonaProfileUpdater | None = None,
    ) -> None:
        self.store = store
        self.chat = chat
        self.wiki = wiki
        self.indexer = indexer
        self.config = config
        self.persona_updater = persona_updater

    async def run_guild(self, guild_id: int, channels: Sequence[LearnChannelSource]) -> None:
        try:
            job = self.store.get_learn_job(guild_id)
            if job and job.status == "stop_requested":
                self.store.mark_learn_job_idle(guild_id, last_error="사용자 요청으로 중지됨")
                return
            self.store.start_or_resume_learn_job(guild_id)
            for channel in channels:
                if self.store.learn_stop_requested(guild_id):
                    self.store.mark_learn_job_idle(guild_id, last_error="사용자 요청으로 중지됨")
                    return
                await self._run_channel(guild_id, channel)
                job = self.store.get_learn_job(guild_id)
                if job and job.status == "idle":
                    return
            self.store.mark_learn_job_completed(guild_id)
        except Exception as exc:
            self.store.mark_learn_job_failed(guild_id, str(exc))
            raise

    async def _run_channel(self, guild_id: int, channel: LearnChannelSource) -> None:
        self.store.set_learn_current_channel(guild_id, channel.id)
        processed_in_channel = 0
        while True:
            if self.store.learn_stop_requested(guild_id):
                self.store.mark_learn_job_idle(guild_id, last_error="사용자 요청으로 중지됨")
                return

            after_id = self.store.get_learn_last_message_id(guild_id, channel.id)
            remaining = self._remaining_limit(processed_in_channel)
            if remaining is not None and remaining <= 0:
                self.store.mark_learn_channel_progress(guild_id, channel.id, after_id, completed=True)
                return
            batch_limit = self.config.batch_messages if remaining is None else min(self.config.batch_messages, remaining)
            try:
                raw_batch = await channel.fetch_messages_after(after_id, limit=batch_limit)
            except LearnChannelUnavailable as exc:
                self.store.mark_learn_channel_progress(
                    guild_id,
                    channel.id,
                    after_id,
                    completed=True,
                    last_error=str(exc),
                )
                self.store.increment_learn_counters(guild_id, skipped_channels=1)
                return
            if not raw_batch:
                self.store.mark_learn_channel_progress(guild_id, channel.id, after_id, completed=True)
                return

            raw_batch = sorted(raw_batch, key=lambda message: message.id)
            valid_messages = [
                message
                for message in raw_batch
                if not message.author_is_bot and message.content.strip()
            ]
            last_raw_id = raw_batch[-1].id
            if not valid_messages:
                self.store.mark_learn_channel_progress(guild_id, channel.id, last_raw_id)
                processed_in_channel += len(raw_batch)
                await self._sleep_if_needed()
                continue

            for message in valid_messages:
                self.store.append_learn_buffer_message(
                    guild_id=guild_id,
                    channel_id=channel.id,
                    message_id=message.id,
                    author_id=message.author_id,
                    author_name=message.author_name,
                    content=message.content,
                    created_at=message.created_at,
                    ttl_seconds=self.config.history_ttl_seconds,
                )

            buffered = [
                BufferedMessage(
                    id=message.id,
                    guild_id=message.guild_id,
                    channel_id=message.channel_id,
                    author_id=message.author_id,
                    author_name=message.author_name,
                    content=message.content,
                    created_at=message.created_at,
                )
                for message in valid_messages
            ]
            result = await self.chat.complete(
                build_history_learn_messages(buffered),
                temperature=0.25,
                max_tokens=700,
            )
            summary = result.content.strip()
            wrote_summary = False
            if summary != "NO_SUMMARY":
                chunk = self.wiki.append_history_summary(
                    guild_id,
                    summary,
                    channel_ids={channel.id},
                    message_count=len(valid_messages),
                    period_start=valid_messages[0].created_at,
                    period_end=valid_messages[-1].created_at,
                )
                await self.indexer.index_chunk(chunk)
                wrote_summary = True

            if self.persona_updater is not None:
                try:
                    await self.persona_updater.update_from_messages(buffered)
                except Exception as exc:
                    LOGGER.warning("Persona profile update failed during history learning: %s", exc)

            self.store.mark_learn_channel_progress(guild_id, channel.id, last_raw_id)
            self.store.increment_learn_counters(
                guild_id,
                processed_messages=len(valid_messages),
                wiki_summaries=1 if wrote_summary else 0,
            )
            processed_in_channel += len(raw_batch)

            if self.store.learn_stop_requested(guild_id):
                self.store.mark_learn_job_idle(guild_id, last_error="사용자 요청으로 중지됨")
                return
            await self._sleep_if_needed()

    def _remaining_limit(self, processed_in_channel: int) -> int | None:
        if self.config.max_messages_per_channel is None:
            return None
        return self.config.max_messages_per_channel - processed_in_channel

    async def _sleep_if_needed(self) -> None:
        if self.config.sleep_seconds > 0:
            await asyncio.sleep(self.config.sleep_seconds)
