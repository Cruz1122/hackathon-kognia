from __future__ import annotations

import hashlib
import asyncio
import time
import os
import json
from collections import OrderedDict
from typing import Any

from .contracts import RetrievalHit, RetrievalResult, VectorStore
from .citations import build_knowledge_context
from .embeddings import E5EmbeddingProvider, EmbeddingProvider
from .fusion import reciprocal_rank_fusion
from .lexical import LexicalRetriever, expand_query
from .rewriting import ContextRewriter, QueryRewriter


class ProgressiveRetriever:
    def __init__(self, store: VectorStore, *, embedding_provider: EmbeddingProvider | None = None, rewriter: QueryRewriter | None = None, cache_size: int = 256) -> None:
        self.store = store
        self.embeddings = embedding_provider or E5EmbeddingProvider()
        self.rewriter = rewriter or ContextRewriter()
        self.cache_size = cache_size
        self._cache: OrderedDict[tuple[str, str], RetrievalResult] = OrderedDict()
        self._hits: dict[str, list[RetrievalHit]] = {}

    def set_document_chunks(self, document_id: str, hits: list[RetrievalHit]) -> None:
        self._hits[document_id] = hits
        self._cache.clear()

    async def ensure_document_chunks(self, document_id: str) -> list[RetrievalHit]:
        hits = self._hits.get(document_id)
        if hits:
            return hits
        loader = getattr(self.store, "get_document_hits", None)
        if loader is None:
            return []
        try:
            hits = await loader(document_id)
        except Exception:
            return []
        if hits:
            self.set_document_chunks(document_id, hits)
        return hits

    async def search(self, query: str, *, document_id: str | None = None, conversation: list[dict[str, str]] | None = None, debug: bool = False) -> RetrievalResult:
        started = time.perf_counter()
        active_document = await self.store.get_active_document()
        active = document_id or active_document
        if not active:
            return RetrievalResult("INSUFFICIENT", [], 1, debug={"reason": "no_active_document"})
        if document_id and document_id != active_document:
            return RetrievalResult("INSUFFICIENT", [], 1, debug={"reason": "stale_document_id", "requested_document_id": document_id, "active_document_id": active_document})
        await self.ensure_document_chunks(active)
        context_fingerprint = hashlib.sha256(json.dumps(list(conversation or [])[-2:], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        key = (hashlib.sha256(f"{query.strip().casefold()}|{context_fingerprint}".encode()).hexdigest(), active)
        if key in self._cache:
            return self._cache[key]
        lexical_started = time.perf_counter()
        lexical_top_k = int(os.getenv("RAG_LEXICAL_TOP_K", "5"))
        semantic_top_k = int(os.getenv("RAG_SEMANTIC_TOP_K", "5"))
        final_top_k = int(os.getenv("RAG_FINAL_TOP_K", "3"))
        lexical = LexicalRetriever(self._hits.get(active, [])).search(query, lexical_top_k)
        lexical_retriever = LexicalRetriever(self._hits.get(active, []))
        analysis_query = expand_query(query)
        related_term_coverage = lexical_retriever.related_term_coverage(analysis_query)
        specific_anchor = lexical_retriever.has_specific_anchor(analysis_query)
        lexical_ms = (time.perf_counter() - lexical_started) * 1000
        signals = LexicalRetriever(self._hits.get(active, [])).signals(query, lexical)
        exact = bool(signals["exact_identifier_match"] or signals["exact_phrase_match"])
        if exact and lexical:
            result = RetrievalResult("SUFFICIENT", lexical[:3], 1, False, {"lexical": lexical, "signals": signals})
            return self._remember(key, result)
        semantic: list[RetrievalHit] = []
        try:
            embedding_started = time.perf_counter()
            query_embedding = (await asyncio.to_thread(self.embeddings.embed_queries, [expand_query(query)]))[0]
            embedding_ms = (time.perf_counter() - embedding_started) * 1000
            chroma_started = time.perf_counter()
            semantic = await asyncio.wait_for(
                self.store.query(query_embedding, semantic_top_k, active),
                timeout=float(__import__("os").getenv("CHROMA_QUERY_TIMEOUT_S", "3")),
            )
            chroma_ms = (time.perf_counter() - chroma_started) * 1000
        except Exception:
            result = RetrievalResult("INSUFFICIENT", [], 2, False, {"status": "unavailable", "lexical": lexical, "timings": {"lexical_ms": lexical_ms, "total_retrieval_ms": (time.perf_counter() - started) * 1000}})
            return self._remember(key, result)
        # A vector hit is not evidence by itself.  This bounded guard prevents a
        # totally unrelated nearest neighbour from becoming a fabricated answer.
        semantic_margin = semantic[0].score - semantic[1].score if len(semantic) > 1 else semantic[0].score if semantic else 0.0
        raw_semantic = list(semantic)
        if semantic and related_term_coverage <= 0.5 and not specific_anchor and semantic_margin < 0.04:
            semantic = []
        if lexical and not semantic and related_term_coverage <= 0.5 and not specific_anchor:
            lexical = []
        overlap = len({h.chunk_id for h in lexical} & {h.chunk_id for h in semantic})
        fused = reciprocal_rank_fusion([lexical, semantic], top_k=final_top_k)
        if fused and (lexical or semantic):
            state = "SUFFICIENT" if lexical and not semantic or overlap or len(semantic) == 1 else "AMBIGUOUS"
            result = RetrievalResult(state, fused, 2, False, {"lexical": lexical, "semantic": semantic, "raw_semantic": raw_semantic, "top3_overlap_count": overlap, "signals": signals, "timings": {"lexical_ms": lexical_ms, "embedding_ms": embedding_ms, "chroma_ms": chroma_ms, "total_retrieval_ms": (time.perf_counter() - started) * 1000}})
        else:
            result = RetrievalResult("INSUFFICIENT", [], 2, False, {"lexical": lexical, "semantic": semantic, "raw_semantic": raw_semantic, "timings": {"lexical_ms": lexical_ms, "embedding_ms": embedding_ms, "chroma_ms": chroma_ms, "total_retrieval_ms": (time.perf_counter() - started) * 1000}})
        if result.evidence_state == "SUFFICIENT" or not conversation:
            return self._remember(key, result)
        try:
            rewrite_started = time.perf_counter()
            rewritten = await asyncio.wait_for(
                self.rewriter.rewrite(query, conversation[-2:]),
                timeout=float(os.getenv("RAG_REWRITE_TIMEOUT_S", "3")),
            )
            rewritten_lexical = LexicalRetriever(self._hits.get(active, [])).search(rewritten.standalone_query, lexical_top_k)
            rewritten_embedding = (await asyncio.to_thread(self.embeddings.embed_queries, [rewritten.standalone_query]))[0]
            rewritten_semantic = await asyncio.wait_for(
                self.store.query(rewritten_embedding, semantic_top_k, active),
                timeout=float(__import__("os").getenv("CHROMA_QUERY_TIMEOUT_S", "3")),
            )
            result.debug.setdefault("timings", {})["rewrite_ms"] = (time.perf_counter() - rewrite_started) * 1000
            result.debug["rewrite_count"] = 1
            final = reciprocal_rank_fusion([lexical, semantic, rewritten_lexical, rewritten_semantic], top_k=final_top_k)
            result = RetrievalResult("SUFFICIENT" if final else "INSUFFICIENT", final, 3, True, {**result.debug, "rewritten": rewritten.__dict__})
            result.debug.setdefault("timings", {})["total_retrieval_ms"] = (time.perf_counter() - started) * 1000
        except Exception:
            result.debug["rewrite_failed"] = True
            result.debug.setdefault("rewrite_count", 1)
        return self._remember(key, result)

    def _remember(self, key, result):
        _, result.source_map = build_knowledge_context(result)
        self._cache[key] = result
        self._cache.move_to_end(key)
        while len(self._cache) > self.cache_size:
            self._cache.popitem(last=False)
        return result
