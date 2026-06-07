from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _parse_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if not value:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc


def _parse_guild_ids(value: str | None) -> tuple[int, ...]:
    if not value:
        return ()
    guild_ids: list[int] = []
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        guild_ids.append(int(part))
    return tuple(guild_ids)


@dataclass(frozen=True)
class Settings:
    discord_token: str
    discord_guild_ids: tuple[int, ...]
    bot_owner_ids: tuple[int, ...]
    data_dir: Path
    primary_provider: str
    fallback_provider: str | None
    ollama_base_url: str
    ollama_chat_model: str
    ollama_embed_model: str
    gemini_api_key: str | None
    gemini_model: str
    qdrant_url: str
    qdrant_collection_prefix: str
    message_ttl_seconds: int
    summary_interval_seconds: int
    min_summary_messages: int
    max_context_messages: int
    default_reply_cooldown_seconds: int
    max_response_chars: int
    response_context_window_seconds: int
    response_context_min_messages: int
    response_context_min_authors: int
    response_judge_max_context_messages: int
    learn_batch_messages: int
    learn_sleep_seconds: int
    learn_history_ttl_seconds: int

    @classmethod
    def from_env(cls) -> "Settings":
        token = os.getenv("DISCORD_TOKEN", "").strip()
        fallback = os.getenv("FALLBACK_PROVIDER", "gemini").strip().lower() or None
        gemini_key = os.getenv("GEMINI_API_KEY", "").strip() or None
        return cls(
            discord_token=token,
            discord_guild_ids=_parse_guild_ids(os.getenv("DISCORD_GUILD_IDS")),
            bot_owner_ids=_parse_guild_ids(os.getenv("BOT_OWNER_IDS")),
            data_dir=Path(os.getenv("DATA_DIR", "./data")).expanduser(),
            primary_provider=os.getenv("PRIMARY_PROVIDER", "ollama").strip().lower(),
            fallback_provider=fallback,
            ollama_base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/"),
            ollama_chat_model=os.getenv("OLLAMA_CHAT_MODEL", "qwen2.5:7b-instruct"),
            ollama_embed_model=os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text"),
            gemini_api_key=gemini_key,
            gemini_model=os.getenv("GEMINI_MODEL", "gemini-3.5-flash"),
            qdrant_url=os.getenv("QDRANT_URL", "http://localhost:6333").rstrip("/"),
            qdrant_collection_prefix=os.getenv("QDRANT_COLLECTION_PREFIX", "hixbot_wiki"),
            message_ttl_seconds=_parse_int("MESSAGE_TTL_SECONDS", 21600),
            summary_interval_seconds=_parse_int("SUMMARY_INTERVAL_SECONDS", 900),
            min_summary_messages=_parse_int("MIN_SUMMARY_MESSAGES", 20),
            max_context_messages=_parse_int("MAX_CONTEXT_MESSAGES", 20),
            default_reply_cooldown_seconds=_parse_int("DEFAULT_REPLY_COOLDOWN_SECONDS", 45),
            max_response_chars=_parse_int("MAX_RESPONSE_CHARS", 1800),
            response_context_window_seconds=_parse_int("RESPONSE_CONTEXT_WINDOW_SECONDS", 180),
            response_context_min_messages=_parse_int("RESPONSE_CONTEXT_MIN_MESSAGES", 3),
            response_context_min_authors=_parse_int("RESPONSE_CONTEXT_MIN_AUTHORS", 2),
            response_judge_max_context_messages=_parse_int("RESPONSE_JUDGE_MAX_CONTEXT_MESSAGES", 8),
            learn_batch_messages=_parse_int("LEARN_BATCH_MESSAGES", 50),
            learn_sleep_seconds=_parse_int("LEARN_SLEEP_SECONDS", 60),
            learn_history_ttl_seconds=_parse_int("LEARN_HISTORY_TTL_SECONDS", 21600),
        )

    def require_token(self) -> None:
        if not self.discord_token:
            raise RuntimeError("DISCORD_TOKEN is required")

    def is_bot_owner(self, user_id: int) -> bool:
        return user_id in self.bot_owner_ids
