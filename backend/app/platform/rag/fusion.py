from __future__ import annotations

from .contracts import RetrievalHit


def reciprocal_rank_fusion(rankings: list[list[RetrievalHit]], *, k: int = 60, top_k: int = 3) -> list[RetrievalHit]:
    scores: dict[str, float] = {}
    hits: dict[str, RetrievalHit] = {}
    for ranking in rankings:
        for rank, hit in enumerate(ranking, 1):
            scores[hit.chunk_id] = scores.get(hit.chunk_id, 0) + 1 / (k + rank)
            hits[hit.chunk_id] = hit
    return [RetrievalHit(hits[key].chunk_id, hits[key].content, hits[key].metadata, scores[key], hits[key].source) for key in sorted(scores, key=lambda key: (-scores[key], key))[:top_k]]
