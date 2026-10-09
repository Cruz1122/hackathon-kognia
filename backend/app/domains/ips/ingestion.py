"""Explicit, idempotent SODA3 -> PostgreSQL -> Chroma bootstrap."""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime
from typing import Any, Protocol

from ...ips_soda3.schema import NormalizedDataset, normalize_dataset
from .repository import IPSRepository
from .vector_store import IPSVectorStore

logger = logging.getLogger(__name__)


class PageSource(Protocol):
    async def list_all_pages(self, *, page_size: int, max_pages: int): ...


class IPSIngestionService:
    def __init__(self, repository: IPSRepository, vector_store: IPSVectorStore) -> None:
        self.repository = repository
        self.vector_store = vector_store

    async def fetch_all(
        self,
        source: PageSource,
        *,
        page_size: int = 1000,
        max_pages: int = 1000,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        last_page = 0
        last_size = 0
        async for page in source.list_all_pages(page_size=page_size, max_pages=max_pages):
            if page.get("stale"):
                raise RuntimeError("IPS_STALE_SOURCE_PAGE")
            values = page.get("data")
            if not isinstance(values, list):
                raise ValueError("IPS_PAGE_DATA_INVALID")
            rows.extend(values)
            last_page = int(page.get("page") or 0)
            last_size = len(values)
            print(f"ips source page {last_page} rows {len(rows)}", flush=True)
        if last_page == max_pages and last_size == page_size:
            raise RuntimeError("IPS_PAGINATION_LIMIT_REACHED")
        return rows

    async def ingest_rows(
        self,
        rows: list[dict[str, Any]],
        *,
        fetched_at: datetime | None = None,
    ) -> dict[str, Any]:
        dataset: NormalizedDataset = await asyncio.to_thread(normalize_dataset, rows)
        fetched_at = fetched_at or datetime.now(UTC)
        existing = await self.repository.snapshot_by_hash(dataset.source_hash)
        if existing is not None and existing.status == "active":
            indexed = await self.vector_store.snapshot_count(existing.id)
            if indexed != existing.site_count:
                await self.vector_store.index_snapshot(existing.id, dataset)
                indexed = await self.vector_store.snapshot_count(existing.id)
            if indexed != existing.site_count:
                raise RuntimeError("IPS_CHROMA_INDEX_INCOMPLETE")
            return self._result(existing.id, dataset, "already_active")
        if existing is not None and existing.status == "inactive":
            indexed = await self.vector_store.snapshot_count(existing.id)
            if indexed != existing.site_count:
                await self.vector_store.index_snapshot(existing.id, dataset)
                indexed = await self.vector_store.snapshot_count(existing.id)
            if indexed != existing.site_count:
                raise RuntimeError("IPS_CHROMA_INDEX_INCOMPLETE")
            await self.repository.activate(existing.id)
            await self._invalidate_version_cache()
            return self._result(existing.id, dataset, "reactivated")
        if existing is not None:
            try:
                await self.vector_store.delete_snapshot(existing.id)
            except Exception:
                logger.warning("Could not clean failed IPS Chroma snapshot", exc_info=True)
            await self.repository.delete_non_active_snapshot(existing.id)

        snapshot, _sites = await self.repository.create_staging(
            dataset,
            fetched_at=fetched_at,
        )
        try:
            indexed = await self.vector_store.index_snapshot(snapshot.id, dataset)
            verified = await self.vector_store.snapshot_count(snapshot.id)
            if indexed != len(dataset.sites) or verified != len(dataset.sites):
                raise RuntimeError(
                    f"IPS_CHROMA_INDEX_INCOMPLETE:expected={len(dataset.sites)}:actual={verified}"
                )
            await self.repository.activate(snapshot.id)
            await self._invalidate_version_cache()
        except Exception as exc:
            try:
                await self.vector_store.delete_snapshot(snapshot.id)
            except Exception:
                logger.warning("Could not roll back IPS Chroma snapshot", exc_info=True)
            await self.repository.mark_failed(snapshot.id, f"{type(exc).__name__}: {exc}")
            raise
        return self._result(snapshot.id, dataset, "activated")

    async def ingest_from_source(
        self,
        source: PageSource,
        *,
        page_size: int = 1000,
        max_pages: int = 1000,
    ) -> dict[str, Any]:
        rows = await self.fetch_all(source, page_size=page_size, max_pages=max_pages)
        return await self.ingest_rows(rows)

    @staticmethod
    def _result(
        snapshot_id: uuid.UUID,
        dataset: NormalizedDataset,
        status: str,
    ) -> dict[str, Any]:
        return {
            "status": status,
            "dataset_id": "s2ru-bqt6",
            "snapshot_id": str(snapshot_id),
            "source_hash": dataset.source_hash,
            "source_rows": dataset.row_count,
            "sites": len(dataset.sites),
            "cutoffs": dataset.cutoffs,
        }

    @staticmethod
    async def _invalidate_version_cache() -> None:
        # SODA3 is queried live and its cache is process-local; no Redis
        # invalidation is needed in the production voice path.
        return None


__all__ = ["IPSIngestionService"]
