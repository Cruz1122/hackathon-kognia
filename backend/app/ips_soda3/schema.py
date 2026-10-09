"""Validated source rows and deterministic IPS normalization."""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


REQUIRED_SOURCE_COLUMNS = frozenset(
    {
        "departamento",
        "municipio",
        "c_digo_prestador",
        "nombre_prestador",
        "c_digo_sede",
        "n_mero_sede",
        "nom_sede_ips",
        "nom_grupo_capacidad",
        "nom_descripcion_capacidad",
        "num_cantidad_capacidad_instalada",
        "fecha_corte",
        "fuente",
    }
)


class IPSDatasetSchemaError(ValueError):
    """The upstream dataset cannot be normalized without losing identity."""


def _clean(value: object) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).strip().split())
    return text or None


class IPSSourceRow(BaseModel):
    """One real row from ``s2ru-bqt6``; source codes deliberately stay strings."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    department: str = Field(alias="departamento", min_length=1)
    municipality: str = Field(alias="municipio", min_length=1)
    provider_code: str = Field(alias="c_digo_prestador", min_length=1)
    provider_name: str = Field(alias="nombre_prestador", min_length=1)
    nit: str | None = Field(default=None, alias="nit_ips")
    verification_digit: str | None = Field(default=None, alias="num_digito_verificion")
    nature: str | None = Field(default=None, alias="naturaleza")
    care_level: str | None = Field(default=None, alias="num_nivel_atencion")
    site_code: str = Field(alias="c_digo_sede", min_length=1)
    site_number: str = Field(alias="n_mero_sede", min_length=1)
    site_name: str = Field(alias="nom_sede_ips", min_length=1)
    manager: str | None = Field(default=None, alias="gerente")
    address: str | None = Field(default=None, alias="direcci_n")
    email: str | None = None
    phone: str | None = Field(default=None, alias="tel_fono")
    capacity_group: str = Field(alias="nom_grupo_capacidad", min_length=1)
    capacity_description: str = Field(alias="nom_descripcion_capacidad", min_length=1)
    installed_capacity: int = Field(alias="num_cantidad_capacidad_instalada", ge=0)
    cutoff: str = Field(alias="fecha_corte", min_length=1)
    source: str = Field(alias="fuente", min_length=1)

    @field_validator(
        "department",
        "municipality",
        "provider_code",
        "provider_name",
        "nit",
        "verification_digit",
        "nature",
        "care_level",
        "site_code",
        "site_number",
        "site_name",
        "manager",
        "address",
        "email",
        "phone",
        "capacity_group",
        "capacity_description",
        "cutoff",
        "source",
        mode="before",
    )
    @classmethod
    def normalize_text(cls, value: object) -> str | None:
        return _clean(value)

    @field_validator("installed_capacity", mode="before")
    @classmethod
    def capacity_is_an_integer(cls, value: object) -> int:
        try:
            numeric = float(str(value).strip())
        except (TypeError, ValueError) as exc:
            raise ValueError("installed capacity must be numeric") from exc
        if not numeric.is_integer():
            raise ValueError("installed capacity must be an integer")
        return int(numeric)


@dataclass(frozen=True, slots=True)
class NormalizedCapacity:
    group_name: str
    description: str
    quantity: int
    raw_record: dict[str, Any]
    source_row_hash: str


@dataclass(frozen=True, slots=True)
class NormalizedSite:
    site_code: str
    provider_code: str
    provider_name: str
    nit: str | None
    verification_digit: str | None
    nature: str | None
    care_level: str | None
    site_number: str
    site_name: str
    manager: str | None
    address: str | None
    email: str | None
    phone: str | None
    department: str
    municipality: str
    cutoff: str
    source: str
    capacities: list[NormalizedCapacity]


@dataclass(frozen=True, slots=True)
class NormalizedDataset:
    source_hash: str
    row_count: int
    sites: list[NormalizedSite]
    cutoffs: list[str]
    sources: list[str]


def canonical_row(row: dict[str, Any]) -> str:
    return json.dumps(row, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def validate_source_rows(rows: list[dict[str, Any]]) -> list[tuple[IPSSourceRow, dict[str, Any], str]]:
    if not rows:
        raise IPSDatasetSchemaError("IPS_DATASET_EMPTY")
    missing = REQUIRED_SOURCE_COLUMNS - set(rows[0])
    if missing:
        raise IPSDatasetSchemaError("IPS_SCHEMA_MISSING_COLUMNS:" + ",".join(sorted(missing)))
    validated: list[tuple[IPSSourceRow, dict[str, Any], str]] = []
    for index, raw in enumerate(rows):
        if not isinstance(raw, dict):
            raise IPSDatasetSchemaError(f"IPS_ROW_NOT_OBJECT:{index}")
        try:
            parsed = IPSSourceRow.model_validate(raw)
        except Exception as exc:
            raise IPSDatasetSchemaError(f"IPS_ROW_INVALID:{index}:{exc}") from exc
        normalized_raw = {str(key): value for key, value in raw.items()}
        digest = hashlib.sha256(canonical_row(normalized_raw).encode()).hexdigest()
        validated.append((parsed, normalized_raw, digest))
    return validated


def _mode(rows: list[IPSSourceRow], field: str) -> str | None:
    values = [value for row in rows if (value := getattr(row, field))]
    if not values:
        return None
    counts = Counter(values)
    return min(counts, key=lambda value: (-counts[value], value.casefold()))


def normalize_dataset(rows: list[dict[str, Any]]) -> NormalizedDataset:
    """Consolidate one facility per official site code and preserve source rows."""
    validated = validate_source_rows(rows)
    unique_rows: dict[str, tuple[IPSSourceRow, dict[str, Any], str]] = {}
    for parsed, raw, digest in validated:
        unique_rows.setdefault(digest, (parsed, raw, digest))

    canonical_rows = sorted(canonical_row(item[1]) for item in validated)
    source_hash = hashlib.sha256(("\n".join(canonical_rows) + "\n").encode()).hexdigest()
    by_site: dict[str, list[tuple[IPSSourceRow, dict[str, Any], str]]] = defaultdict(list)
    for item in unique_rows.values():
        by_site[item[0].site_code].append(item)

    sites: list[NormalizedSite] = []
    for site_code, items in sorted(by_site.items()):
        parsed_rows = [item[0] for item in items]
        capacities = [
            NormalizedCapacity(
                group_name=item[0].capacity_group,
                description=item[0].capacity_description,
                quantity=item[0].installed_capacity,
                raw_record=item[1],
                source_row_hash=item[2],
            )
            for item in sorted(
                items,
                key=lambda value: (
                    value[0].capacity_group.casefold(),
                    value[0].capacity_description.casefold(),
                    value[0].installed_capacity,
                    value[2],
                ),
            )
        ]
        required_modes = {
            field: _mode(parsed_rows, field)
            for field in (
                "provider_code",
                "provider_name",
                "site_number",
                "site_name",
                "department",
                "municipality",
                "cutoff",
                "source",
            )
        }
        if any(value is None for value in required_modes.values()):
            raise IPSDatasetSchemaError(f"IPS_SITE_IDENTITY_INCOMPLETE:{site_code}")
        sites.append(
            NormalizedSite(
                site_code=site_code,
                provider_code=str(required_modes["provider_code"]),
                provider_name=str(required_modes["provider_name"]),
                nit=_mode(parsed_rows, "nit"),
                verification_digit=_mode(parsed_rows, "verification_digit"),
                nature=_mode(parsed_rows, "nature"),
                care_level=_mode(parsed_rows, "care_level"),
                site_number=str(required_modes["site_number"]),
                site_name=str(required_modes["site_name"]),
                manager=_mode(parsed_rows, "manager"),
                address=_mode(parsed_rows, "address"),
                email=_mode(parsed_rows, "email"),
                phone=_mode(parsed_rows, "phone"),
                department=str(required_modes["department"]),
                municipality=str(required_modes["municipality"]),
                cutoff=str(required_modes["cutoff"]),
                source=str(required_modes["source"]),
                capacities=capacities,
            )
        )
    return NormalizedDataset(
        source_hash=source_hash,
        row_count=len(validated),
        sites=sites,
        cutoffs=sorted({item[0].cutoff for item in unique_rows.values()}),
        sources=sorted({item[0].source for item in unique_rows.values()}),
    )


__all__ = [
    "IPSDatasetSchemaError",
    "IPSSourceRow",
    "NormalizedCapacity",
    "NormalizedDataset",
    "NormalizedSite",
    "REQUIRED_SOURCE_COLUMNS",
    "normalize_dataset",
    "validate_source_rows",
]
