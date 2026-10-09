from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Rewrite:
    standalone_query: str
    keywords: list[str]
    entities: list[str]
    constraints: list[str]


class QueryRewriter(Protocol):
    async def rewrite(self, query: str, context: list[dict[str, str]]) -> Rewrite: ...


class NoopRewriter:
    async def rewrite(self, query: str, context: list[dict[str, str]]) -> Rewrite:
        return Rewrite(query, query.split(), [], [])


class ContextRewriter:
    """Bounded local rewrite for follow-ups when no LLM rewriter is configured."""
    async def rewrite(self, query: str, context: list[dict[str, str]]) -> Rewrite:
        relevant = " ".join(item.get("content", "") for item in context[-2:])
        standalone = f"{relevant[-700:]} {query}".strip()
        keywords = list(dict.fromkeys(standalone.split()))[-24:]
        return Rewrite(standalone, keywords, [], [])
