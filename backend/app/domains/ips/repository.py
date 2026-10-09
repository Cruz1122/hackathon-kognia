"""PostgreSQL persistence and exact read queries for official IPS data."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from ...db.models import IPSCapacity, IPSSite, IPSSnapshot
from ...ips_soda3.client import DATASET_ID, SODA3_URL
from ...ips_soda3.schema import NormalizedDataset


SessionFactory = Callable[[], AsyncSession] | async_sessionmaker[AsyncSession]


def site_payload(site: IPSSite, *, include_capacities: bool = True) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "site_id": str(site.id),
        "snapshot_id": str(site.snapshot_id),
        "site_code": site.site_code,
        "site_number": site.site_number,
        "site_name": site.site_name,
        "provider_code": site.provider_code,
        "provider_name": site.provider_name,
        "nit": site.nit,
        "verification_digit": site.verification_digit,
        "nature": site.nature,
        "care_level": site.care_level,
        "department": site.department,
        "municipality": site.municipality,
        "address": site.address,
        "email": site.email,
        "phone": site.phone,
        "cutoff": site.cutoff,
        "source": site.source,
    }
    if include_capacities:
        payload["capacities"] = [
            {
                "group": capacity.group_name,
                "description": capacity.description,
                "registered_quantity": capacity.quantity,
                "source_row_hash": capacity.source_row_hash,
            }
            for capacity in sorted(
                site.capacities,
                key=lambda item: (item.group_name.casefold(), item.description.casefold()),
            )
        ]
        payload["capacity_warning"] = (
            "Las cantidades son capacidad instalada registrada en la fecha de corte; "
            "no indican disponibilidad actual."
        )
    return payload


class IPSRepository:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    async def snapshot_by_hash(self, source_hash: str) -> IPSSnapshot | None:
        async with self._session_factory() as session:
            return await session.scalar(
                select(IPSSnapshot).where(IPSSnapshot.source_hash == source_hash)
            )

    async def active_snapshot(self) -> IPSSnapshot | None:
        async with self._session_factory() as session:
            return await session.scalar(
                select(IPSSnapshot)
                .where(IPSSnapshot.status == "active")
                .order_by(IPSSnapshot.activated_at.desc())
            )

    async def delete_non_active_snapshot(self, snapshot_id: uuid.UUID) -> None:
        async with self._session_factory() as session:
            snapshot = await session.get(IPSSnapshot, snapshot_id)
            if snapshot is not None and snapshot.status != "active":
                await session.delete(snapshot)
                await session.commit()

    async def create_staging(
        self,
        dataset: NormalizedDataset,
        *,
        fetched_at: datetime,
    ) -> tuple[IPSSnapshot, list[IPSSite]]:
        snapshot_id = uuid.uuid4()
        snapshot = IPSSnapshot(
            id=snapshot_id,
            dataset_id=DATASET_ID,
            source_url=SODA3_URL,
            source_hash=dataset.source_hash,
            status="staging",
            source_row_count=dataset.row_count,
            site_count=len(dataset.sites),
            cutoff_values=dataset.cutoffs,
            source_values=dataset.sources,
            fetched_at=fetched_at,
        )
        site_rows: list[IPSSite] = []
        capacity_rows: list[IPSCapacity] = []
        for normalized in dataset.sites:
            site_id = uuid.uuid5(snapshot_id, normalized.site_code)
            site = IPSSite(
                id=site_id,
                snapshot_id=snapshot_id,
                site_code=normalized.site_code,
                provider_code=normalized.provider_code,
                provider_name=normalized.provider_name,
                nit=normalized.nit,
                verification_digit=normalized.verification_digit,
                nature=normalized.nature,
                care_level=normalized.care_level,
                site_number=normalized.site_number,
                site_name=normalized.site_name,
                manager=normalized.manager,
                address=normalized.address,
                email=normalized.email,
                phone=normalized.phone,
                department=normalized.department,
                municipality=normalized.municipality,
                cutoff=normalized.cutoff,
                source=normalized.source,
            )
            site_rows.append(site)
            for capacity in normalized.capacities:
                capacity_rows.append(
                    IPSCapacity(
                        id=uuid.uuid5(
                            site_id,
                            capacity.source_row_hash,
                        ),
                        site_id=site_id,
                        group_name=capacity.group_name,
                        description=capacity.description,
                        quantity=capacity.quantity,
                        raw_record=capacity.raw_record,
                        source_row_hash=capacity.source_row_hash,
                    )
                )
        async with self._session_factory() as session:
            session.add(snapshot)
            session.add_all(site_rows)
            session.add_all(capacity_rows)
            await session.commit()
        return snapshot, site_rows

    async def mark_failed(self, snapshot_id: uuid.UUID, error: str) -> None:
        async with self._session_factory() as session:
            await session.execute(
                update(IPSSnapshot)
                .where(IPSSnapshot.id == snapshot_id, IPSSnapshot.status == "staging")
                .values(status="failed", error=error[:255])
            )
            await session.commit()

    async def activate(self, snapshot_id: uuid.UUID) -> None:
        async with self._session_factory() as session:
            async with session.begin():
                target = await session.scalar(
                    select(IPSSnapshot).where(IPSSnapshot.id == snapshot_id).with_for_update()
                )
                if target is None or target.status != "staging":
                    raise RuntimeError("IPS_SNAPSHOT_NOT_STAGING")
                await session.execute(
                    update(IPSSnapshot)
                    .where(IPSSnapshot.status == "active")
                    .values(status="inactive")
                )
                target.status = "active"
                target.activated_at = datetime.now(UTC)
                target.error = None

    async def _active_id(self, session: AsyncSession) -> uuid.UUID | None:
        return await session.scalar(
            select(IPSSnapshot.id)
            .where(IPSSnapshot.status == "active")
            .order_by(IPSSnapshot.activated_at.desc())
        )

    async def search(
        self,
        *,
        query: str | None = None,
        department: str | None = None,
        municipality: str | None = None,
        nature: str | None = None,
        kind: str | None = None,
        capacity: str | None = None,
        limit: int = 10,
    ) -> tuple[list[dict[str, Any]], int] | None:
        async with self._session_factory() as session:
            snapshot_id = await self._active_id(session)
            if snapshot_id is None:
                return None
            filters = [IPSSite.snapshot_id == snapshot_id]
            if query:
                term = f"%{query.strip()}%"
                filters.append(or_(IPSSite.site_name.ilike(term), IPSSite.provider_name.ilike(term)))
            if department:
                filters.append(IPSSite.department.ilike(f"%{department.strip()}%"))
            if municipality:
                filters.append(IPSSite.municipality.ilike(f"%{municipality.strip()}%"))
            if nature == "publica":
                filters.append(or_(IPSSite.nature.ilike("%public%"), IPSSite.nature.ilike("%p_blic%")))
            elif nature == "privada":
                filters.append(or_(IPSSite.nature.ilike("%privad%"), IPSSite.nature.ilike("%pr_vad%")))
            if kind == "hospital":
                filters.append(IPSSite.site_name.ilike("%hospital%"))
            elif kind == "clinica":
                filters.append(or_(IPSSite.site_name.ilike("%clinic%"), IPSSite.site_name.ilike("%cl_nic%")))
            count = (
                select(func.count(func.distinct(IPSSite.id)))
                .select_from(IPSSite)
                .where(*filters)
            )
            statement = select(IPSSite).where(*filters).options(selectinload(IPSSite.capacities))
            if capacity:
                term = f"%{capacity.strip()}%"
                capacity_match = or_(IPSCapacity.group_name.ilike(term), IPSCapacity.description.ilike(term))
                count = count.join(IPSCapacity).where(capacity_match)
                statement = statement.join(IPSCapacity).where(capacity_match)
            total = int(await session.scalar(count) or 0)
            rows = list((await session.scalars(statement.order_by(IPSSite.site_name).distinct().limit(limit))).all())
            return [site_payload(row) for row in rows], total

    async def compare_capacity(self, site_codes: list[str], capacity: str) -> list[dict[str, Any]] | None:
        """Registered quantities of one category, per site. Does not sum groups or pick a winner."""
        async with self._session_factory() as session:
            snapshot_id = await self._active_id(session)
            if snapshot_id is None:
                return None
            rows = list((await session.scalars(
                select(IPSSite)
                .where(IPSSite.snapshot_id == snapshot_id, IPSSite.site_code.in_(site_codes[:10]))
                .options(selectinload(IPSSite.capacities))
                .order_by(IPSSite.site_name)
            )).all())
            needle = capacity.casefold()
            compared: list[dict[str, Any]] = []
            for site in rows:
                quantities = [
                    {
                        "group": item.group_name,
                        "description": item.description,
                        "registered_quantity": item.registered_quantity,
                    }
                    for item in site.capacities
                    if needle in f"{item.group_name} {item.description}".casefold()
                ]
                compared.append({
                    "site_code": site.site_code,
                    "site_name": site.site_name,
                    "municipality": site.municipality,
                    "quantities": quantities,
                })
            return compared

    async def details(self, site_code: str) -> dict[str, Any] | None:
        async with self._session_factory() as session:
            snapshot_id = await self._active_id(session)
            if snapshot_id is None:
                return None
            site = await session.scalar(
                select(IPSSite)
                .where(IPSSite.snapshot_id == snapshot_id, IPSSite.site_code == site_code)
                .options(selectinload(IPSSite.capacities))
            )
            return site_payload(site) if site else None

    async def capacities(self, site_code: str) -> dict[str, Any] | None:
        details = await self.details(site_code)
        if details is None:
            return None
        return {
            "site_code": details["site_code"],
            "site_name": details["site_name"],
            "cutoff": details["cutoff"],
            "source": details["source"],
            "capacities": details["capacities"],
            "warning": details["capacity_warning"],
        }

    async def sites_by_ids(self, site_ids: Sequence[str]) -> list[dict[str, Any]]:
        identifiers: list[uuid.UUID] = []
        for value in site_ids:
            try:
                identifiers.append(uuid.UUID(value))
            except ValueError:
                continue
        if not identifiers:
            return []
        async with self._session_factory() as session:
            snapshot_id = await self._active_id(session)
            if snapshot_id is None:
                return []
            sites = list(
                (
                    await session.scalars(
                        select(IPSSite)
                        .where(IPSSite.snapshot_id == snapshot_id, IPSSite.id.in_(identifiers))
                        .options(selectinload(IPSSite.capacities))
                    )
                ).all()
            )
            by_id = {site.id: site for site in sites}
            return [site_payload(by_id[item]) for item in identifiers if item in by_id]


__all__ = ["IPSRepository", "site_payload"]
