from __future__ import annotations

import asyncio
import logging
from typing import Literal

from .config import Settings
from .learning import LearnChannelSource, LearnChannelUnavailable, LearnRunConfig, LearnRunner
from .models import LearnSourceMessage
from .policy import ResponsePolicyEngine
from .prompts import build_reply_messages, build_summary_messages
from .providers import build_chat_provider, build_embedding_provider
from .retriever import QdrantRetriever, WikiIndexer
from .storage import SQLiteStore, now_ts
from .wiki import WikiManager, format_retrieved_context

try:
    import discord
    from discord import app_commands
except ImportError:  # pragma: no cover - only relevant before dependencies are installed
    discord = None  # type: ignore[assignment]
    app_commands = None  # type: ignore[assignment]


LOGGER = logging.getLogger(__name__)
_DiscordClientBase = discord.Client if discord is not None else object


class HixbotClient(_DiscordClientBase):  # type: ignore[misc, valid-type]
    def __init__(self, settings: Settings) -> None:
        if discord is None or app_commands is None:
            raise RuntimeError("discord.py is not installed. Run `pip install -e .` first.")
        intents = discord.Intents.default()
        intents.message_content = True
        intents.guilds = True
        intents.messages = True
        super().__init__(intents=intents)

        self.settings = settings
        self.tree = app_commands.CommandTree(self)
        self.store = SQLiteStore(
            settings.data_dir / "hixbot.sqlite3",
            message_ttl_seconds=settings.message_ttl_seconds,
            default_cooldown_seconds=settings.default_reply_cooldown_seconds,
        )
        self.chat = build_chat_provider(settings)
        self.embeddings = build_embedding_provider(settings)
        self.wiki = WikiManager(settings.data_dir)
        self.retriever = QdrantRetriever(settings.qdrant_url, settings.qdrant_collection_prefix)
        self.indexer = WikiIndexer(self.wiki, self.retriever, self.embeddings)
        self.policy_engine = ResponsePolicyEngine()
        self._summary_task: asyncio.Task[None] | None = None
        self._learn_tasks: dict[int, asyncio.Task[None]] = {}

    async def setup_hook(self) -> None:
        self._register_commands()
        if self.settings.discord_guild_ids:
            for guild_id in self.settings.discord_guild_ids:
                guild = discord.Object(id=guild_id)
                self.tree.copy_global_to(guild=guild)
                await self.tree.sync(guild=guild)
                LOGGER.info("Synced commands to guild %s", guild_id)
        else:
            await self.tree.sync()
            LOGGER.info("Synced global commands")
        self._summary_task = asyncio.create_task(self._summary_loop())

    async def close(self) -> None:
        if self._summary_task:
            self._summary_task.cancel()
        for task in self._learn_tasks.values():
            task.cancel()
        self.store.close()
        await super().close()

    async def on_ready(self) -> None:
        LOGGER.info("Logged in as %s", self.user)

    async def on_message(self, message: "discord.Message") -> None:
        if message.guild is None or self.user is None:
            return
        content = (message.content or "").strip()
        guild_id = int(message.guild.id)
        channel_id = int(message.channel.id)
        policy = self.store.resolve_channel_policy(guild_id, channel_id)

        if message.author.bot:
            return
        if not policy.observe_enabled:
            return
        if policy.remember_enabled and content:
            self.store.append_buffer_message(
                guild_id=guild_id,
                channel_id=channel_id,
                author_id=int(message.author.id),
                author_name=message.author.display_name,
                content=content,
            )

        bot_mentioned = any(user.id == self.user.id for user in message.mentions)
        replied_to_bot = bool(message.reference and message.reference.resolved and getattr(message.reference.resolved.author, "id", None) == self.user.id)
        decision = self.policy_engine.decide(
            guild_id=guild_id,
            channel_id=channel_id,
            content=content,
            author_is_bot=message.author.bot,
            channel_policy=policy,
            is_muted=self.store.is_muted(guild_id, channel_id),
            bot_mentioned=bot_mentioned,
            replied_to_bot=replied_to_bot,
            now=now_ts(),
        )
        if not decision.should_respond:
            return

        try:
            async with message.channel.typing():
                reply = await self._build_reply(message, guild_id, channel_id, content)
            if reply.lower().strip() == "[stay quiet]":
                return
            reply = reply[: self.settings.max_response_chars]
            await message.channel.send(
                reply,
                reference=message,
                mention_author=False,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            self.policy_engine.record_response(
                guild_id=guild_id,
                channel_id=channel_id,
                now=now_ts(),
            )
        except Exception:
            LOGGER.exception("Failed to respond to Discord message")

    async def _build_reply(
        self,
        message: "discord.Message",
        guild_id: int,
        channel_id: int,
        content: str,
    ) -> str:
        recent = self.store.recent_messages(
            guild_id,
            channel_id,
            limit=self.settings.max_context_messages,
        )
        try:
            query_vector = await self.embeddings.embed(content)
            retrieved = await self.retriever.search(guild_id, query_vector, limit=5)
        except Exception as exc:
            LOGGER.warning("Wiki retrieval failed: %s", exc)
            retrieved = []
        messages = build_reply_messages(
            recent_messages=recent,
            wiki_context=format_retrieved_context(retrieved),
            current_author=message.author.display_name,
            current_message=content,
        )
        result = await self.chat.complete(messages, temperature=0.8, max_tokens=512)
        return result.content

    async def _summary_loop(self) -> None:
        while True:
            await asyncio.sleep(self.settings.summary_interval_seconds)
            try:
                guild_ids = self.settings.discord_guild_ids or tuple(guild.id for guild in self.guilds)
                for guild_id in guild_ids:
                    await self._summarize_guild(int(guild_id))
                self.store.purge_expired_messages()
                self.store.purge_expired_mutes()
            except asyncio.CancelledError:
                raise
            except Exception:
                LOGGER.exception("Summary loop failed")

    async def _summarize_guild(self, guild_id: int) -> None:
        messages = self.store.unsummarized_messages(guild_id, limit=80)
        if len(messages) < self.settings.min_summary_messages:
            return
        result = await self.chat.complete(
            build_summary_messages(messages),
            temperature=0.3,
            max_tokens=700,
        )
        summary = result.content.strip()
        max_id = max(message.id for message in messages)
        if summary == "NO_SUMMARY":
            self.store.mark_summarized(guild_id, max_id)
            return
        channel_ids = {message.channel_id for message in messages}
        self.wiki.append_summary(guild_id, summary, channel_ids=channel_ids)
        try:
            await self.indexer.reindex_guild(guild_id)
        except Exception as exc:
            LOGGER.warning("Wiki reindex failed: %s", exc)
        self.store.mark_summarized(guild_id, max_id)

    def _register_commands(self) -> None:
        hix = app_commands.Group(name="hix", description="Hixbot controls")
        wiki = app_commands.Group(name="wiki", description="Server Wiki controls")
        learn = app_commands.Group(name="learn", description="과거 대화 학습")

        @hix.command(name="status", description="Show Hixbot component status.")
        async def status(interaction: "discord.Interaction") -> None:
            await interaction.response.defer(ephemeral=True)
            chat_statuses = await self.chat.component_health()
            embed_status = await self.embeddings.health()
            qdrant_status = await self.retriever.health()
            rows = [
                f"Discord: ok ({self.user})",
                *[f"{item.name}: {'ok' if item.ok else 'fail'} - {item.detail}" for item in chat_statuses],
                f"{embed_status.name}: {'ok' if embed_status.ok else 'fail'} - {embed_status.detail}",
                f"{qdrant_status.name}: {'ok' if qdrant_status.ok else 'fail'} - {qdrant_status.detail}",
            ]
            await interaction.followup.send("\n".join(rows), ephemeral=True)

        @hix.command(name="mute", description="Mute Hixbot for this channel or server.")
        @app_commands.default_permissions(manage_guild=True)
        async def mute(
            interaction: "discord.Interaction",
            scope: Literal["channel", "server"],
            duration_minutes: app_commands.Range[int, 1, 1440],
        ) -> None:
            if interaction.guild is None or interaction.channel is None:
                await interaction.response.send_message("서버 채널에서만 사용할 수 있어요.", ephemeral=True)
                return
            channel_id = None if scope == "server" else int(interaction.channel.id)
            self.store.add_mute(int(interaction.guild.id), channel_id, int(duration_minutes) * 60)
            self.store.append_audit(
                guild_id=int(interaction.guild.id),
                actor_id=int(interaction.user.id),
                action="mute",
                detail=f"scope={scope} duration_minutes={duration_minutes}",
            )
            await interaction.response.send_message(
                f"{scope} 범위로 {duration_minutes}분 동안 조용히 있을게요.",
                ephemeral=True,
            )

        @hix.command(name="config", description="Configure Hixbot for this channel.")
        @app_commands.default_permissions(manage_guild=True)
        async def config(
            interaction: "discord.Interaction",
            observe: bool | None = None,
            respond: bool | None = None,
            remember: bool | None = None,
            cooldown_seconds: app_commands.Range[int, 5, 3600] | None = None,
        ) -> None:
            if interaction.guild is None or interaction.channel is None:
                await interaction.response.send_message("서버 채널에서만 사용할 수 있어요.", ephemeral=True)
                return
            policy = self.store.set_channel_policy(
                int(interaction.guild.id),
                int(interaction.channel.id),
                observe_enabled=observe,
                respond_enabled=respond,
                remember_enabled=remember,
                cooldown_seconds=int(cooldown_seconds) if cooldown_seconds is not None else None,
            )
            self.store.append_audit(
                guild_id=int(interaction.guild.id),
                actor_id=int(interaction.user.id),
                action="config",
                detail=str(policy),
            )
            await interaction.response.send_message(
                (
                    "현재 채널 설정: "
                    f"observe={policy.observe_enabled}, "
                    f"respond={policy.respond_enabled}, "
                    f"remember={policy.remember_enabled}, "
                    f"cooldown={policy.cooldown_seconds}s"
                ),
                ephemeral=True,
            )

        @learn.command(name="start", description="서버 전체 과거 대화 학습을 이어서 시작합니다.")
        @app_commands.default_permissions(manage_guild=True)
        async def learn_start(
            interaction: "discord.Interaction",
            batch_messages: app_commands.Range[int, 1, 200] | None = None,
            sleep_seconds: app_commands.Range[int, 0, 3600] | None = None,
            max_messages_per_channel: app_commands.Range[int, 1, 100000] | None = None,
        ) -> None:
            if interaction.guild is None:
                await interaction.response.send_message("서버에서만 사용할 수 있어요.", ephemeral=True)
                return
            guild_id = int(interaction.guild.id)
            task = self._learn_tasks.get(guild_id)
            if task is not None and not task.done():
                await interaction.response.send_message(
                    self._format_learn_status(guild_id, prefix="이미 학습 작업이 실행 중이에요."),
                    ephemeral=True,
                )
                return

            self.store.start_or_resume_learn_job(guild_id)
            sources, skipped = self._build_learn_sources(interaction.guild)
            self.store.set_learn_skipped_channels(guild_id, skipped)
            if not sources:
                self.store.mark_learn_job_failed(guild_id, "읽을 수 있는 텍스트 채널이 없습니다.")
                await interaction.response.send_message("읽을 수 있는 텍스트 채널이 없어요.", ephemeral=True)
                return

            config = LearnRunConfig(
                batch_messages=int(batch_messages or self.settings.learn_batch_messages),
                sleep_seconds=int(sleep_seconds if sleep_seconds is not None else self.settings.learn_sleep_seconds),
                history_ttl_seconds=self.settings.learn_history_ttl_seconds,
                max_messages_per_channel=(
                    int(max_messages_per_channel) if max_messages_per_channel is not None else None
                ),
            )
            runner = LearnRunner(
                store=self.store,
                chat=self.chat,
                wiki=self.wiki,
                indexer=self.indexer,
                config=config,
            )
            task = asyncio.create_task(runner.run_guild(guild_id, sources))
            task.add_done_callback(lambda done_task, gid=guild_id: self._on_learn_task_done(gid, done_task))
            self._learn_tasks[guild_id] = task
            self.store.append_audit(
                guild_id=guild_id,
                actor_id=int(interaction.user.id),
                action="learn_start",
                detail=(
                    f"batch_messages={config.batch_messages} "
                    f"sleep_seconds={config.sleep_seconds} "
                    f"max_messages_per_channel={config.max_messages_per_channel}"
                ),
            )
            await interaction.response.send_message(
                "과거 대화 학습을 이어서 시작했어요. 이전 cursor 이후부터 천천히 읽습니다.",
                ephemeral=True,
            )

        @learn.command(name="status", description="과거 대화 학습 상태를 확인합니다.")
        @app_commands.default_permissions(manage_guild=True)
        async def learn_status(interaction: "discord.Interaction") -> None:
            if interaction.guild is None:
                await interaction.response.send_message("서버에서만 사용할 수 있어요.", ephemeral=True)
                return
            await interaction.response.send_message(
                self._format_learn_status(int(interaction.guild.id)),
                ephemeral=True,
            )

        @learn.command(name="stop", description="현재 batch 이후 과거 대화 학습을 안전하게 멈춥니다.")
        @app_commands.default_permissions(manage_guild=True)
        async def learn_stop(interaction: "discord.Interaction") -> None:
            if interaction.guild is None:
                await interaction.response.send_message("서버에서만 사용할 수 있어요.", ephemeral=True)
                return
            guild_id = int(interaction.guild.id)
            job = self.store.request_learn_stop(guild_id)
            if job is None or job.status not in {"running", "stop_requested"}:
                await interaction.response.send_message(
                    "실행 중인 과거 대화 학습 작업이 없어요. 저장된 cursor는 그대로 유지됩니다.",
                    ephemeral=True,
                )
                return
            self.store.append_audit(
                guild_id=guild_id,
                actor_id=int(interaction.user.id),
                action="learn_stop",
                detail="stop_requested",
            )
            await interaction.response.send_message(
                "중지 요청을 받았어요. 현재 batch를 정리한 뒤 멈추고, 다음 start는 이어서 시작합니다.",
                ephemeral=True,
            )

        @wiki.command(name="search", description="Search the server Wiki.")
        async def wiki_search(interaction: "discord.Interaction", query: str) -> None:
            if interaction.guild is None:
                await interaction.response.send_message("서버에서만 사용할 수 있어요.", ephemeral=True)
                return
            await interaction.response.defer(ephemeral=True)
            try:
                vector = await self.embeddings.embed(query)
                results = await self.retriever.search(int(interaction.guild.id), vector, limit=5)
                text = format_retrieved_context(results, max_chars=1800)
            except Exception as exc:
                LOGGER.warning("Wiki search failed: %s", exc)
                text = "Wiki 검색에 실패했어요. Qdrant/Ollama 상태를 `/hix status`로 확인해 주세요."
            await interaction.followup.send(text, ephemeral=True)

        @wiki.command(name="export", description="Export the server Wiki Markdown file.")
        async def wiki_export(interaction: "discord.Interaction") -> None:
            if interaction.guild is None:
                await interaction.response.send_message("서버에서만 사용할 수 있어요.", ephemeral=True)
                return
            path = self.wiki.export_path(int(interaction.guild.id))
            await interaction.response.send_message(
                "현재 서버 Wiki예요.",
                file=discord.File(path),
                ephemeral=True,
            )

        @wiki.command(name="delete", description="Delete matching Wiki entries and reindex.")
        @app_commands.default_permissions(manage_guild=True)
        async def wiki_delete(interaction: "discord.Interaction", target: str) -> None:
            if interaction.guild is None:
                await interaction.response.send_message("서버에서만 사용할 수 있어요.", ephemeral=True)
                return
            await interaction.response.defer(ephemeral=True)
            deleted = self.wiki.delete(int(interaction.guild.id), target)
            try:
                await self.indexer.reindex_guild(int(interaction.guild.id))
            except Exception as exc:
                LOGGER.warning("Wiki reindex after delete failed: %s", exc)
            self.store.append_audit(
                guild_id=int(interaction.guild.id),
                actor_id=int(interaction.user.id),
                action="wiki_delete",
                detail=f"target={target} deleted={deleted}",
            )
            await interaction.followup.send(f"{deleted}개 Wiki 항목을 삭제했어요.", ephemeral=True)

        hix.add_command(wiki)
        hix.add_command(learn)
        self.tree.add_command(hix)

    def _build_learn_sources(self, guild: "discord.Guild") -> tuple[list[LearnChannelSource], int]:
        member = guild.me
        if member is None and self.user is not None:
            member = guild.get_member(self.user.id)
        sources: list[LearnChannelSource] = []
        skipped = 0
        for channel in sorted(guild.text_channels, key=lambda item: (item.position, item.id)):
            if member is None:
                skipped += 1
                continue
            permissions = channel.permissions_for(member)
            if not permissions.view_channel or not permissions.read_message_history:
                skipped += 1
                continue
            sources.append(_DiscordLearnChannelSource(channel))
        return sources, skipped

    def _format_learn_status(self, guild_id: int, *, prefix: str | None = None) -> str:
        job = self.store.get_learn_job(guild_id)
        if job is None:
            return "아직 과거 대화 학습 작업이 없어요. `/hix learn start`로 시작할 수 있습니다."
        channel_count, completed_count, last_cursor = self.store.learn_progress_stats(guild_id)
        lines = []
        if prefix:
            lines.append(prefix)
        lines.extend(
            [
                f"상태: {job.status}",
                f"현재 채널: {job.current_channel_id or '없음'}",
                f"처리 메시지 수: {job.processed_messages}",
                f"작성한 Wiki 요약 수: {job.wiki_summaries}",
                f"건너뛴 채널 수: {job.skipped_channels}",
                f"완료 채널 수: {completed_count}/{channel_count}",
                f"마지막 cursor: {last_cursor or '없음'}",
                f"마지막 오류: {job.last_error or '없음'}",
                "다음 `/hix learn start`는 저장된 cursor 이후부터 이어서 시작합니다.",
            ]
        )
        return "\n".join(lines)

    def _on_learn_task_done(self, guild_id: int, task: asyncio.Task[None]) -> None:
        if self._learn_tasks.get(guild_id) is task:
            self._learn_tasks.pop(guild_id, None)
        if task.cancelled():
            return
        try:
            task.result()
        except Exception:
            LOGGER.exception("Learn task failed for guild %s", guild_id)


class _DiscordLearnChannelSource:
    def __init__(self, channel: "discord.TextChannel") -> None:
        self.channel = channel
        self.id = int(channel.id)
        self.name = channel.name

    async def fetch_messages_after(
        self,
        after_message_id: int | None,
        *,
        limit: int,
    ) -> list[LearnSourceMessage]:
        after = discord.Object(id=after_message_id) if after_message_id is not None else None
        messages: list[LearnSourceMessage] = []
        try:
            async for message in self.channel.history(
                limit=limit,
                after=after,
                oldest_first=True,
            ):
                if message.guild is None:
                    continue
                messages.append(
                    LearnSourceMessage(
                        id=int(message.id),
                        guild_id=int(message.guild.id),
                        channel_id=int(message.channel.id),
                        author_id=int(message.author.id),
                        author_name=message.author.display_name,
                        content=(message.content or "").strip(),
                        created_at=int(message.created_at.timestamp()),
                        author_is_bot=message.author.bot,
                    )
                )
        except (discord.Forbidden, discord.NotFound) as exc:
            raise LearnChannelUnavailable(f"{self.id} 채널을 읽을 수 없습니다: {exc}") from exc
        return messages


async def run_bot() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = Settings.from_env()
    settings.require_token()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    client = HixbotClient(settings)
    await client.start(settings.discord_token)
