from __future__ import annotations

import hashlib
import asyncio
from datetime import datetime, timezone
from pathlib import Path

from .chunking import chunk_text
from .contracts import RetrievalHit, VectorStore
from .embeddings import EmbeddingProvider
from .extraction import extract_document
from .retrieval import ProgressiveRetriever


class RagIngestionService:
    def __init__(self, store: VectorStore, embeddings: EmbeddingProvider, retriever: ProgressiveRetriever) -> None:
        self.store = store
        self.embeddings = embeddings
        self.retriever = retriever
        self._replace_lock = asyncio.Lock()

    async def replace(self, data: bytes, filename: str, *, document_version: int = 1) -> dict:
        async with self._replace_lock:
            return await self._replace_unlocked(data, filename, document_version=document_version)

    async def _replace_unlocked(self, data: bytes, filename: str, *, document_version: int = 1) -> dict:
        digest = hashlib.sha256(data).hexdigest()
        document_id = f"doc_{digest[:12]}"
        active = await self.store.get_active_document()
        if active == document_id:
            count = len(self.retriever._hits.get(document_id, []))
            return {"document_id": document_id, "filename": filename, "hash": digest, "chunks": count, "active": True, "already_indexed": True}
        text, source_type, parser_version = extract_document(data, filename)
        pieces = chunk_text(text)
        if not pieces:
            raise ValueError("RAG_TEXT_EXTRACTION_EMPTY")
        chunk_count = len(pieces)
        now = datetime.now(timezone.utc).isoformat()
        ids = [f"{document_id}:{index:05d}" for index in range(chunk_count)]
        contents = [piece[0] for piece in pieces]
        vectors = self.embeddings.embed_passages(contents)
        metadatas = []
        hits = []
        for index, (content, start, end) in enumerate(pieces):
            line_start = text[:start].count("\n") + 1
            line_end = text[:end].count("\n") + 1
            section = next((line.lstrip("# ").strip() for line in reversed(text[:start].splitlines()) if line.startswith("#")), "General")
            metadata = {
                "document_id": document_id, "document_hash": digest, "document_version": document_version,
                "source_filename": filename, "source_type": source_type, "mime_type": {"pdf": "application/pdf", "md": "text/markdown", "txt": "text/plain"}[source_type],
                "document_title": Path(filename).stem, "section": section, "heading_path": section,
                "chunk_index": index, "chunk_count": chunk_count, "content_hash": hashlib.sha256(content.encode()).hexdigest(),
                "ingested_at": now, "parser_version": parser_version, "chunker_version": "structural-v1",
                "embedding_model": getattr(self.embeddings, "model_name", "intfloat/multilingual-e5-small"), "embedding_dimensions": len(vectors[index]),
                "citation_label": f"{Path(filename).stem} — líneas {line_start}–{line_end}", "line_start": line_start, "line_end": line_end,
                "char_start": start, "char_end": end, "char_count": len(content),
            }
            metadatas.append(metadata)
            hits.append(RetrievalHit(ids[index], content, metadata, 0.0, "semantic"))
        await self.store.ensure_collection()
        try:
            await self.store.upsert(ids, contents, vectors, metadatas)
            self.retriever.set_document_chunks(document_id, hits)
            await self.store.set_active_document(document_id)
        except Exception:
            self.retriever._hits.pop(document_id, None)
            delete_document = getattr(self.store, "delete_document", None)
            if delete_document is not None:
                try:
                    await delete_document(document_id)
                except Exception:
                    pass
            raise
        if active and active != document_id:
            # Retirement happens only after the new pointer is durable. A
            # cleanup failure must not turn a successful replacement into a
            # failed request; active retrieval is already isolated by id.
            try:
                delete_document = getattr(self.store, "delete_document", None)
                if delete_document is not None:
                    await delete_document(active)
                self.retriever._hits.pop(active, None)
            except Exception:
                pass
        return {"document_id": document_id, "filename": filename, "hash": digest, "chunks": chunk_count, "active": True}
