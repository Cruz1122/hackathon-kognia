from __future__ import annotations

import hashlib
import asyncio
from collections import OrderedDict
from typing import Any

from .contracts import RetrievalHit, RetrievalResult, VectorStore
from .citations import build_knowledge_context
from .embeddings import E5EmbeddingProvider, EmbeddingProvider
from .fusion import reciprocal_rank_fusion
from .lexical import LexicalRetriever
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

    async def search(self, query: str, *, document_id: str | None = None, conversation: list[dict[str, str]] | None = None, debug: bool = False) -> RetrievalResult:
        active = document_id or await self.store.get_active_document()
        if not active:
            return RetrievalResult("INSUFFICIENT", [], 1, debug={"reason": "no_active_document"})
        key = (hashlib.sha256(query.strip().casefold().encode()).hexdigest(), active)
        if key in self._cache:
            return self._cache[key]
        lexical = LexicalRetriever(self._hits.get(active, [])).search(query, 3)
        signals = LexicalRetriever(self._hits.get(active, [])).signals(query, lexical)
        exact = bool(signals["exact_identifier_match"] or signals["exact_phrase_match"])
        if exact and lexical:
            result = RetrievalResult("SUFFICIENT", lexical[:3], 1, False, {"lexical": lexical, "signals": signals})
            return self._remember(key, result)
        semantic: list[RetrievalHit] = []
        try:
            semantic = await asyncio.wait_for(
                self.store.query(self.embeddings.embed_queries([query])[0], 3, active),
                timeout=float(__import__("os").getenv("CHROMA_QUERY_TIMEOUT_S", "3")),
            )
        except Exception:
            result = RetrievalResult("INSUFFICIENT", [], 2, False, {"status": "unavailable", "lexical": lexical})
            return self._remember(key, result)
        # A vector hit is not evidence by itself.  This bounded guard prevents a
        # totally unrelated nearest neighbour from becoming a fabricated answer.
        if not lexical and semantic and semantic[0].score < 0.45:
            semantic = []
        overlap = len({h.chunk_id for h in lexical} & {h.chunk_id for h in semantic})
        fused = reciprocal_rank_fusion([lexical, semantic], top_k=3)
        if fused and (overlap >= 1 or semantic):
            state = "SUFFICIENT" if overlap or len(semantic) == 1 else "AMBIGUOUS"
            result = RetrievalResult(state, fused, 2, False, {"lexical": lexical, "semantic": semantic, "top3_overlap_count": overlap, "signals": signals})
        else:
            result = RetrievalResult("INSUFFICIENT", [], 2, False, {"lexical": lexical, "semantic": semantic})
        if result.evidence_state == "SUFFICIENT" or not conversation:
            return self._remember(key, result)
        try:
            rewritten = await self.rewriter.rewrite(query, conversation[-2:])
            rewritten_lexical = LexicalRetriever(self._hits.get(active, [])).search(rewritten.standalone_query, 3)
            rewritten_semantic = await asyncio.wait_for(
                self.store.query(self.embeddings.embed_queries([rewritten.standalone_query])[0], 3, active),
                timeout=float(__import__("os").getenv("CHROMA_QUERY_TIMEOUT_S", "3")),
            )
            final = reciprocal_rank_fusion([lexical, semantic, rewritten_lexical, rewritten_semantic], top_k=3)
            result = RetrievalResult("SUFFICIENT" if final else "INSUFFICIENT", final, 3, True, {**result.debug, "rewritten": rewritten.__dict__})
        except Exception:
            result.debug["rewrite_failed"] = True
        return self._remember(key, result)

    def _remember(self, key, result):
        _, result.source_map = build_knowledge_context(result)
        self._cache[key] = result
        self._cache.move_to_end(key)
        while len(self._cache) > self.cache_size:
            self._cache.popitem(last=False)
        return result
