import asyncio
import os

from app.platform.rag.chroma_store import MemoryVectorStore
from app.platform.rag.contracts import RetrievalHit
from app.platform.rag.embeddings import HashEmbeddingProvider
from app.platform.rag.retrieval import ProgressiveRetriever
from app.platform.rag.rewriting import Rewrite
from app.platform.rag.extraction import RagExtractionError, extract_document
from app.platform.rag.lexical import expand_query


def test_exact_identifier_uses_lexical_level_without_semantic_query() -> None:
    async def run():
        store = MemoryVectorStore()
        embeddings = HashEmbeddingProvider()
        retriever = ProgressiveRetriever(store, embedding_provider=embeddings)
        hit = RetrievalHit("doc:0", "POL-R48329. Reembolso completo.", {"document_id": "doc"})
        retriever.set_document_chunks("doc", [hit])
        await store.set_active_document("doc")
        result = await retriever.search("¿Qué dice POL-R48329?")
        assert result.level_reached == 1
        assert result.hits[0].chunk_id == "doc:0"
    asyncio.run(run())


def test_memory_store_filters_document_versions() -> None:
    async def run():
        store = MemoryVectorStore()
        embeddings = HashEmbeddingProvider()
        vectors = embeddings.embed_passages(["version one", "version two"])
        await store.upsert(["a", "b"], ["version one", "version two"], vectors, [{"document_id": "v1"}, {"document_id": "v2"}])
        hits = await store.query(vectors[1], 3, "v2")
        assert [hit.chunk_id for hit in hits] == ["b"]
    asyncio.run(run())


def test_context_is_part_of_retrieval_cache_key() -> None:
    async def run():
        store = MemoryVectorStore(); embeddings = HashEmbeddingProvider()
        retriever = ProgressiveRetriever(store, embedding_provider=embeddings)
        retriever.set_document_chunks("doc", [RetrievalHit("a", "Cancelaciones y cambios de reserva.", {"document_id": "doc"})])
        await store.set_active_document("doc")
        first = await retriever.search("¿Y eso?", document_id="doc", conversation=[])
        second = await retriever.search("¿Y eso?", document_id="doc", conversation=[{"role": "user", "content": "Hablamos de cambios."}])
        assert first is not second
    asyncio.run(run())


def test_rewriter_timeout_is_bounded() -> None:
    class SlowRewriter:
        async def rewrite(self, query, context):
            await asyncio.sleep(0.05)
            return Rewrite(query, [], [], [])

    class NoHitStore(MemoryVectorStore):
        async def query(self, embedding, top_k, document_id):
            return []

    async def run():
        old = os.environ.get("RAG_REWRITE_TIMEOUT_S")
        os.environ["RAG_REWRITE_TIMEOUT_S"] = "0.001"
        try:
            store = NoHitStore(); embeddings = HashEmbeddingProvider()
            retriever = ProgressiveRetriever(store, embedding_provider=embeddings, rewriter=SlowRewriter())
            retriever.set_document_chunks("doc", [RetrievalHit("a", "Cambios de reserva.", {"document_id": "doc"})])
            await store.set_active_document("doc")
            result = await retriever.search("¿Y eso?", document_id="doc", conversation=[{"role": "user", "content": "Cancelaciones."}])
            assert result.debug["rewrite_failed"] is True
            assert result.debug["rewrite_count"] == 1
        finally:
            if old is None: os.environ.pop("RAG_REWRITE_TIMEOUT_S", None)
            else: os.environ["RAG_REWRITE_TIMEOUT_S"] = old
    asyncio.run(run())


def test_malformed_text_is_a_controlled_input_error() -> None:
    try:
        extract_document(b"\xff\xfe", "bad.txt")
    except RagExtractionError as exc:
        assert str(exc) == "RAG_INVALID_UTF8"
    else:
        raise AssertionError("invalid UTF-8 was accepted")


def test_explicit_stale_document_id_cannot_bypass_active_pointer() -> None:
    async def run():
        store = MemoryVectorStore(); embeddings = HashEmbeddingProvider()
        retriever = ProgressiveRetriever(store, embedding_provider=embeddings)
        retriever.set_document_chunks("v1", [RetrievalHit("v1:0", "old", {"document_id": "v1"})])
        retriever.set_document_chunks("v2", [RetrievalHit("v2:0", "new", {"document_id": "v2"})])
        await store.set_active_document("v2")
        result = await retriever.search("old", document_id="v1")
        assert result.evidence_state == "INSUFFICIENT"
        assert result.debug["reason"] == "stale_document_id"
    asyncio.run(run())


def test_retriever_hydrates_lexical_chunks_after_backend_restart() -> None:
    async def run():
        store = MemoryVectorStore(); embeddings = HashEmbeddingProvider()
        hit = RetrievalHit("doc:0", "POL-R48329. Reembolso completo.", {"document_id": "doc"})
        vector = embeddings.embed_passages([hit.content])[0]
        await store.upsert([hit.chunk_id], [hit.content], [vector], [hit.metadata])
        await store.set_active_document("doc")
        retriever = ProgressiveRetriever(store, embedding_provider=embeddings)
        result = await retriever.search("¿Qué dice POL-R48329?")
        assert result.level_reached == 1
        assert result.hits[0].chunk_id == hit.chunk_id
        assert retriever._hits["doc"][0].chunk_id == hit.chunk_id
    asyncio.run(run())


def test_cancellation_inflections_reach_the_cancellation_chunk() -> None:
    expanded = expand_query("qué pasa si la cance")
    assert "cancelación" in expanded
