from __future__ import annotations

import asyncio
from typing import Protocol, Sequence

from .config import Settings
from .models import ChatResult, ChatTurn, HealthStatus

try:
    import aiohttp
except ImportError:  # pragma: no cover - exercised only in missing dependency installs
    aiohttp = None  # type: ignore[assignment]


class ChatProvider(Protocol):
    name: str

    async def complete(
        self,
        messages: Sequence[ChatTurn],
        *,
        temperature: float = 0.8,
        max_tokens: int = 512,
    ) -> ChatResult:
        ...

    async def health(self) -> HealthStatus:
        ...


class EmbeddingProvider(Protocol):
    name: str

    async def embed(self, text: str) -> list[float]:
        ...

    async def health(self) -> HealthStatus:
        ...


def _require_aiohttp() -> None:
    if aiohttp is None:
        raise RuntimeError("aiohttp is not installed. Run `pip install -e .` first.")


def _turns_to_payload(messages: Sequence[ChatTurn]) -> list[dict[str, str]]:
    payload: list[dict[str, str]] = []
    for message in messages:
        item = {"role": message.role, "content": message.content}
        if message.name:
            item["name"] = message.name
        payload.append(item)
    return payload


class OllamaChatProvider:
    name = "ollama"

    def __init__(self, base_url: str, model: str, timeout_seconds: int = 60) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds

    async def complete(
        self,
        messages: Sequence[ChatTurn],
        *,
        temperature: float = 0.8,
        max_tokens: int = 512,
    ) -> ChatResult:
        _require_aiohttp()
        payload = {
            "model": self.model,
            "messages": _turns_to_payload(messages),
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }
        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(f"{self.base_url}/api/chat", json=payload) as response:
                response.raise_for_status()
                data = await response.json()
        content = data.get("message", {}).get("content", "")
        if not content:
            raise RuntimeError("Ollama returned an empty response")
        return ChatResult(provider=self.name, content=content.strip())

    async def health(self) -> HealthStatus:
        _require_aiohttp()
        try:
            timeout = aiohttp.ClientTimeout(total=5)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(f"{self.base_url}/api/tags") as response:
                    response.raise_for_status()
            return HealthStatus(self.name, True, f"reachable: {self.model}")
        except Exception as exc:  # pragma: no cover - network dependent
            return HealthStatus(self.name, False, str(exc))


class OllamaEmbeddingProvider:
    name = "ollama-embed"

    def __init__(self, base_url: str, model: str, timeout_seconds: int = 60) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds

    async def embed(self, text: str) -> list[float]:
        _require_aiohttp()
        text = text.strip()
        if not text:
            raise ValueError("Cannot embed empty text")
        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            vector = await self._embed_new_api(session, text)
            if vector is None:
                vector = await self._embed_legacy_api(session, text)
        if not vector:
            raise RuntimeError("Ollama returned an empty embedding")
        return vector

    async def _embed_new_api(self, session: "aiohttp.ClientSession", text: str) -> list[float] | None:
        payload = {"model": self.model, "input": text}
        async with session.post(f"{self.base_url}/api/embed", json=payload) as response:
            if response.status == 404:
                return None
            response.raise_for_status()
            data = await response.json()
        embeddings = data.get("embeddings")
        if isinstance(embeddings, list) and embeddings:
            first = embeddings[0]
            if isinstance(first, list):
                return [float(value) for value in first]
        return None

    async def _embed_legacy_api(self, session: "aiohttp.ClientSession", text: str) -> list[float]:
        payload = {"model": self.model, "prompt": text}
        async with session.post(f"{self.base_url}/api/embeddings", json=payload) as response:
            response.raise_for_status()
            data = await response.json()
        return [float(value) for value in data.get("embedding", [])]

    async def health(self) -> HealthStatus:
        _require_aiohttp()
        try:
            vector = await asyncio.wait_for(self.embed("health check"), timeout=8)
            return HealthStatus(self.name, True, f"dimension={len(vector)} model={self.model}")
        except Exception as exc:  # pragma: no cover - network dependent
            return HealthStatus(self.name, False, str(exc))


class GeminiChatProvider:
    name = "gemini"

    def __init__(self, api_key: str, model: str, timeout_seconds: int = 60) -> None:
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.base_url = "https://generativelanguage.googleapis.com/v1beta/openai"

    async def complete(
        self,
        messages: Sequence[ChatTurn],
        *,
        temperature: float = 0.8,
        max_tokens: int = 512,
    ) -> ChatResult:
        _require_aiohttp()
        payload = {
            "model": self.model,
            "messages": _turns_to_payload(messages),
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                f"{self.base_url}/chat/completions",
                json=payload,
                headers=headers,
            ) as response:
                response.raise_for_status()
                data = await response.json()
        choices = data.get("choices", [])
        if not choices:
            raise RuntimeError("Gemini returned no choices")
        content = choices[0].get("message", {}).get("content", "")
        if not content:
            raise RuntimeError("Gemini returned an empty response")
        return ChatResult(provider=self.name, content=content.strip())

    async def health(self) -> HealthStatus:
        if not self.api_key:
            return HealthStatus(self.name, False, "GEMINI_API_KEY is missing")
        return HealthStatus(self.name, True, f"configured: {self.model}")


class FallbackChatProvider:
    name = "fallback"

    def __init__(self, primary: ChatProvider, fallback: ChatProvider | None) -> None:
        self.primary = primary
        self.fallback = fallback

    async def complete(
        self,
        messages: Sequence[ChatTurn],
        *,
        temperature: float = 0.8,
        max_tokens: int = 512,
    ) -> ChatResult:
        try:
            return await self.primary.complete(
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        except Exception as primary_exc:
            if self.fallback is None:
                raise
            try:
                result = await self.fallback.complete(
                    messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                return ChatResult(
                    provider=f"{result.provider} fallback",
                    content=result.content,
                )
            except Exception as fallback_exc:
                raise RuntimeError(
                    f"primary failed: {primary_exc}; fallback failed: {fallback_exc}"
                ) from fallback_exc

    async def health(self) -> HealthStatus:
        primary = await self.primary.health()
        if primary.ok:
            return primary
        if self.fallback is None:
            return primary
        fallback = await self.fallback.health()
        return HealthStatus(
            self.name,
            fallback.ok,
            f"primary={primary.detail}; fallback={fallback.detail}",
        )

    async def component_health(self) -> list[HealthStatus]:
        statuses = [await self.primary.health()]
        if self.fallback is not None:
            statuses.append(await self.fallback.health())
        return statuses


def build_chat_provider(settings: Settings) -> FallbackChatProvider:
    primary = _build_named_chat_provider(settings, settings.primary_provider)
    fallback = None
    if settings.fallback_provider:
        fallback = _build_named_chat_provider(settings, settings.fallback_provider, allow_missing=True)
    return FallbackChatProvider(primary, fallback)


def _build_named_chat_provider(
    settings: Settings,
    name: str,
    *,
    allow_missing: bool = False,
) -> ChatProvider | None:
    if name == "ollama":
        return OllamaChatProvider(settings.ollama_base_url, settings.ollama_chat_model)
    if name == "gemini":
        if settings.gemini_api_key:
            return GeminiChatProvider(settings.gemini_api_key, settings.gemini_model)
        if allow_missing:
            return None
        raise RuntimeError("GEMINI_API_KEY is required when PRIMARY_PROVIDER=gemini")
    raise RuntimeError(f"Unknown chat provider: {name}")


def build_embedding_provider(settings: Settings) -> EmbeddingProvider:
    return OllamaEmbeddingProvider(settings.ollama_base_url, settings.ollama_embed_model)
