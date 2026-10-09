"""Read and query one frozen IPS snapshot without touching the live source."""

from __future__ import annotations

import unicodedata
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.db.models import IPSSite, IPSSnapshot

from .models import CapacityRecord, SiteRecord, SnapshotMetadata


class SnapshotError(RuntimeError):
    """The requested frozen snapshot cannot be used safely."""


def _fold(value: str | None) -> str:
    if not value:
        return ""
    normalized = unicodedata.normalize("NFD", value.casefold())
    return "".join(char for char in normalized if unicodedata.category(char) != "Mn")


def _contains(value: str | None, needle: str | None) -> bool:
    return not needle or _fold(needle.strip()) in _fold(value)


def _is_kind(site_name: str, kind: str | None) -> bool:
    if not kind:
        return True
    name = _fold(site_name)
    if kind == "hospital":
        return "hospital" in name
    if kind == "clinica":
        return "clinic" in name
    return False


def _is_nature(nature: str | None, expected: str | None) -> bool:
    if not expected:
        return True
    value = _fold(nature)
    if expected == "publica":
        return "public" in value
    if expected == "privada":
        return "privad" in value
    return False


def _site_record(site: IPSSite) -> SiteRecord:
    return SiteRecord(
        site_id=str(site.id),
        snapshot_id=str(site.snapshot_id),
        site_code=site.site_code,
        site_number=site.site_number,
        site_name=site.site_name,
        provider_code=site.provider_code,
        provider_name=site.provider_name,
        nit=site.nit,
        verification_digit=site.verification_digit,
        nature=site.nature,
        care_level=site.care_level,
        address=site.address,
        email=site.email,
        phone=site.phone,
        department=site.department,
        municipality=site.municipality,
        cutoff=site.cutoff,
        source=site.source,
        capacities=[
            CapacityRecord(
                group=item.group_name,
                description=item.description,
                registered_quantity=item.quantity,
                source_row_hash=item.source_row_hash,
            )
            for item in sorted(
                site.capacities,
                key=lambda item: (item.group_name.casefold(), item.description.casefold(), item.source_row_hash),
            )
        ],
    )


@dataclass(frozen=True, slots=True)
class SnapshotView:
    """An in-memory, immutable view of the selected PostgreSQL snapshot."""

    metadata: SnapshotMetadata
    sites: tuple[SiteRecord, ...]

    @classmethod
    def from_records(cls, metadata: SnapshotMetadata, sites: Iterable[SiteRecord]) -> "SnapshotView":
        values = tuple(sorted(sites, key=lambda item: (item.site_name.casefold(), item.site_code)))
        if not values:
            raise SnapshotError("IPS snapshot has no normalized sites")
        if any(item.snapshot_id != metadata.snapshot_id for item in values):
            raise SnapshotError("IPS snapshot site identity mismatch")
        if len(values) != metadata.site_count:
            raise SnapshotError(
                f"IPS snapshot site_count mismatch: expected {metadata.site_count}, got {len(values)}"
            )
        return cls(metadata=metadata, sites=values)

    @property
    def by_code(self) -> dict[str, SiteRecord]:
        return {item.site_code: item for item in self.sites}

    @property
    def by_id(self) -> dict[str, SiteRecord]:
        return {item.site_id: item for item in self.sites}

    def get(self, site_code: str) -> SiteRecord | None:
        return self.by_code.get(site_code)

    def details(self, site_code: str) -> dict[str, Any] | None:
        site = self.get(site_code)
        return site.detail() if site else None

    def capacities(self, site_code: str) -> dict[str, Any] | None:
        site = self.get(site_code)
        if site is None:
            return None
        return {
            "site_code": site.site_code,
            "site_name": site.site_name,
            "cutoff": site.cutoff,
            "source": site.source,
            "capacities": [item.model_dump(mode="json") for item in site.capacities],
            "warning": "Las cantidades son capacidad instalada registrada en la fecha de corte; no indican disponibilidad actual.",
        }

    def search(
        self,
        *,
        query: str | None = None,
        department: str | None = None,
        municipality: str | None = None,
        nature: str | None = None,
        kind: str | None = None,
        capacity: str | None = None,
        limit: int = 10,
    ) -> tuple[list[SiteRecord], int]:
        """Mirror the exact IPS repository filters over the frozen rows."""

        query_folded = _fold(query)
        values = []
        for site in self.sites:
            if query_folded and query_folded not in {_fold(site.site_name), _fold(site.provider_name)}:
                if query_folded not in _fold(site.site_name) and query_folded not in _fold(site.provider_name):
                    continue
            if not _contains(site.department, department):
                continue
            if not _contains(site.municipality, municipality):
                continue
            if not _is_nature(site.nature, nature):
                continue
            if not _is_kind(site.site_name, kind):
                continue
            if capacity:
                needle = _fold(capacity)
                if not any(
                    needle in f"{_fold(item.group)} {_fold(item.description)}"
                    for item in site.capacities
                ):
                    continue
            values.append(site)
        values.sort(key=lambda item: (item.site_name.casefold(), item.site_code))
        return values[:limit], len(values)

    def compare_capacity(self, site_codes: list[str], capacity: str) -> list[dict[str, Any]]:
        needle = _fold(capacity)
        values: list[dict[str, Any]] = []
        for code in site_codes[:10]:
            site = self.get(code)
            if site is None:
                continue
            values.append(
                {
                    "site_code": site.site_code,
                    "site_name": site.site_name,
                    "municipality": site.municipality,
                    "quantities": [
                        item.model_dump(
                            mode="json",
                            include={"group", "description", "registered_quantity"},
                        )
                        for item in site.capacities
                        if needle in f"{_fold(item.group)} {_fold(item.description)}"
                    ],
                }
            )
        values.sort(key=lambda item: item["site_name"].casefold())
        return values

    def homonyms(self, *, minimum: int = 2) -> list[tuple[str, list[SiteRecord]]]:
        groups: dict[str, list[SiteRecord]] = defaultdict(list)
        for site in self.sites:
            groups[site.site_name].append(site)
        return sorted(
            ((name, sorted(rows, key=lambda item: (item.municipality.casefold(), item.site_code)))
             for name, rows in groups.items() if len(rows) >= minimum),
            key=lambda item: (-len(item[1]), item[0].casefold()),
        )

    def sites_with_null(self, field: str) -> list[SiteRecord]:
        if field not in {"phone", "address", "email", "nature", "care_level"}:
            raise ValueError(f"Unsupported nullable IPS field: {field}")
        return [site for site in self.sites if getattr(site, field) is None]

    def sites_with_multiple_capacities(self, minimum: int = 2) -> list[SiteRecord]:
        return [site for site in self.sites if len(site.capacities) >= minimum]


