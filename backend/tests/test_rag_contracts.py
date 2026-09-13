import asyncio

from app.platform.rag.chroma_store import MemoryVectorStore
from app.platform.rag.contracts import RetrievalHit
from app.platform.rag.embeddings import E5EmbeddingProvider
from app.platform.rag.retrieval import ProgressiveRetriever


def test_exact_identifier_uses_lexical_level_without_semantic_query() -> None:
    async def run():
        store = MemoryVectorStore()
        embeddings = E5EmbeddingProvider()
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
        embeddings = E5EmbeddingProvider()
        vectors = embeddings.embed_passages(["version one", "version two"])
        await store.upsert(["a", "b"], ["version one", "version two"], vectors, [{"document_id": "v1"}, {"document_id": "v2"}])
        hits = await store.query(vectors[1], 3, "v2")
        assert [hit.chunk_id for hit in hits] == ["b"]
    asyncio.run(run())
