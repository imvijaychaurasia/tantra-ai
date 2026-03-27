"""
Tantra AI — RAG Pipeline
LlamaIndex 0.10 + Qdrant vector store + LiteLLM embeddings

Supports ingesting: PDF, DOCX, TXT, CSV, web pages, YouTube transcripts
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

from tantra.core.config import settings

logger = logging.getLogger(__name__)


def _build_embed_model():
    """Build LiteLLM-backed embedding model for LlamaIndex."""
    from llama_index.embeddings.litellm import LiteLLMEmbedding
    return LiteLLMEmbedding(
        model=settings.embed_model,
        api_base=f"{settings.litellm_base_url}/v1",
        api_key=settings.litellm_key,
        embed_batch_size=settings.embed_batch_size,
    )


def _build_llm(model_tier: str = "director"):
    """Build LiteLLM-backed LLM for LlamaIndex."""
    from llama_index.llms.litellm import LiteLLM
    return LiteLLM(
        model=model_tier,
        api_base=f"{settings.litellm_base_url}/v1",
        api_key=settings.litellm_key,
    )


def _build_vector_store(collection: str):
    """Build Qdrant vector store for a given collection."""
    from llama_index.vector_stores.qdrant import QdrantVectorStore
    from qdrant_client import QdrantClient

    client = QdrantClient(host=settings.qdrant_host, port=settings.qdrant_port)
    return QdrantVectorStore(client=client, collection_name=collection)


class KnowledgeBase:
    """
    Tantra RAG knowledge base — ingest documents and query them.

    Usage:
        kb = KnowledgeBase(collection="tantra_linkedin_playbook")
        await kb.ingest_file("/path/to/linkedin_strategy.pdf")
        answer = await kb.query("What posting frequency works best on LinkedIn?")
    """

    def __init__(
        self,
        collection: str = "tantra_knowledge",
        model_tier: str = "director",
    ) -> None:
        self.collection = collection
        self.model_tier = model_tier
        self._index = None

    def _get_service_context(self):
        from llama_index.core import Settings as LISettings
        LISettings.embed_model = _build_embed_model()
        LISettings.llm = _build_llm(self.model_tier)
        LISettings.chunk_size = settings.rag_chunk_size
        LISettings.chunk_overlap = settings.rag_chunk_overlap

    def _get_index(self):
        from llama_index.core import VectorStoreIndex
        from llama_index.core.storage.storage_context import StorageContext

        self._get_service_context()
        vector_store = _build_vector_store(self.collection)
        storage_context = StorageContext.from_defaults(vector_store=vector_store)

        if self._index is None:
            self._index = VectorStoreIndex.from_vector_store(
                vector_store=vector_store,
                storage_context=storage_context,
            )
        return self._index

    def ingest_file(self, file_path: str | Path) -> int:
        """
        Ingest a document file into the knowledge base.
        Returns number of chunks stored.
        """
        from llama_index.core import SimpleDirectoryReader, VectorStoreIndex
        from llama_index.core.storage.storage_context import StorageContext

        self._get_service_context()
        path = Path(file_path)

        if not path.exists():
            raise FileNotFoundError(f"File not found: {path}")

        loader = SimpleDirectoryReader(input_files=[str(path)])
        documents = loader.load_data()

        vector_store = _build_vector_store(self.collection)
        storage_context = StorageContext.from_defaults(vector_store=vector_store)

        self._index = VectorStoreIndex.from_documents(
            documents,
            storage_context=storage_context,
        )
        logger.info(f"Ingested {len(documents)} document(s) from {path.name}")
        return len(documents)

    def ingest_text(self, text: str, metadata: Optional[dict[str, Any]] = None) -> None:
        """Ingest raw text directly."""
        from llama_index.core import Document, VectorStoreIndex
        from llama_index.core.storage.storage_context import StorageContext

        self._get_service_context()
        doc = Document(text=text, metadata=metadata or {})
        vector_store = _build_vector_store(self.collection)
        storage_context = StorageContext.from_defaults(vector_store=vector_store)
        self._index = VectorStoreIndex.from_documents([doc], storage_context=storage_context)
        logger.info(f"Ingested text chunk ({len(text)} chars) into {self.collection}")

    def query(
        self,
        question: str,
        top_k: int = 5,
        response_mode: str = "compact",
    ) -> str:
        """
        Query the knowledge base and return an LLM-synthesised answer.

        Args:
            question:      Natural language question.
            top_k:         Number of retrieved chunks.
            response_mode: LlamaIndex response mode (compact/tree_summarize/no_text).

        Returns:
            Synthesised answer string.
        """
        index = self._get_index()
        query_engine = index.as_query_engine(
            similarity_top_k=top_k,
            response_mode=response_mode,
        )
        response = query_engine.query(question)
        return str(response)

    def retrieve(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        """
        Retrieve raw chunks (no LLM synthesis) — useful for agents
        who want to see the source passages directly.
        """
        index = self._get_index()
        retriever = index.as_retriever(similarity_top_k=top_k)
        nodes = retriever.retrieve(query)
        return [
            {
                "text": n.node.get_content(),
                "score": n.score,
                "metadata": n.node.metadata,
            }
            for n in nodes
        ]
