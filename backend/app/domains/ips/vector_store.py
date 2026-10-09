"""Independent Chroma index for semantic IPS facility discovery."""

from __future__ import annotations

import asyncio
import hashlib
import os
import uuid
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from ...ips_soda3.schema import NormalizedDataset, NormalizedSite
from ...platform.rag.embeddings import E5EmbeddingProvider, EmbeddingProvider


@dataclass(frozen=True, slots=True)
class IPSSemanticHit:
    site_id: str
    site_code: str
    content: str
    score: float
    metadata: dict[str, Any]


def semantic_document(site: NormalizedSite) -> str:
    grouped: dict[tuple[str, str], list[int]] = defaultdict(list)
    for item in site.capacities:
        grouped[(item.group_name, item.description)].append(item.quantity)
    capacities = "; ".join(
        f"{group} — {description}: cantidades registradas {', '.join(map(str, sorted(values)))}"
        for (group, description), values in sorted(grouped.items())
    )
    contacts = ". ".join(
        value
        for value in (
            f"Dirección: {site.address}" if site.address else None,
            f"Teléfono registrado: {site.phone}" if site.phone else None,
        )
        if value
    )
    return (
        f"Sede IPS: {site.site_name}. Prestador: {site.provider_name}. "
        f"Código de sede: {site.site_code}. Ubicación: {site.municipality}, "
        f"{site.department}. Naturaleza: {site.nature or 'no registrada'}. "
        f"Nivel de atención registrado: {site.care_level or 'no registrado'}. "
        f"{contacts}. Capacidad instalada registrada: {capacities}. "
        "Las cantidades son capacidad instalada a la fecha de corte y no representan "
        "disponibilidad actual, camas libres, citas ni horarios."
    )


class IPSVectorStore:
    def __init__(
        self,
        *,
        client: Any | None = None,
        embeddings: EmbeddingProvider | None = None,
        collection_name: str | None = None,
        batch_size: int | None = None,
    ) -> None:
        self._client = client
        self._collection = None
        self.embeddings = embeddings or E5EmbeddingProvider()
        self.collection_name = collection_name or os.getenv(
            "IPS_CHROMA_COLLECTION", "ips_facilities"
        )
        self.batch_size = batch_size or int(os.getenv("IPS_EMBEDDING_BATCH_SIZE", "128"))
        if self.batch_size < 1:
            raise ValueError("IPS_EMBEDDING_BATCH_SIZE must be positive")

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
                self.collection_name,
                metadata={"hnsw:space": "cosine", "dataset_id": "s2ru-bqt6"},
            )
        return self._collection

    async def index_snapshot(
        self,
        snapshot_id: uuid.UUID,
        dataset: NormalizedDataset,
    ) -> int:
        collection = await self._get_collection()
        total = 0
        site_total = len(dataset.sites)
        # E5 truncates to 512 tokens. Shorter inputs avoid tokenizing the full capacity list.
        embed_chars = int(os.getenv("IPS_EMBED_MAX_CHARS", "1200") or "1200")
        for start in range(0, site_total, self.batch_size):
            sites = dataset.sites[start : start + self.batch_size]
            documents = [semantic_document(site) for site in sites]
            embed_inputs = [document[:embed_chars] for document in documents]
            vectors = await asyncio.to_thread(self.embeddings.embed_passages, embed_inputs)
            print(f"ips chroma {start + len(sites)}/{site_total}", flush=True)
            ids = [str(uuid.uuid5(snapshot_id, site.site_code)) for site in sites]
            metadatas = [
                {
                    "dataset_id": "s2ru-bqt6",
                    "snapshot_id": str(snapshot_id),
                    "site_id": ids[index],
                    "site_code": site.site_code,
                    "department": site.department,
                    "municipality": site.municipality,
                    "content_hash": hashlib.sha256(documents[index].encode()).hexdigest(),
                    "cutoff": site.cutoff,
                }
                for index, site in enumerate(sites)
            ]
            await collection.upsert(
                ids=ids,
                documents=documents,
                embeddings=vectors,
                metadatas=metadatas,
            )
            total += len(ids)
        return total

    async def snapshot_count(self, snapshot_id: uuid.UUID) -> int:
        collection = await self._get_collection()
        response = await collection.get(
            where={"snapshot_id": str(snapshot_id)},
            include=["metadatas"],
        )
        return len(response.get("ids") or [])

    async def delete_snapshot(self, snapshot_id: uuid.UUID) -> None:
        collection = await self._get_collection()
        await collection.delete(where={"snapshot_id": str(snapshot_id)})

    async def search(
        self,
        query: str,
        *,
        snapshot_id: uuid.UUID,
        department: str | None = None,
        municipality: str | None = None,
        limit: int = 5,
    ) -> list[IPSSemanticHit]:
        predicates: list[dict[str, str]] = [{"snapshot_id": str(snapshot_id)}]
        if department:
            predicates.append({"department": department})
        if municipality:
            predicates.append({"municipality": municipality})
        where: dict[str, Any] = predicates[0] if len(predicates) == 1 else {"$and": predicates}
        vector = (await asyncio.to_thread(self.embeddings.embed_queries, [query]))[0]
        collection = await self._get_collection()
        response = await collection.query(
            query_embeddings=[vector],
            n_results=limit,
            where=where,
            include=["documents", "metadatas", "distances"],
        )
        documents = (response.get("documents") or [[]])[0]
        metadatas = (response.get("metadatas") or [[]])[0]
        distances = (response.get("distances") or [[]])[0]
        hits: list[IPSSemanticHit] = []
        for content, metadata, distance in zip(documents, metadatas, distances):
            values = metadata or {}
            expected_hash = values.get("content_hash")
            if expected_hash != hashlib.sha256(str(content or "").encode()).hexdigest():
                continue
            hits.append(
                IPSSemanticHit(
                    site_id=str(values.get("site_id", "")),
                    site_code=str(values.get("site_code", "")),
                    content=str(content or ""),
                    score=1.0 - float(distance or 0.0),
                    metadata=values,
                )
            )
        return hits


__all__ = ["IPSSemanticHit", "IPSVectorStore", "semantic_document"]
