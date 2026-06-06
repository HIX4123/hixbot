from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path

from .models import RetrievedChunk, WikiChunk


HEADING_RE = re.compile(r"^##\s+(.+?)\s*$")


class WikiManager:
    def __init__(self, data_dir: Path) -> None:
        self.root = data_dir / "wiki"
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for_guild(self, guild_id: int) -> Path:
        guild_dir = self.root / str(guild_id)
        guild_dir.mkdir(parents=True, exist_ok=True)
        path = guild_dir / "server.md"
        if not path.exists():
            path.write_text("# 서버 Wiki\n\n", encoding="utf-8")
        return path

    def append_summary(
        self,
        guild_id: int,
        summary: str,
        *,
        channel_ids: set[int],
    ) -> WikiChunk:
        summary = summary.strip()
        if not summary:
            raise ValueError("summary must not be empty")
        path = self.path_for_guild(guild_id)
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        title = f"{now} 대화 요약"
        channels = ", ".join(str(channel_id) for channel_id in sorted(channel_ids)) or "알 수 없음"
        entry = (
            f"## {title}\n\n"
            f"- 업데이트 시각: {now}\n"
            f"- 채널: {channels}\n\n"
            f"{summary}\n\n"
        )
        with path.open("a", encoding="utf-8") as file:
            file.write(entry)
        chunk = self._chunk_from_parts(guild_id, title, entry.strip(), str(path))
        return chunk

    def append_history_summary(
        self,
        guild_id: int,
        summary: str,
        *,
        channel_ids: set[int],
        message_count: int,
        period_start: int | None,
        period_end: int | None,
    ) -> WikiChunk:
        summary = summary.strip()
        if not summary:
            raise ValueError("summary must not be empty")
        path = self.path_for_guild(guild_id)
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        title = f"{now} 과거 대화 학습 요약"
        channels = ", ".join(str(channel_id) for channel_id in sorted(channel_ids)) or "알 수 없음"
        period = f"{self._format_timestamp(period_start)} ~ {self._format_timestamp(period_end)}"
        entry = (
            f"## {title}\n\n"
            f"- 출처: 과거 대화 학습\n"
            f"- 업데이트 시각: {now}\n"
            f"- 채널: {channels}\n"
            f"- 메시지 수: {message_count}\n"
            f"- 기간: {period}\n\n"
            f"{summary}\n\n"
        )
        with path.open("a", encoding="utf-8") as file:
            file.write(entry)
        return self._chunk_from_parts(guild_id, title, entry.strip(), str(path))

    def export_path(self, guild_id: int) -> Path:
        return self.path_for_guild(guild_id)

    def chunks(self, guild_id: int) -> list[WikiChunk]:
        path = self.path_for_guild(guild_id)
        text = path.read_text(encoding="utf-8")
        chunks: list[WikiChunk] = []
        current_heading: str | None = None
        current_lines: list[str] = []
        for line in text.splitlines():
            match = HEADING_RE.match(line)
            if match:
                if current_heading and current_lines:
                    body = "\n".join(current_lines).strip()
                    chunks.append(self._chunk_from_parts(guild_id, current_heading, body, str(path)))
                current_heading = match.group(1)
                current_lines = [line]
            elif current_heading:
                current_lines.append(line)
        if current_heading and current_lines:
            body = "\n".join(current_lines).strip()
            chunks.append(self._chunk_from_parts(guild_id, current_heading, body, str(path)))
        return chunks

    def delete(self, guild_id: int, target: str) -> int:
        target = target.strip().lower()
        if not target:
            raise ValueError("target must not be empty")
        path = self.path_for_guild(guild_id)
        lines = path.read_text(encoding="utf-8").splitlines()
        output: list[str] = []
        current: list[str] = []
        current_heading: str | None = None
        deleted = 0

        def flush() -> None:
            nonlocal deleted
            if not current:
                return
            heading_text = current_heading or ""
            body = "\n".join(current).strip()
            chunk_id = self._chunk_id(guild_id, heading_text, body)
            should_delete = target in heading_text.lower() or target == chunk_id
            if should_delete:
                deleted += 1
            else:
                output.extend(current)

        for line in lines:
            match = HEADING_RE.match(line)
            if match:
                flush()
                current = [line]
                current_heading = match.group(1)
            elif current:
                current.append(line)
            else:
                output.append(line)
        flush()

        text = "\n".join(output).rstrip() + "\n\n"
        path.write_text(text, encoding="utf-8")
        return deleted

    def _chunk_from_parts(self, guild_id: int, heading: str, text: str, path: str) -> WikiChunk:
        return WikiChunk(
            id=self._chunk_id(guild_id, heading, text),
            guild_id=guild_id,
            heading=heading,
            text=text,
            path=path,
        )

    @staticmethod
    def _chunk_id(guild_id: int, heading: str, text: str) -> str:
        digest = hashlib.sha256(f"{guild_id}\n{heading}\n{text}".encode("utf-8")).hexdigest()
        return digest[:32]

    @staticmethod
    def _format_timestamp(timestamp: int | None) -> str:
        if timestamp is None:
            return "알 수 없음"
        return datetime.fromtimestamp(timestamp, timezone.utc).replace(microsecond=0).isoformat()


def format_retrieved_context(chunks: list[RetrievedChunk], *, max_chars: int = 1800) -> str:
    if not chunks:
        return "관련 Wiki 항목을 찾지 못했습니다."
    parts: list[str] = []
    remaining = max_chars
    for item in chunks:
        excerpt = item.chunk.text.strip()
        if len(excerpt) > remaining:
            excerpt = excerpt[: max(0, remaining - 3)] + "..."
        parts.append(f"[{item.chunk.heading} score={item.score:.2f}]\n{excerpt}")
        remaining -= len(excerpt)
        if remaining <= 0:
            break
    return "\n\n".join(parts)
