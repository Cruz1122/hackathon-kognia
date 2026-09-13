from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol


@dataclass(frozen=True)
class RetrievalHit:
    chunk_id: str
    content: str
    metadata: dict[str, Any]
    score: float = 0.0
    source: Literal["lexical", "semantic"] = "semantic"


@dataclass
class RetrievalResult:
    evidence_state: Literal["SUFFICIENT", "AMBIGUOUS", "INSUFFICIENT"]
    hits: list[RetrievalHit] = field(default_factory=list)
    level_reached: int = 1
    rewrite_used: bool = False
    debug: dict[str, Any] = field(default_factory=dict)
    source_map: dict[str, Any] = field(default_factory=dict)


class VectorStore(Protocol):
    async def ensure_collection(self) -> None: ...
    async def upsert(self, ids: list[str], documents: list[str], embeddings: list[list[float]], metadatas: list[dict[str, Any]]) -> None: ...
    async def query(self, embedding: list[float], top_k: int, document_id: str) -> list[RetrievalHit]: ...
    async def set_active_document(self, document_id: str) -> None: ...
    async def get_active_document(self) -> str | None: ...
