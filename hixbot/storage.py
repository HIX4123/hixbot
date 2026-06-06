from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

from .models import BufferedMessage, ChannelPolicy, LearnChannelProgress, LearnJob, PersonaProfile


def now_ts() -> int:
    return int(time.time())


class SQLiteStore:
    def __init__(
        self,
        path: Path,
        *,
        message_ttl_seconds: int,
        default_cooldown_seconds: int,
    ) -> None:
        self.path = path
        self.message_ttl_seconds = message_ttl_seconds
        self.default_cooldown_seconds = default_cooldown_seconds
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(self.path)
        self._connection.row_factory = sqlite3.Row
        self._init_schema()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def _init_schema(self) -> None:
        with self._lock:
            self._connection.executescript(
                """
                PRAGMA journal_mode=WAL;

                CREATE TABLE IF NOT EXISTS channel_config (
                    guild_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    observe_enabled INTEGER,
                    respond_enabled INTEGER,
                    remember_enabled INTEGER,
                    cooldown_seconds INTEGER,
                    updated_at INTEGER NOT NULL,
                    PRIMARY KEY (guild_id, channel_id)
                );

                CREATE TABLE IF NOT EXISTS mutes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    channel_id INTEGER,
                    expires_at INTEGER NOT NULL,
                    created_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS message_buffer (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    author_id INTEGER NOT NULL,
                    author_name TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_message_buffer_guild_id
                ON message_buffer (guild_id, id);

                CREATE INDEX IF NOT EXISTS idx_message_buffer_expires_at
                ON message_buffer (expires_at);

                CREATE TABLE IF NOT EXISTS summary_state (
                    guild_id INTEGER PRIMARY KEY,
                    last_message_id INTEGER NOT NULL DEFAULT 0,
                    updated_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER,
                    actor_id INTEGER,
                    action TEXT NOT NULL,
                    detail TEXT NOT NULL,
                    created_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS learn_jobs (
                    guild_id INTEGER PRIMARY KEY,
                    status TEXT NOT NULL,
                    current_channel_id INTEGER,
                    processed_messages INTEGER NOT NULL DEFAULT 0,
                    wiki_summaries INTEGER NOT NULL DEFAULT 0,
                    skipped_channels INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    started_at INTEGER,
                    stopped_at INTEGER,
                    completed_at INTEGER,
                    updated_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS learn_channel_progress (
                    guild_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    last_processed_message_id INTEGER,
                    completed_at INTEGER,
                    last_error TEXT,
                    updated_at INTEGER NOT NULL,
                    PRIMARY KEY (guild_id, channel_id)
                );

                CREATE TABLE IF NOT EXISTS learn_message_buffer (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    message_id INTEGER NOT NULL,
                    author_id INTEGER NOT NULL,
                    author_name TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL,
                    UNIQUE (guild_id, channel_id, message_id)
                );

                CREATE INDEX IF NOT EXISTS idx_learn_message_buffer_expires_at
                ON learn_message_buffer (expires_at);

                CREATE TABLE IF NOT EXISTS persona_profile (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    profile_markdown TEXT NOT NULL,
                    message_count INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                """
            )
            self._connection.commit()

    def resolve_channel_policy(self, guild_id: int, channel_id: int) -> ChannelPolicy:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT observe_enabled, respond_enabled, remember_enabled, cooldown_seconds
                FROM channel_config
                WHERE guild_id = ? AND channel_id = ?
                """,
                (guild_id, channel_id),
            ).fetchone()
        observe = True
        respond = True
        remember = True
        cooldown = self.default_cooldown_seconds
        if row:
            observe = bool(row["observe_enabled"]) if row["observe_enabled"] is not None else observe
            respond = bool(row["respond_enabled"]) if row["respond_enabled"] is not None else respond
            remember = bool(row["remember_enabled"]) if row["remember_enabled"] is not None else remember
            cooldown = row["cooldown_seconds"] or cooldown
        return ChannelPolicy(observe, respond, remember, cooldown)

    def set_channel_policy(
        self,
        guild_id: int,
        channel_id: int,
        *,
        observe_enabled: bool | None = None,
        respond_enabled: bool | None = None,
        remember_enabled: bool | None = None,
        cooldown_seconds: int | None = None,
    ) -> ChannelPolicy:
        existing = self.resolve_channel_policy(guild_id, channel_id)
        observe = existing.observe_enabled if observe_enabled is None else observe_enabled
        respond = existing.respond_enabled if respond_enabled is None else respond_enabled
        remember = existing.remember_enabled if remember_enabled is None else remember_enabled
        cooldown = existing.cooldown_seconds if cooldown_seconds is None else cooldown_seconds
        with self._lock:
            self._connection.execute(
                """
                INSERT INTO channel_config (
                    guild_id, channel_id, observe_enabled, respond_enabled,
                    remember_enabled, cooldown_seconds, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(guild_id, channel_id) DO UPDATE SET
                    observe_enabled = excluded.observe_enabled,
                    respond_enabled = excluded.respond_enabled,
                    remember_enabled = excluded.remember_enabled,
                    cooldown_seconds = excluded.cooldown_seconds,
                    updated_at = excluded.updated_at
                """,
                (
                    guild_id,
                    channel_id,
                    int(observe),
                    int(respond),
                    int(remember),
                    cooldown,
                    now_ts(),
                ),
            )
            self._connection.commit()
        return ChannelPolicy(observe, respond, remember, cooldown)

    def add_mute(self, guild_id: int, channel_id: int | None, duration_seconds: int) -> int:
        created = now_ts()
        expires = created + duration_seconds
        with self._lock:
            cursor = self._connection.execute(
                """
                INSERT INTO mutes (guild_id, channel_id, expires_at, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (guild_id, channel_id, expires, created),
            )
            self._connection.commit()
            return int(cursor.lastrowid)

    def is_muted(self, guild_id: int, channel_id: int, *, at: int | None = None) -> bool:
        current = now_ts() if at is None else at
        self.purge_expired_mutes(current)
        with self._lock:
            row = self._connection.execute(
                """
                SELECT 1
                FROM mutes
                WHERE guild_id = ?
                  AND expires_at > ?
                  AND (channel_id IS NULL OR channel_id = ?)
                LIMIT 1
                """,
                (guild_id, current, channel_id),
            ).fetchone()
        return row is not None

    def purge_expired_mutes(self, at: int | None = None) -> int:
        current = now_ts() if at is None else at
        with self._lock:
            cursor = self._connection.execute(
                "DELETE FROM mutes WHERE expires_at <= ?",
                (current,),
            )
            self._connection.commit()
            return cursor.rowcount

    def append_buffer_message(
        self,
        *,
        guild_id: int,
        channel_id: int,
        author_id: int,
        author_name: str,
        content: str,
        created_at: int | None = None,
    ) -> int:
        created = now_ts() if created_at is None else created_at
        expires = created + self.message_ttl_seconds
        with self._lock:
            cursor = self._connection.execute(
                """
                INSERT INTO message_buffer (
                    guild_id, channel_id, author_id, author_name,
                    content, created_at, expires_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (guild_id, channel_id, author_id, author_name, content, created, expires),
            )
            self._connection.commit()
            return int(cursor.lastrowid)

    def recent_messages(
        self,
        guild_id: int,
        channel_id: int | None = None,
        *,
        limit: int = 20,
        at: int | None = None,
    ) -> list[BufferedMessage]:
        current = now_ts() if at is None else at
        params: list[int] = [guild_id, current]
        channel_filter = ""
        if channel_id is not None:
            channel_filter = "AND channel_id = ?"
            params.append(channel_id)
        params.append(limit)
        with self._lock:
            rows = self._connection.execute(
                f"""
                SELECT id, guild_id, channel_id, author_id, author_name, content, created_at
                FROM message_buffer
                WHERE guild_id = ?
                  AND expires_at > ?
                  {channel_filter}
                ORDER BY id DESC
                LIMIT ?
                """,
                tuple(params),
            ).fetchall()
        return [self._row_to_message(row) for row in reversed(rows)]

    def unsummarized_messages(self, guild_id: int, *, limit: int = 80) -> list[BufferedMessage]:
        with self._lock:
            state = self._connection.execute(
                "SELECT last_message_id FROM summary_state WHERE guild_id = ?",
                (guild_id,),
            ).fetchone()
            last_id = int(state["last_message_id"]) if state else 0
            rows = self._connection.execute(
                """
                SELECT id, guild_id, channel_id, author_id, author_name, content, created_at
                FROM message_buffer
                WHERE guild_id = ?
                  AND id > ?
                  AND expires_at > ?
                ORDER BY id ASC
                LIMIT ?
                """,
                (guild_id, last_id, now_ts(), limit),
            ).fetchall()
        return [self._row_to_message(row) for row in rows]

    def mark_summarized(self, guild_id: int, last_message_id: int) -> None:
        with self._lock:
            self._connection.execute(
                """
                INSERT INTO summary_state (guild_id, last_message_id, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET
                    last_message_id = MAX(summary_state.last_message_id, excluded.last_message_id),
                    updated_at = excluded.updated_at
                """,
                (guild_id, last_message_id, now_ts()),
            )
            self._connection.commit()

    def purge_expired_messages(self, at: int | None = None) -> int:
        current = now_ts() if at is None else at
        with self._lock:
            cursor = self._connection.execute(
                "DELETE FROM message_buffer WHERE expires_at <= ?",
                (current,),
            )
            self._connection.commit()
            return cursor.rowcount

    def append_audit(
        self,
        *,
        guild_id: int | None,
        actor_id: int | None,
        action: str,
        detail: str,
    ) -> None:
        with self._lock:
            self._connection.execute(
                """
                INSERT INTO audit_log (guild_id, actor_id, action, detail, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (guild_id, actor_id, action, detail, now_ts()),
            )
            self._connection.commit()

    def get_learn_job(self, guild_id: int) -> LearnJob | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT guild_id, status, current_channel_id, processed_messages,
                       wiki_summaries, skipped_channels, last_error, started_at,
                       stopped_at, completed_at, updated_at
                FROM learn_jobs
                WHERE guild_id = ?
                """,
                (guild_id,),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_learn_job(row)

    def start_or_resume_learn_job(self, guild_id: int) -> tuple[LearnJob, bool]:
        current = self.get_learn_job(guild_id)
        if current and current.status == "running":
            return current, True
        timestamp = now_ts()
        if current is None:
            with self._lock:
                self._connection.execute(
                    """
                    INSERT INTO learn_jobs (
                        guild_id, status, current_channel_id, processed_messages,
                        wiki_summaries, skipped_channels, last_error, started_at,
                        stopped_at, completed_at, updated_at
                    )
                    VALUES (?, 'running', NULL, 0, 0, 0, NULL, ?, NULL, NULL, ?)
                    """,
                    (guild_id, timestamp, timestamp),
                )
                self._connection.commit()
        else:
            with self._lock:
                self._connection.execute(
                    """
                    UPDATE learn_jobs
                    SET status = 'running',
                        current_channel_id = NULL,
                        last_error = NULL,
                        stopped_at = NULL,
                        completed_at = NULL,
                        updated_at = ?
                    WHERE guild_id = ?
                    """,
                    (timestamp, guild_id),
                )
                self._connection.commit()
        job = self.get_learn_job(guild_id)
        if job is None:
            raise RuntimeError("learn job was not created")
        return job, False

    def request_learn_stop(self, guild_id: int) -> LearnJob | None:
        job = self.get_learn_job(guild_id)
        if job is None:
            return None
        if job.status == "running":
            timestamp = now_ts()
            with self._lock:
                self._connection.execute(
                    """
                    UPDATE learn_jobs
                    SET status = 'stop_requested',
                        updated_at = ?
                    WHERE guild_id = ?
                    """,
                    (timestamp, guild_id),
                )
                self._connection.commit()
            return self.get_learn_job(guild_id)
        return job

    def learn_stop_requested(self, guild_id: int) -> bool:
        job = self.get_learn_job(guild_id)
        return bool(job and job.status == "stop_requested")

    def mark_learn_job_idle(self, guild_id: int, *, last_error: str | None = None) -> None:
        timestamp = now_ts()
        with self._lock:
            self._connection.execute(
                """
                UPDATE learn_jobs
                SET status = 'idle',
                    current_channel_id = NULL,
                    last_error = ?,
                    stopped_at = ?,
                    updated_at = ?
                WHERE guild_id = ?
                """,
                (last_error, timestamp, timestamp, guild_id),
            )
            self._connection.commit()

    def mark_learn_job_completed(self, guild_id: int) -> None:
        timestamp = now_ts()
        with self._lock:
            self._connection.execute(
                """
                UPDATE learn_jobs
                SET status = 'completed',
                    current_channel_id = NULL,
                    last_error = NULL,
                    completed_at = ?,
                    updated_at = ?
                WHERE guild_id = ?
                """,
                (timestamp, timestamp, guild_id),
            )
            self._connection.commit()

    def mark_learn_job_failed(self, guild_id: int, error: str) -> None:
        timestamp = now_ts()
        with self._lock:
            self._connection.execute(
                """
                UPDATE learn_jobs
                SET status = 'failed',
                    current_channel_id = NULL,
                    last_error = ?,
                    updated_at = ?
                WHERE guild_id = ?
                """,
                (error, timestamp, guild_id),
            )
            self._connection.commit()

    def set_learn_current_channel(self, guild_id: int, channel_id: int | None) -> None:
        timestamp = now_ts()
        with self._lock:
            self._connection.execute(
                """
                UPDATE learn_jobs
                SET current_channel_id = ?,
                    updated_at = ?
                WHERE guild_id = ?
                """,
                (channel_id, timestamp, guild_id),
            )
            self._connection.commit()

    def increment_learn_counters(
        self,
        guild_id: int,
        *,
        processed_messages: int = 0,
        wiki_summaries: int = 0,
        skipped_channels: int = 0,
    ) -> None:
        timestamp = now_ts()
        with self._lock:
            self._connection.execute(
                """
                UPDATE learn_jobs
                SET processed_messages = processed_messages + ?,
                    wiki_summaries = wiki_summaries + ?,
                    skipped_channels = skipped_channels + ?,
                    updated_at = ?
                WHERE guild_id = ?
                """,
                (processed_messages, wiki_summaries, skipped_channels, timestamp, guild_id),
            )
            self._connection.commit()

    def set_learn_skipped_channels(self, guild_id: int, skipped_channels: int) -> None:
        timestamp = now_ts()
        with self._lock:
            self._connection.execute(
                """
                UPDATE learn_jobs
                SET skipped_channels = ?,
                    updated_at = ?
                WHERE guild_id = ?
                """,
                (skipped_channels, timestamp, guild_id),
            )
            self._connection.commit()

    def get_learn_channel_progress(
        self,
        guild_id: int,
        channel_id: int,
    ) -> LearnChannelProgress | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT guild_id, channel_id, last_processed_message_id,
                       completed_at, last_error, updated_at
                FROM learn_channel_progress
                WHERE guild_id = ? AND channel_id = ?
                """,
                (guild_id, channel_id),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_learn_progress(row)

    def get_learn_last_message_id(self, guild_id: int, channel_id: int) -> int | None:
        progress = self.get_learn_channel_progress(guild_id, channel_id)
        if progress is None:
            return None
        return progress.last_processed_message_id

    def mark_learn_channel_progress(
        self,
        guild_id: int,
        channel_id: int,
        last_processed_message_id: int | None,
        *,
        completed: bool = False,
        last_error: str | None = None,
    ) -> None:
        timestamp = now_ts()
        completed_at = timestamp if completed else None
        with self._lock:
            self._connection.execute(
                """
                INSERT INTO learn_channel_progress (
                    guild_id, channel_id, last_processed_message_id,
                    completed_at, last_error, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(guild_id, channel_id) DO UPDATE SET
                    last_processed_message_id = COALESCE(
                        excluded.last_processed_message_id,
                        learn_channel_progress.last_processed_message_id
                    ),
                    completed_at = excluded.completed_at,
                    last_error = excluded.last_error,
                    updated_at = excluded.updated_at
                """,
                (
                    guild_id,
                    channel_id,
                    last_processed_message_id,
                    completed_at,
                    last_error,
                    timestamp,
                ),
            )
            self._connection.commit()

    def count_completed_learn_channels(self, guild_id: int) -> int:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT COUNT(*) AS count
                FROM learn_channel_progress
                WHERE guild_id = ?
                  AND completed_at IS NOT NULL
                """,
                (guild_id,),
            ).fetchone()
        return int(row["count"]) if row else 0

    def learn_progress_stats(self, guild_id: int) -> tuple[int, int, int | None]:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT COUNT(*) AS channel_count,
                       SUM(CASE WHEN completed_at IS NOT NULL THEN 1 ELSE 0 END) AS completed_count,
                       MAX(last_processed_message_id) AS last_cursor
                FROM learn_channel_progress
                WHERE guild_id = ?
                """,
                (guild_id,),
            ).fetchone()
        if row is None:
            return (0, 0, None)
        return (
            int(row["channel_count"]),
            int(row["completed_count"] or 0),
            int(row["last_cursor"]) if row["last_cursor"] is not None else None,
        )

    def append_learn_buffer_message(
        self,
        *,
        guild_id: int,
        channel_id: int,
        message_id: int,
        author_id: int,
        author_name: str,
        content: str,
        created_at: int,
        ttl_seconds: int,
    ) -> None:
        expires = now_ts() + ttl_seconds
        with self._lock:
            self._connection.execute(
                """
                INSERT OR IGNORE INTO learn_message_buffer (
                    guild_id, channel_id, message_id, author_id, author_name,
                    content, created_at, expires_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    guild_id,
                    channel_id,
                    message_id,
                    author_id,
                    author_name,
                    content,
                    created_at,
                    expires,
                ),
            )
            self._connection.commit()

    def purge_expired_learn_messages(self, at: int | None = None) -> int:
        current = now_ts() if at is None else at
        with self._lock:
            cursor = self._connection.execute(
                "DELETE FROM learn_message_buffer WHERE expires_at <= ?",
                (current,),
            )
            self._connection.commit()
            return cursor.rowcount

    def get_persona_profile(self) -> PersonaProfile | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT profile_markdown, message_count, updated_at
                FROM persona_profile
                WHERE id = 1
                """
            ).fetchone()
        if row is None:
            return None
        return self._row_to_persona_profile(row)

    def save_persona_profile(
        self,
        profile_markdown: str,
        *,
        message_count_delta: int,
        updated_at: int | None = None,
    ) -> PersonaProfile:
        profile_markdown = profile_markdown.strip()
        if not profile_markdown:
            raise ValueError("profile_markdown must not be empty")
        if message_count_delta < 0:
            raise ValueError("message_count_delta must not be negative")
        timestamp = now_ts() if updated_at is None else updated_at
        with self._lock:
            self._connection.execute(
                """
                INSERT INTO persona_profile (id, profile_markdown, message_count, updated_at)
                VALUES (1, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    profile_markdown = excluded.profile_markdown,
                    message_count = persona_profile.message_count + excluded.message_count,
                    updated_at = excluded.updated_at
                """,
                (profile_markdown, message_count_delta, timestamp),
            )
            self._connection.commit()
        profile = self.get_persona_profile()
        if profile is None:
            raise RuntimeError("persona profile was not saved")
        return profile

    def reset_persona_profile(self) -> bool:
        with self._lock:
            cursor = self._connection.execute("DELETE FROM persona_profile WHERE id = 1")
            self._connection.commit()
            return cursor.rowcount > 0

    @staticmethod
    def _row_to_message(row: sqlite3.Row) -> BufferedMessage:
        return BufferedMessage(
            id=int(row["id"]),
            guild_id=int(row["guild_id"]),
            channel_id=int(row["channel_id"]),
            author_id=int(row["author_id"]),
            author_name=str(row["author_name"]),
            content=str(row["content"]),
            created_at=int(row["created_at"]),
        )

    @staticmethod
    def _row_to_learn_job(row: sqlite3.Row) -> LearnJob:
        return LearnJob(
            guild_id=int(row["guild_id"]),
            status=row["status"],
            current_channel_id=(
                int(row["current_channel_id"]) if row["current_channel_id"] is not None else None
            ),
            processed_messages=int(row["processed_messages"]),
            wiki_summaries=int(row["wiki_summaries"]),
            skipped_channels=int(row["skipped_channels"]),
            last_error=str(row["last_error"]) if row["last_error"] is not None else None,
            started_at=int(row["started_at"]) if row["started_at"] is not None else None,
            stopped_at=int(row["stopped_at"]) if row["stopped_at"] is not None else None,
            completed_at=int(row["completed_at"]) if row["completed_at"] is not None else None,
            updated_at=int(row["updated_at"]),
        )

    @staticmethod
    def _row_to_learn_progress(row: sqlite3.Row) -> LearnChannelProgress:
        return LearnChannelProgress(
            guild_id=int(row["guild_id"]),
            channel_id=int(row["channel_id"]),
            last_processed_message_id=(
                int(row["last_processed_message_id"])
                if row["last_processed_message_id"] is not None
                else None
            ),
            completed_at=int(row["completed_at"]) if row["completed_at"] is not None else None,
            last_error=str(row["last_error"]) if row["last_error"] is not None else None,
            updated_at=int(row["updated_at"]),
        )

    @staticmethod
    def _row_to_persona_profile(row: sqlite3.Row) -> PersonaProfile:
        return PersonaProfile(
            profile_markdown=str(row["profile_markdown"]),
            message_count=int(row["message_count"]),
            updated_at=int(row["updated_at"]),
        )
