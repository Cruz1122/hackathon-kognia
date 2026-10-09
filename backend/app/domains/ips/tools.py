"""Read-only agent tools. Their only evidence source is the live SODA3 IPS API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator

from ...config import AgentPromptVariant, get_agent_prompt_variant
from ...agent.tools.contracts import ToolContext, ToolDefinition
from ...agent.tools.registry import ToolRegistry
from ...ips_soda3.client import Soda3Error
from ...ips_soda3.runtime import IPSConfigurationError, get_ips_service


_BASELINE_CONTEXT_INSTRUCTIONS = (
    "Official Colombian IPS orientation domain. The tools read dataset s2ru-bqt6 live from the SODA3 API. "
    "If the user supplies an exact institution/provider name or a concrete location, ALWAYS call search_ips first, even "
    "when the name is long. Use semantic_search_ips only for approximate, descriptive, misspelled, or STT wording, or "
    "as a recovery after an exact search returns no rows. Use search_ips for exact names, location filters, or an exact registered capacity category. Put institution/provider "
    "names in query and capacity descriptions in capacity; never put a requested service or capacity in query. "
    "Use get_ips_details for contact and address data, get_ips_capacity for installed-capacity categories, "
    "semantic_search_ips when the user says the name is approximate, describes a similar site, or the wording has likely STT errors, "
    "and compare_ips_capacity to read one category across the sites already found. "
    "Say how many were found using total, then the main sites with name, municipality, phone, nature and level, "
    "warn that capacity is registered rather than currently available, and ask one follow-up question. "
    "Never invent an institution, service, address, phone, schedule, appointment, clinical recommendation, bed availability, "
    "or real-time availability. Installed capacity is a historical registered quantity at the dataset cutoff, never proof that "
    "a bed, room, ambulance, appointment, or service is currently available. If a tool returns no results, say so clearly."
)

_COMPACT_CONTEXT_INSTRUCTIONS = (
    "Official Colombian IPS registry from the live SODA3 API. Use search_ips for exact name, location, nature, type, "
    "or registered capacity; put names in query and capacity in capacity. Use semantic_search_ips for approximate, "
    "descriptive, misspelled, or STT wording; use details/capacity by site_code and compare for known sites. "
    "Report only tool evidence, state that installed capacity is registered rather than current availability, and say when there are no results. "
    "Never invent services, addresses, phones, schedules, appointments, clinical advice, or availability."
)

CONTEXT_INSTRUCTIONS = (
    _COMPACT_CONTEXT_INSTRUCTIONS
    if get_agent_prompt_variant() is AgentPromptVariant.COMPACT
    else _BASELINE_CONTEXT_INSTRUCTIONS
)

async def _fetch_rows(**kwargs: Any) -> list[dict[str, Any]]:
    return await get_ips_service().agent_rows(**kwargs)


class SearchIPSArgs(BaseModel):
    query: str | None = Field(default=None, min_length=2, max_length=120)
    department: str | None = Field(default=None, min_length=2, max_length=100)
    municipality: str | None = Field(default=None, min_length=2, max_length=100)
    nature: str | None = Field(default=None, pattern="^(publica|privada)$")
    kind: str | None = Field(default=None, pattern="^(hospital|clinica)$")
    capacity: str | None = Field(default=None, min_length=2, max_length=120)
    limit: int = Field(default=5, ge=1, le=10)

    @model_validator(mode="after")
    def at_least_one_filter(self) -> "SearchIPSArgs":
        if not any((self.query, self.department, self.municipality, self.nature, self.kind, self.capacity)):
            raise ValueError("At least one IPS search criterion is required")
        return self


class IPSDetailsArgs(BaseModel):
    site_code: str = Field(min_length=1, max_length=32)


class SemanticSearchIPSArgs(BaseModel):
    query: str = Field(min_length=3, max_length=500)
    department: str | None = Field(default=None, min_length=2, max_length=100)
    municipality: str | None = Field(default=None, min_length=2, max_length=100)
    limit: int = Field(default=5, ge=1, le=5)


class CompareCapacityArgs(BaseModel):
    site_codes: list[str] = Field(min_length=1, max_length=10)
    capacity: str = Field(min_length=2, max_length=120)


def _card(site: dict[str, Any]) -> dict[str, Any]:
    card = {
        "site_code": site.get("site_code"),
        "site_name": site.get("site_name"),
        "municipality": site.get("municipality"),
        "department": site.get("department"),
        "phone": site.get("phone"),
        "nature": site.get("nature"),
        "level": site.get("care_level") or site.get("level"),
    }
    if "semantic_score" in site:
        card["semantic_score"] = site["semantic_score"]
    return card


def _envelope(results: list[dict[str, Any]], *, total: int | None = None, status: str | None = None) -> dict[str, Any]:
    return {
        "dataset_id": "s2ru-bqt6",
        "source": "datos.gov.co",
        "count": len(results),
        "total": len(results) if total is None else total,
        "results": results,
        "status": status or ("ok" if results else "no_results"),
    }


def _unavailable() -> dict[str, Any]:
    return _envelope([], total=0, status="soda_unavailable")


def _text(row: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = row.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _sites(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        code = _text(row, "c_digo_sede", "site_code")
        if not code:
            continue
        site = grouped.get(code)
        if site is None:
            site = {
                "site_code": code,
                "site_name": _text(row, "nom_sede_ips", "site_name"),
                "provider_name": _text(row, "nombre_prestador", "provider_name"),
                "municipality": _text(row, "municipio", "municipality"),
                "department": _text(row, "departamento", "department"),
                "phone": _text(row, "tel_fono", "phone"),
                "address": _text(row, "direcci_n", "address"),
                "email": _text(row, "email"),
                "nature": _text(row, "naturaleza", "nature"),
                "level": _text(row, "num_nivel_atencion", "care_level", "level"),
                "cutoff": _text(row, "fecha_corte", "cutoff"),
                "source": _text(row, "fuente", "source"),
                "capacities": [],
            }
            grouped[code] = site
        description = _text(row, "nom_descripcion_capacidad", "description")
        group = _text(row, "nom_grupo_capacidad", "group")
        quantity = row.get("num_cantidad_capacidad_instalada", row.get("registered_quantity"))
        if description or group or quantity is not None:
            site["capacities"].append(
                {"group": group, "description": description, "registered_quantity": quantity}
            )
    return list(grouped.values())


async def _rows(**kwargs: Any) -> tuple[list[dict[str, Any]] | None, str | None]:
    try:
        return await _fetch_rows(**kwargs), None
    except (IPSConfigurationError, Soda3Error):
        return None, "soda_unavailable"


async def search_ips(args: SearchIPSArgs, _context: ToolContext) -> dict[str, Any]:
    rows, failure = await _rows(
        query=args.query,
        department=args.department,
        municipality=args.municipality,
        nature=args.nature,
        kind=args.kind,
        capacity=args.capacity,
        page_size=min(500, max(args.limit * 20, 50)),
    )
    if failure or rows is None:
        return _unavailable()
    sites = _sites(rows)
    return _envelope([_card(site) for site in sites[: args.limit]], total=len(sites))


async def get_ips_details(args: IPSDetailsArgs, _context: ToolContext) -> dict[str, Any]:
    rows, failure = await _rows(site_code=args.site_code, page_size=200)
    if failure:
        return {"dataset_id": "s2ru-bqt6", "source": "datos.gov.co", "status": "soda_unavailable", "site": None}
    sites = _sites(rows or [])
    return {
        "dataset_id": "s2ru-bqt6",
        "source": "datos.gov.co",
        "status": "ok" if sites else "not_found",
        "site": sites[0] if sites else None,
    }


async def get_ips_capacity(args: IPSDetailsArgs, _context: ToolContext) -> dict[str, Any]:
    rows, failure = await _rows(site_code=args.site_code, page_size=200)
    if failure:
        return {
            "dataset_id": "s2ru-bqt6", "source": "datos.gov.co",
            "status": "soda_unavailable", "site_capacity": None, "warning": None,
        }
    sites = _sites(rows or [])
    if not sites:
        return {
            "dataset_id": "s2ru-bqt6", "source": "datos.gov.co",
            "status": "not_found", "site_capacity": None, "warning": None,
        }
    site = sites[0]
    capacity = {
        "site_code": site["site_code"],
        "site_name": site["site_name"],
        "cutoff": site["cutoff"],
        "source": site["source"],
        "capacities": site["capacities"],
    }
    return {
        "dataset_id": "s2ru-bqt6",
        "source": "datos.gov.co",
        "status": "ok",
        "site_capacity": capacity,
        "warning": "La capacidad instalada registrada no equivale a disponibilidad actual.",
    }


async def semantic_search_ips(
    args: SemanticSearchIPSArgs,
    _context: ToolContext,
) -> dict[str, Any]:
    rows, failure = await _rows(
        query=args.query,
        department=args.department,
        municipality=args.municipality,
        page_size=min(500, max(args.limit * 20, 50)),
    )
    if failure or rows is None:
        return _unavailable()
    sites = _sites(rows)
    return _envelope([_card(site) for site in sites[: args.limit]], total=len(sites))


async def compare_ips_capacity(args: CompareCapacityArgs, _context: ToolContext) -> dict[str, Any]:
    rows, failure = await _rows(
        site_codes=args.site_codes,
        capacity=args.capacity,
        page_size=200,
    )
    if failure or rows is None:
        return {**_unavailable(), "capacity": args.capacity, "sites": []}
    sites = [
        {
            "site_code": site["site_code"],
            "site_name": site["site_name"],
            "municipality": site["municipality"],
            "quantities": site["capacities"],
        }
        for site in _sites(rows)
    ]
    return {
        "dataset_id": "s2ru-bqt6",
        "source": "datos.gov.co",
        "status": "ok" if sites else "no_results",
        "capacity": args.capacity,
        "sites": sites,
        "total": len(sites),
        "warning": "La capacidad instalada registrada no equivale a disponibilidad actual.",
    }


def register_tools(registry: ToolRegistry) -> None:
    registry.register(
        ToolDefinition(
            "search_ips",
            "Build a SODA3 query from what the person said. Put the institution or site name in query, the city in municipality, the department in department, hospital or clinica in kind, publica or privada in nature, and the capacity name in capacity.",
            SearchIPSArgs,
            search_ips,
            "read",
            timeout_s=20.0,
        )
    )
    registry.register(
        ToolDefinition(
            "get_ips_details",
            "Get official SODA3 contact and location details for one IPS site code.",
            IPSDetailsArgs,
            get_ips_details,
            "read",
            timeout_s=20.0,
        )
    )
    registry.register(
        ToolDefinition(
            "get_ips_capacity",
            "Get SODA3 registered installed-capacity categories for one IPS site; this is not real-time availability.",
            IPSDetailsArgs,
            get_ips_capacity,
            "read",
            timeout_s=20.0,
        )
    )
    registry.register(
        ToolDefinition(
            "semantic_search_ips",
            "Search SODA3 by an approximate, descriptive, misspelled, or STT site or provider name. Do not use it for an exact name or a concrete location.",
            SemanticSearchIPSArgs,
            semantic_search_ips,
            "read",
            timeout_s=20.0,
        )
    )
    registry.register(
        ToolDefinition(
            "compare_ips_capacity",
            "Compare one SODA3 registered capacity category across specific site codes. Does not choose a winner.",
            CompareCapacityArgs,
            compare_ips_capacity,
            "read",
            timeout_s=20.0,
        )
    )


__all__ = [
    "CONTEXT_INSTRUCTIONS",
    "CompareCapacityArgs",
    "IPSDetailsArgs",
    "SearchIPSArgs",
    "SemanticSearchIPSArgs",
    "compare_ips_capacity",
    "get_ips_capacity",
    "get_ips_details",
    "register_tools",
    "search_ips",
    "semantic_search_ips",
]
