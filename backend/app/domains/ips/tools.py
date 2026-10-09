"""Read-only agent tools grounded in the active official IPS snapshot."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator

from ...config import AgentPromptVariant, get_agent_prompt_variant
from ...agent.tools.contracts import ToolContext, ToolDefinition
from ...agent.tools.registry import ToolRegistry
from ...db.session import get_session_factory
from ...platform.rag.runtime import embeddings as shared_embeddings
from .repository import IPSRepository
from .vector_store import IPSVectorStore


_BASELINE_CONTEXT_INSTRUCTIONS = (
    "Official Colombian IPS orientation domain. The tools read the active snapshot of dataset s2ru-bqt6. "
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
    "Official Colombian IPS registry, active snapshot only. Use search_ips for exact name, location, nature, type, "
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

repository = IPSRepository(get_session_factory())
vector_store = IPSVectorStore(embeddings=shared_embeddings)


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


def _missing_snapshot() -> dict[str, Any]:
    return _envelope([], total=0, status="no_active_snapshot")


async def search_ips(args: SearchIPSArgs, _context: ToolContext) -> dict[str, Any]:
    if await repository.active_snapshot() is None:
        return _missing_snapshot()
    found = await repository.search(
        query=args.query,
        department=args.department,
        municipality=args.municipality,
        nature=args.nature,
        kind=args.kind,
        capacity=args.capacity,
        limit=args.limit,
    )
    if found is None:
        return _missing_snapshot()
    rows, total = found if isinstance(found, tuple) else (found, len(found))
    return _envelope([_card(row) for row in rows], total=total)


async def get_ips_details(args: IPSDetailsArgs, _context: ToolContext) -> dict[str, Any]:
    if await repository.active_snapshot() is None:
        return {"dataset_id": "s2ru-bqt6", "source": "datos.gov.co", "status": "no_active_snapshot", "site": None}
    details = await repository.details(args.site_code)
    return {
        "dataset_id": "s2ru-bqt6",
        "source": "datos.gov.co",
        "status": "ok" if details else "not_found",
        "site": details,
    }


async def get_ips_capacity(args: IPSDetailsArgs, _context: ToolContext) -> dict[str, Any]:
    if await repository.active_snapshot() is None:
        return {
            "dataset_id": "s2ru-bqt6", "source": "datos.gov.co",
            "status": "no_active_snapshot", "site_capacity": None, "warning": None,
        }
    capacity = await repository.capacities(args.site_code)
    return {
        "dataset_id": "s2ru-bqt6",
        "source": "datos.gov.co",
        "status": "ok" if capacity else "not_found",
        "site_capacity": capacity,
        "warning": (
            "La capacidad instalada registrada no equivale a disponibilidad actual."
            if capacity
            else None
        ),
    }


async def semantic_search_ips(
    args: SemanticSearchIPSArgs,
    _context: ToolContext,
) -> dict[str, Any]:
    snapshot = await repository.active_snapshot()
    if snapshot is None:
        return _envelope([]) | {"status": "no_active_snapshot"}
    department, municipality = await repository.canonical_location(
        snapshot.id,
        department=args.department,
        municipality=args.municipality,
    )
    hits = await vector_store.search(
        args.query,
        snapshot_id=snapshot.id,
        department=department,
        municipality=municipality,
        limit=args.limit,
    )
    sites = await repository.sites_by_ids([hit.site_id for hit in hits])
    score_by_id = {hit.site_id: hit.score for hit in hits}
    for site in sites:
        site["semantic_score"] = score_by_id.get(site["site_id"], 0.0)
    return _envelope([_card(site) for site in sites])


async def compare_ips_capacity(args: CompareCapacityArgs, _context: ToolContext) -> dict[str, Any]:
    if await repository.active_snapshot() is None:
        return {**_missing_snapshot(), "capacity": args.capacity, "sites": []}
    sites = await repository.compare_capacity(args.site_codes, args.capacity)
    if sites is None:
        return {**_missing_snapshot(), "capacity": args.capacity, "sites": []}
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
            "Search by exact institution/provider name in query, location filters, or an exact registered category in capacity. Never put a service/capacity phrase in query.",
            SearchIPSArgs,
            search_ips,
            "read",
            timeout_s=10.0,
        )
    )
    registry.register(
        ToolDefinition(
            "get_ips_details",
            "Get exact official contact and location details for one IPS site code.",
            IPSDetailsArgs,
            get_ips_details,
            "read",
            timeout_s=10.0,
        )
    )
    registry.register(
        ToolDefinition(
            "get_ips_capacity",
            "Get registered installed-capacity categories for one IPS site; this is not real-time availability.",
            IPSDetailsArgs,
            get_ips_capacity,
            "read",
            timeout_s=10.0,
        )
    )
    registry.register(
        ToolDefinition(
            "semantic_search_ips",
            "Find likely IPS sites only when the name or wording is approximate, descriptive, misspelled, or affected by STT; do not use it for an exact name or concrete location supplied by the user; return exact PostgreSQL records.",
            SemanticSearchIPSArgs,
            semantic_search_ips,
            "read",
            timeout_s=20.0,
        )
    )
    registry.register(
        ToolDefinition(
            "compare_ips_capacity",
            "Compare one registered capacity category across specific site codes. Does not choose a winner.",
            CompareCapacityArgs,
            compare_ips_capacity,
            "read",
            timeout_s=10.0,
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
