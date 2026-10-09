"""Typed IPS queries, safe SoQL construction and cache-aside behavior."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any
from weakref import WeakValueDictionary

from .cache import RedisCache
from .client import DATASET_ID, Soda3Client, Soda3Error

logger = logging.getLogger(__name__)


def literal(value: str) -> str:
    """Build one escaped SoQL string literal, never an identifier or clause."""
    value = value.strip()
    if not 1 <= len(value) <= 100 or any(
        ord(character) < 32 or ord(character) == 127 for character in value
    ):
        raise ValueError("Texto de filtro inválido (longitud 1-100, sin caracteres de control)")
    return "'" + value.replace("'", "''") + "'"


def where_clause(
    *,
    departamento: str | None = None,
    municipio: str | None = None,
    naturaleza: str | None = None,
    nivel: str | None = None,
    nombre: str | None = None,
    descripcion_capacidad: str | None = None,
) -> str:
    predicates: list[str] = []
    fields = {
        "departamento": departamento,
        "municipio": municipio,
        "naturaleza": naturaleza,
        "num_nivel_atencion": nivel,
        "nom_descripcion_capacidad": descripcion_capacidad,
    }
    for field, value in fields.items():
        if value is not None:
            predicates.append(f"{field} = {literal(value)}")
    if nombre is not None:
        normalized_name = nombre.strip()
        if not normalized_name:
            raise ValueError("El nombre de la IPS no puede estar vacío")
        if any(character in normalized_name for character in ("%", "_", "\\")):
            raise ValueError("El nombre no puede contener comodines LIKE: %, _ o \\")
        pattern = literal(f"%{normalized_name}%")
        predicates.append(f"upper(nombre_prestador) like upper({pattern})")
    return (" WHERE " + " AND ".join(predicates)) if predicates else ""


class IPSService:
    """Application service for the fixed IPS dataset."""

    def __init__(
        self,
        client: Soda3Client,
        cache: RedisCache,
        *,
        ttl: int = 3600,
        empty_ttl: int = 300,
        stale_ttl: int = 86400,
    ) -> None:
        if min(ttl, empty_ttl, stale_ttl) < 1:
            raise ValueError("Los TTL de IPS deben ser positivos")
        self.client = client
        self.cache = cache
        self.ttl = ttl
        self.empty_ttl = empty_ttl
        self.stale_ttl = stale_ttl
        self._locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()

    async def _query(
        self,
        query: str,
        *,
        page: int | None = None,
        page_size: int | None = None,
    ) -> dict[str, Any]:
        payload = self.client.payload(query, page=page, page_size=page_size)
        key = self.cache.key_for(payload)

        def response(entry: dict[str, Any], *, cached: bool, stale: bool) -> dict[str, Any]:
            return {
                "data": entry["rows"],
                "source": "datos.gov.co",
                "dataset_id": DATASET_ID,
                "cached": cached,
                "stale": stale,
                "fetched_at": entry["fetched_at"],
            }

        found = await self.cache.get(key)
        if found is not None:
            logger.info("IPS SODA3 cache=HIT")
            return response(found, cached=True, stale=False)

        logger.info("IPS SODA3 cache=MISS")
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        async with lock:
            found = await self.cache.get(key)
            if found is not None:
                logger.info("IPS SODA3 cache=HIT_AFTER_LOCK")
                return response(found, cached=True, stale=False)
            try:
                rows = await self.client.query(query, page=page, page_size=page_size)
            except Soda3Error as exc:
                if exc.transient:
                    stale = await self.cache.get(f"{key}:stale")
                    if stale is not None:
                        logger.warning("SODA3 temporalmente caído; devolviendo caché stale")
                        return response(stale, cached=True, stale=True)
                raise
            entry = {
                "rows": rows,
                "fetched_at": datetime.now(UTC).isoformat(),
            }
            await self.cache.set(key, entry, ttl=self.ttl if rows else self.empty_ttl)
            await self.cache.set(f"{key}:stale", entry, ttl=self.stale_ttl)
            return response(entry, cached=False, stale=False)

    async def list_ips(
        self,
        *,
        departamento: str | None = None,
        municipio: str | None = None,
        naturaleza: str | None = None,
        nivel: str | None = None,
        nombre: str | None = None,
        descripcion_capacidad: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> dict[str, Any]:
        if page < 1 or page > 10000 or not 1 <= page_size <= 500:
            raise ValueError("page debe estar entre 1 y 10000 y page_size entre 1 y 500")
        query = "SELECT *" + where_clause(
            departamento=departamento,
            municipio=municipio,
            naturaleza=naturaleza,
            nivel=nivel,
            nombre=nombre,
            descripcion_capacidad=descripcion_capacidad,
        )
        result = await self._query(query, page=page, page_size=page_size)
        return {
            **result,
            "page": page,
            "page_size": page_size,
            "has_more": len(result["data"]) == page_size,
        }

    async def count_records(
        self,
        *,
        departamento: str | None = None,
        municipio: str | None = None,
        naturaleza: str | None = None,
        nivel: str | None = None,
    ) -> dict[str, Any]:
        query = "SELECT count(*) AS total" + where_clause(
            departamento=departamento,
            municipio=municipio,
            naturaleza=naturaleza,
            nivel=nivel,
        )
        result = await self._query(query)
        rows = result.pop("data")
        if len(rows) != 1 or "total" not in rows[0]:
            raise Soda3Error(
                "SODA3 devolvió un agregado de recuento inesperado",
                transient=True,
            )
        try:
            count = int(rows[0]["total"])
        except (TypeError, ValueError) as exc:
            raise Soda3Error(
                "SODA3 devolvió un total no numérico",
                transient=True,
            ) from exc
        return {
            **result,
            "total_registros": count,
            "metric": "filas_del_dataset_no_ips_unicas",
        }

    async def grouped_capacity(
        self,
        *,
        departamento: str | None = None,
        municipio: str | None = None,
        naturaleza: str | None = None,
        nivel: str | None = None,
        page: int = 1,
        page_size: int = 200,
    ) -> dict[str, Any]:
        if page < 1 or page > 10000 or not 1 <= page_size <= 500:
            raise ValueError("page debe estar entre 1 y 10000 y page_size entre 1 y 500")
        query = (
            "SELECT nom_grupo_capacidad, nom_descripcion_capacidad, "
            "sum(num_cantidad_capacidad_instalada) AS total_capacidad"
            + where_clause(
                departamento=departamento,
                municipio=municipio,
                naturaleza=naturaleza,
                nivel=nivel,
            )
            + " GROUP BY nom_grupo_capacidad, nom_descripcion_capacidad"
            + " ORDER BY nom_grupo_capacidad, nom_descripcion_capacidad"
        )
        result = await self._query(query, page=page, page_size=page_size)
        return {
            **result,
            "page": page,
            "page_size": page_size,
            "has_more": len(result["data"]) == page_size,
            "warning": "Las cantidades no deben sumarse entre descripciones de capacidad diferentes.",
        }

    async def list_all_pages(
        self,
        *,
        page_size: int = 1000,
        max_pages: int = 100,
    ) -> AsyncIterator[dict[str, Any]]:
        """Administrative iterator; never call it from a voice request."""
        if not 1 <= page_size <= 1000 or not 1 <= max_pages <= 1000:
            raise ValueError("Límites inválidos para sincronización")
        for page in range(1, max_pages + 1):
            result = await self._query("SELECT *", page=page, page_size=page_size)
            if not result["data"]:
                break
            yield {**result, "page": page, "page_size": page_size}
            if len(result["data"]) < page_size:
                break


__all__ = ["IPSService", "literal", "where_clause"]
