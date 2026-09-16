from __future__ import annotations

import logging
import os
from typing import Any

from .contracts import RetrievalHit

logger = logging.getLogger(__name__)


class ChromaVectorStore:
    """Small adapter around Chroma's HTTP client; Chroma payloads stop here."""

    def __init__(self, client: Any | None = None, collection_name: str | None = None) -> None:
        self._client = client
        self.collection_name = collection_name or os.getenv("CHROMA_RAG_COLLECTION", "rag_documents")
        self._collection = None
        self.available = True

    async def _get_collection(self) -> Any:
        if self._client is None:
            import chromadb
            self._client = await chromadb.AsyncHttpClient(
                host=os.getenv("CHROMA_HOST", "chroma"),
                port=int(os.getenv("CHROMA_PORT", "8000")),
                ssl=os.getenv("CHROMA_SSL", "false").lower() == "true",
            )
        if self._collection is None:
            self._collection = await self._client.get_or_create_collection(
                self.collection_name, metadata={"hnsw:space": "cosine"}
            )
        return self._collection

    async def ensure_collection(self) -> None:
        await self._get_collection()

    async def upsert(self, ids: list[str], documents: list[str], embeddings: list[list[float]], metadatas: list[dict[str, Any]]) -> None:
        collection = await self._get_collection()
        await collection.upsert(ids=ids, documents=documents, embeddings=embeddings, metadatas=metadatas)

    async def query(self, embedding: list[float], top_k: int, document_id: str) -> list[RetrievalHit]:
        collection = await self._get_collection()
        response = await collection.query(query_embeddings=[embedding], n_results=top_k, where={"document_id": document_id})
        ids = (response.get("ids") or [[]])[0]
        documents = (response.get("documents") or [[]])[0]
        metadatas = (response.get("metadatas") or [[]])[0]
        distances = (response.get("distances") or [[]])[0]
        return [RetrievalHit(str(identifier), document, metadata or {}, 1.0 - float(distance or 0), "semantic") for identifier, document, metadata, distance in zip(ids, documents, metadatas, distances)]

    async def set_active_document(self, document_id: str) -> None:
        collection = await self._get_collection()
        await collection.modify(metadata={"active_document_id": document_id})

    async def get_active_document(self) -> str | None:
        collection = await self._get_collection()
        metadata = collection.metadata
        return (metadata or {}).get("active_document_id")

    async def delete_document(self, document_id: str) -> None:
        collection = await self._get_collection()
        await collection.delete(where={"document_id": document_id})

    async def get_document_hits(self, document_id: str) -> list[RetrievalHit]:
        collection = await self._get_collection()
        response = await collection.get(
            where={"document_id": document_id},
            include=["documents", "metadatas"],
        )
        ids = response.get("ids") or []
        documents = response.get("documents") or []
        metadatas = response.get("metadatas") or []
        return [
            RetrievalHit(str(identifier), document or "", metadata or {}, 0.0, "semantic")
            for identifier, document, metadata in zip(ids, documents, metadatas)
        ]


class MemoryVectorStore:
    """Deterministic test store with the same adapter contract."""
    def __init__(self) -> None:
        self.items: dict[str, tuple[str, list[float], dict[str, Any]]] = {}
        self.active_document_id: str | None = None

    async def ensure_collection(self) -> None: return None
    async def upsert(self, ids, documents, embeddings, metadatas) -> None:
        self.items.update({i: (d, e, m) for i, d, e, m in zip(ids, documents, embeddings, metadatas)})
    async def query(self, embedding, top_k, document_id) -> list[RetrievalHit]:
        import math
        def cosine(left, right):
            denominator = math.sqrt(sum(v*v for v in left)) * math.sqrt(sum(v*v for v in right)) or 1
            return sum(a*b for a,b in zip(left,right)) / denominator
        values = [(cosine(embedding, vector), identifier, document, metadata) for identifier, (document, vector, metadata) in self.items.items() if metadata.get("document_id") == document_id]
        return [RetrievalHit(identifier, document, metadata, score, "semantic") for score, identifier, document, metadata in sorted(values, reverse=True)[:top_k]]
    async def set_active_document(self, document_id): self.active_document_id = document_id
    async def get_active_document(self): return self.active_document_id
    async def delete_document(self, document_id):
        self.items = {key: value for key, value in self.items.items() if value[2].get("document_id") != document_id}

    async def get_document_hits(self, document_id):
        return [
            RetrievalHit(identifier, document, metadata, 0.0, "semantic")
            for identifier, (document, _vector, metadata) in self.items.items()
            if metadata.get("document_id") == document_id
        ]
