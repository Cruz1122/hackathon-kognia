"""Authenticated HTTP routes for structured IPS searches."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from ..auth.dependencies import get_current_user
from .client import Soda3Error
from .runtime import IPSConfigurationError, get_ips_service
from .service import IPSService


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, ValueError):
        return HTTPException(status_code=422, detail=str(exc))
    if isinstance(exc, IPSConfigurationError):
        return HTTPException(
            status_code=503,
            detail="La integración SODA3 IPS no está disponible.",
        )
    if isinstance(exc, Soda3Error):
        status = 503 if exc.transient else 502
        return HTTPException(
            status_code=status,
            detail=(
                "Servicio externo SODA3 temporalmente inaccesible"
                if exc.transient
                else "Fallo consultando la API SODA3"
            ),
        )
    raise exc


def create_ips_router(
    service: IPSService | None = None,
    *,
    require_auth: bool = True,
) -> APIRouter:
    """Create the router; ``service`` is injectable for offline tests."""
    dependencies = [Depends(get_current_user)] if require_auth else []
    router = APIRouter(
        prefix="/api/ips",
        tags=["ips"],
        dependencies=dependencies,
    )

    def resolve_service() -> IPSService:
        return service if service is not None else get_ips_service()

    @router.get("")
    async def search_ips(
        departamento: str | None = None,
        municipio: str | None = None,
        naturaleza: str | None = None,
        nivel: str | None = None,
        nombre: str | None = None,
        descripcion_capacidad: str | None = None,
        page: int = Query(1, ge=1, le=10000),
        page_size: int = Query(50, ge=1, le=500),
    ):
        try:
            return await resolve_service().list_ips(
                departamento=departamento,
                municipio=municipio,
                naturaleza=naturaleza,
                nivel=nivel,
                nombre=nombre,
                descripcion_capacidad=descripcion_capacidad,
                page=page,
                page_size=page_size,
            )
        except (ValueError, IPSConfigurationError, Soda3Error) as exc:
            raise _http_error(exc) from exc

    @router.get("/count")
    async def count_ips_records(
        departamento: str | None = None,
        municipio: str | None = None,
        naturaleza: str | None = None,
        nivel: str | None = None,
    ):
        try:
            return await resolve_service().count_records(
                departamento=departamento,
                municipio=municipio,
                naturaleza=naturaleza,
                nivel=nivel,
            )
        except (ValueError, IPSConfigurationError, Soda3Error) as exc:
            raise _http_error(exc) from exc

    @router.get("/capacity")
    async def capacity(
        departamento: str | None = None,
        municipio: str | None = None,
        naturaleza: str | None = None,
        nivel: str | None = None,
        page: int = Query(1, ge=1, le=10000),
        page_size: int = Query(200, ge=1, le=500),
    ):
        try:
            return await resolve_service().grouped_capacity(
                departamento=departamento,
                municipio=municipio,
                naturaleza=naturaleza,
                nivel=nivel,
                page=page,
                page_size=page_size,
            )
        except (ValueError, IPSConfigurationError, Soda3Error) as exc:
            raise _http_error(exc) from exc

    return router


__all__ = ["create_ips_router"]