async def load_snapshot(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    snapshot_id: str | None = None,
    source_hash: str | None = None,
    require_active: bool = False,
) -> SnapshotView:
    """Load one explicit snapshot and all its capacities from PostgreSQL.

    If no identity is supplied, the latest active snapshot is selected once and
    its identity is written to the run manifest.  The runner can then be
    replayed with that exact ``snapshot_id`` and ``source_hash``.
    """

    async with session_factory() as session:
        statement = select(IPSSnapshot)
        if snapshot_id:
            try:
                statement = statement.where(IPSSnapshot.id == UUID(snapshot_id))
            except ValueError as exc:
                raise SnapshotError("snapshot_id must be a UUID") from exc
        elif source_hash:
            statement = statement.where(IPSSnapshot.source_hash == source_hash)
        else:
            statement = (
                statement.where(IPSSnapshot.status == "active")
                .order_by(IPSSnapshot.activated_at.desc())
            )
        snapshot = await session.scalar(statement)
        if snapshot is None:
            raise SnapshotError("Requested IPS snapshot was not found in PostgreSQL")
        if source_hash and snapshot.source_hash != source_hash:
            raise SnapshotError(
                "snapshot_id and source_hash identify different IPS snapshots"
            )
        if snapshot.status in {"staging", "failed"}:
            raise SnapshotError(f"IPS snapshot is not immutable/usable: {snapshot.status}")
        if require_active and snapshot.status != "active":
            raise SnapshotError(
                "The stateful product runtime reads the active IPS snapshot; "
                "the selected snapshot is not active"
            )
        rows = list(
            (
                await session.scalars(
                    select(IPSSite)
                    .where(IPSSite.snapshot_id == snapshot.id)
                    .options(selectinload(IPSSite.capacities))
                )
            ).all()
        )

    metadata = SnapshotMetadata(
        dataset_id=snapshot.dataset_id,
        snapshot_id=str(snapshot.id),
        source_hash=snapshot.source_hash,
        status=snapshot.status,
        source_row_count=snapshot.source_row_count,
        site_count=snapshot.site_count,
        cutoff_values=list(snapshot.cutoff_values or []),
        source_values=list(snapshot.source_values or []),
        created_at=snapshot.created_at,
        fetched_at=snapshot.fetched_at,
        activated_at=snapshot.activated_at,
    )
    if len(rows) != snapshot.site_count:
        raise SnapshotError(
            f"IPS snapshot row count mismatch: metadata says {snapshot.site_count}, loaded {len(rows)}"
        )
    return SnapshotView.from_records(metadata, (_site_record(row) for row in rows))


__all__ = ["SnapshotError", "SnapshotView", "load_snapshot"]
