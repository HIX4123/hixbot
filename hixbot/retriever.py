from __future__ import annotations

import uuid

from .models import HealthStatus, RetrievedChunk, WikiChunk
from .providers import EmbeddingProvider
from .wiki import WikiManager

try:
    import aiohttp
except ImportError:  # pragma: no cover
    aiohttp = None  # type: ignore[assignment]


class QdrantRetriever:
    def __init__(self, base_url: str, collection_prefix: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.collection_prefix = collection_prefix

    def collection_name(self, guild_id: int) -> str:
        return f"{self.collection_prefix}_{guild_id}"

    async def health(self) -> HealthStatus:
        if aiohttp is None:
            return HealthStatus("qdrant", False, "aiohttp is not installed")
        try:
            timeout = aiohttp.ClientTimeout(total=5)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(f"{self.base_url}/collections") as response:
                    response.raise_for_status()
            return HealthStatus("qdrant", True, "reachable")
        except Exception as exc:  # pragma: no cover - network dependent
            return HealthStatus("qdrant", False, str(exc))

    async def ensure_collection(self, guild_id: int, vector_size: int) -> None:
        if aiohttp is None:
            raise RuntimeError("aiohttp is not installed")
        payload = {"vectors": {"size": vector_size, "distance": "Cosine"}}
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.put(
                f"{self.base_url}/collections/{self.collection_name(guild_id)}",
                json=payload,
            ) as response:
                response.raise_for_status()

    async def upsert(self, guild_id: int, chunks: list[tuple[WikiChunk, list[float]]]) -> int:
        if aiohttp is None:
            raise RuntimeError("aiohttp is not installed")
        if not chunks:
            return 0
        points = []
        for chunk, vector in chunks:
            points.append(
                {
                    "id": str(uuid.uuid5(uuid.NAMESPACE_URL, chunk.id)),
                    "vector": vector,
                    "payload": {
                        "chunk_id": chunk.id,
                        "guild_id": chunk.guild_id,
                        "heading": chunk.heading,
                        "text": chunk.text,
                        "path": chunk.path,
                    },
                }
            )
        payload = {"points": points}
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.put(
                f"{self.base_url}/collections/{self.collection_name(guild_id)}/points",
                params={"wait": "true"},
                json=payload,
            ) as response:
                response.raise_for_status()
        return len(points)

    async def search(self, guild_id: int, vector: list[float], *, limit: int = 5) -> list[RetrievedChunk]:
        if aiohttp is None:
            raise RuntimeError("aiohttp is not installed")
        payload = {
            "vector": vector,
            "limit": limit,
            "with_payload": True,
        }
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                f"{self.base_url}/collections/{self.collection_name(guild_id)}/points/search",
                json=payload,
            ) as response:
                if response.status == 404:
                    return []
                response.raise_for_status()
                data = await response.json()
        results: list[RetrievedChunk] = []
        for item in data.get("result", []):
            payload = item.get("payload") or {}
            chunk = WikiChunk(
                id=str(payload.get("chunk_id", "")),
                guild_id=int(payload.get("guild_id", guild_id)),
                heading=str(payload.get("heading", "")),
                text=str(payload.get("text", "")),
                path=str(payload.get("path", "")),
            )
            results.append(RetrievedChunk(chunk=chunk, score=float(item.get("score", 0.0))))
        return results


class WikiIndexer:
    def __init__(
        self,
        wiki: WikiManager,
        retriever: QdrantRetriever,
        embeddings: EmbeddingProvider,
    ) -> None:
        self.wiki = wiki
        self.retriever = retriever
        self.embeddings = embeddings

    async def reindex_guild(self, guild_id: int) -> int:
        chunks = self.wiki.chunks(guild_id)
        if not chunks:
            return 0
        embedded: list[tuple[WikiChunk, list[float]]] = []
        for chunk in chunks:
            vector = await self.embeddings.embed(chunk.text)
            embedded.append((chunk, vector))
        await self.retriever.ensure_collection(guild_id, len(embedded[0][1]))
        return await self.retriever.upsert(guild_id, embedded)

    async def index_chunk(self, chunk: WikiChunk) -> int:
        vector = await self.embeddings.embed(chunk.text)
        await self.retriever.ensure_collection(chunk.guild_id, len(vector))
        return await self.retriever.upsert(chunk.guild_id, [(chunk, vector)])
