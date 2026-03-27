"""
Tantra AI — Memory Manager
4-layer memory architecture:
  1. Short-term  — rolling context window (agent._history)
  2. Long-term   — mem0 (auto-extract facts, preferences, patterns)
  3. Episodic    — PostgreSQL event log (what happened, when, result)
  4. Semantic    — Qdrant vector store (similarity search across all knowledge)

Leaders READ all subordinate namespaces.
Workers READ/WRITE only their own namespace.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    Distance,
    PointStruct,
    VectorParams,
)

from tantra.core.config import settings
from tantra.core.llm import embed

logger = logging.getLogger(__name__)

# Qdrant collection naming convention: tantra_{namespace}
COLLECTION_PREFIX = settings.qdrant_collection_prefix


def _collection_name(namespace: str) -> str:
    return f"{COLLECTION_PREFIX}_{namespace.replace(':', '_').replace(' ', '_')}"


class MemoryManager:
    """
    Central memory manager for a single agent namespace.

    Usage:
        mem = MemoryManager(namespace="agent:cmo")
        await mem.init()

        await mem.save("User prefers data-backed LinkedIn posts")
        results = await mem.search("What content style does the user prefer?")
    """

    def __init__(self, namespace: str) -> None:
        self.namespace = namespace
        self.collection = _collection_name(namespace)
        self._client: AsyncQdrantClient | None = None

    async def _get_client(self) -> AsyncQdrantClient:
        if self._client is None:
            self._client = AsyncQdrantClient(
                host=settings.qdrant_host,
                port=settings.qdrant_port,
            )
        return self._client

    async def init(self) -> None:
        """Ensure the Qdrant collection exists (idempotent)."""
        client = await self._get_client()
        collections = await client.get_collections()
        existing = {c.name for c in collections.collections}

        if self.collection not in existing:
            await client.create_collection(
                collection_name=self.collection,
                vectors_config=VectorParams(
                    size=settings.embed_dim,
                    distance=Distance.COSINE,
                ),
            )
            logger.info(f"Created Qdrant collection: {self.collection}")

    async def save(
        self,
        content: str,
        metadata: Optional[dict[str, Any]] = None,
        point_id: Optional[str] = None,
    ) -> str:
        """
        Embed and store a memory chunk.

        Args:
            content:  The text to remember.
            metadata: Optional key-value metadata (agent, task, timestamp, etc.).
            point_id: Optional stable ID (for upsert / deduplication).

        Returns:
            The point ID used.
        """
        import uuid as _uuid

        client = await self._get_client()
        pid = point_id or str(_uuid.uuid4())

        vectors = await embed(content)
        vector = vectors[0]

        payload: dict[str, Any] = {
            "content": content,
            "namespace": self.namespace,
            "timestamp": datetime.utcnow().isoformat(),
            **(metadata or {}),
        }

        await client.upsert(
            collection_name=self.collection,
            points=[PointStruct(id=pid, vector=vector, payload=payload)],
        )
        logger.debug(f"Memory saved to {self.collection}: {content[:60]}...")
        return pid

    async def search(
        self,
        query: str,
        top_k: int = 5,
        score_threshold: float = 0.65,
        filter_metadata: Optional[dict[str, Any]] = None,
    ) -> list[dict[str, Any]]:
        """
        Semantic similarity search over stored memories.

        Returns a list of dicts with 'content', 'score', and 'metadata'.
        """
        client = await self._get_client()
        vectors = await embed(query)
        query_vector = vectors[0]

        results = await client.search(
            collection_name=self.collection,
            query_vector=query_vector,
            limit=top_k,
            score_threshold=score_threshold,
            with_payload=True,
        )

        return [
            {
                "content": r.payload.get("content", ""),
                "score": r.score,
                "metadata": {k: v for k, v in (r.payload or {}).items() if k != "content"},
            }
            for r in results
        ]

    async def search_text(self, query: str, top_k: int = 5) -> str:
        """
        Convenience method — returns memories as a formatted text block
        suitable for injecting into an LLM prompt.
        """
        results = await self.search(query, top_k=top_k)
        if not results:
            return ""
        lines = [f"- [{r['score']:.2f}] {r['content']}" for r in results]
        return "\n".join(lines)

    async def delete(self, point_id: str) -> None:
        """Delete a specific memory point."""
        client = await self._get_client()
        await client.delete(
            collection_name=self.collection,
            points_selector=[point_id],
        )

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None


class LeaderMemoryManager(MemoryManager):
    """
    Extended memory manager for leader agents.
    Can read from multiple subordinate namespaces.
    """

    def __init__(self, namespace: str) -> None:
        super().__init__(namespace)
        self._sub_managers: list[MemoryManager] = []

    def add_subordinate(self, namespace: str) -> None:
        """Register a subordinate namespace to read from."""
        self._sub_managers.append(MemoryManager(namespace))

    async def search_all(
        self,
        query: str,
        top_k_per_namespace: int = 3,
    ) -> str:
        """
        Search this leader's memory AND all registered subordinate memories.
        Returns a combined formatted text block.
        """
        all_results: list[str] = []

        # Own memory
        own = await self.search_text(query, top_k=top_k_per_namespace)
        if own:
            all_results.append(f"## {self.namespace} (self)\n{own}")

        # Subordinate memories
        for sub in self._sub_managers:
            sub_results = await sub.search_text(query, top_k=top_k_per_namespace)
            if sub_results:
                all_results.append(f"## {sub.namespace} (subordinate)\n{sub_results}")

        return "\n\n".join(all_results) if all_results else ""
