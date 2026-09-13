from __future__ import annotations

import re
import unicodedata

from .contracts import RetrievalHit


def normalize(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


class LexicalRetriever:
    def __init__(self, hits: list[RetrievalHit]) -> None:
        self.hits = hits

    def search(self, query: str, top_k: int = 3) -> list[RetrievalHit]:
        query_norm = normalize(query)
        terms = re.findall(r"[\w]+(?:-[\w]+)*", query_norm)
        if not terms:
            return []
        scored: list[RetrievalHit] = []
        for hit in self.hits:
            content = normalize(hit.content)
            exact_phrase = query_norm.strip() in content
            matches = sum(1 for term in terms if term in content)
            score = matches / len(terms) + (1.0 if exact_phrase else 0.0)
            if score:
                scored.append(RetrievalHit(hit.chunk_id, hit.content, hit.metadata, score, "lexical"))
        return sorted(scored, key=lambda item: (-item.score, item.chunk_id))[:top_k]

    def signals(self, query: str, hits: list[RetrievalHit]) -> dict[str, object]:
        q = normalize(query)
        identifiers = re.findall(r"\b[A-Z]{2,}[\w-]*\d[\w-]*\b", query.upper())
        top = hits[0] if hits else None
        return {
            "top1_score": top.score if top else 0.0,
            "top2_score": hits[1].score if len(hits) > 1 else 0.0,
            "top1_top2_margin": (top.score - hits[1].score) if top and len(hits) > 1 else 0.0,
            "query_term_coverage": top.score if top else 0.0,
            "exact_phrase_match": bool(top and q.strip() in normalize(top.content)),
            "exact_identifier_match": bool(top and identifiers and all(identifier.casefold() in normalize(top.content) for identifier in identifiers)),
        }
