from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.domains.ips.ingestion import IPSIngestionService
from app.domains.ips import tools as ips_tools
from app.domains.ips.vector_store import IPSVectorStore, semantic_document
from app.agent.tools.contracts import ToolContext
from app.ips_soda3.schema import IPSDatasetSchemaError, normalize_dataset


def source_row(**overrides):
    row = {
        "departamento": "Amazonas",
        "municipio": "LETICIA",
        "c_digo_prestador": "9100100019",
        "nombre_prestador": "E.S.E. HOSPITAL SAN RAFAEL DE LETICIA",
        "nit_ips": "838000096",
        "num_digito_verificion": "7",
        "naturaleza": "Pública",
        "num_nivel_atencion": "2",
        "c_digo_sede": "9100100019",
        "n_mero_sede": "01",
        "nom_sede_ips": "E.S.E. HOSPITAL SAN RAFAEL",
        "gerente": "GERENTE",
        "direcci_n": "CRA 10 # 13-78",
        "email": "hospital@example.test",
        "tel_fono": "1234567",
        "nom_grupo_capacidad": "CAMAS",
        "nom_descripcion_capacidad": "Adultos",
        "num_cantidad_capacidad_instalada": "4",
        "fecha_corte": "Fecha corte REPS: Nov 5 2022",
        "fuente": "REPS",
    }
    row.update(overrides)
    return row


def test_normalization_deduplicates_sites_and_preserves_capacity_categories() -> None:
    rows = [
        source_row(),
        source_row(),
        source_row(
            nom_grupo_capacidad="SALAS",
            nom_descripcion_capacidad="Procedimientos",
            num_cantidad_capacidad_instalada="2",
        ),
    ]
    dataset = normalize_dataset(rows)

    assert dataset.row_count == 3
    assert len(dataset.sites) == 1
    assert [(item.group_name, item.description, item.quantity) for item in dataset.sites[0].capacities] == [
        ("CAMAS", "Adultos", 4),
        ("SALAS", "Procedimientos", 2),
    ]
    assert dataset.sites[0].capacities[0].raw_record["c_digo_sede"] == "9100100019"


def test_incomplete_source_schema_is_rejected() -> None:
    row = source_row()
    row.pop("c_digo_sede")
    with pytest.raises(IPSDatasetSchemaError, match="IPS_SCHEMA_MISSING_COLUMNS:c_digo_sede"):
        normalize_dataset([row])


def test_conflicting_capacity_quantities_are_preserved_not_summed() -> None:
    dataset = normalize_dataset(
        [source_row(), source_row(num_cantidad_capacidad_instalada="9")]
    )
    assert [item.quantity for item in dataset.sites[0].capacities] == [4, 9]


@dataclass
class FakeSnapshot:
    id: uuid.UUID
    source_hash: str
    status: str
    site_count: int


class FakeRepository:
    def __init__(self) -> None:
        self.snapshots: dict[str, FakeSnapshot] = {}
        self.active: FakeSnapshot | None = None
        self.failed: list[uuid.UUID] = []

    async def snapshot_by_hash(self, source_hash):
        return self.snapshots.get(source_hash)

    async def active_snapshot(self):
        return self.active

    async def delete_non_active_snapshot(self, snapshot_id):
        self.snapshots = {
            key: value for key, value in self.snapshots.items() if value.id != snapshot_id
        }

    async def create_staging(self, dataset, *, fetched_at):
        snapshot = FakeSnapshot(uuid.uuid4(), dataset.source_hash, "staging", len(dataset.sites))
        self.snapshots[dataset.source_hash] = snapshot
        return snapshot, []

    async def mark_failed(self, snapshot_id, error):
        snapshot = next(item for item in self.snapshots.values() if item.id == snapshot_id)
        snapshot.status = "failed"
        self.failed.append(snapshot_id)

    async def activate(self, snapshot_id):
        target = next(item for item in self.snapshots.values() if item.id == snapshot_id)
        if self.active is not None:
            self.active.status = "inactive"
        target.status = "active"
        self.active = target


class FakeVectorStore:
    def __init__(self) -> None:
        self.counts: dict[uuid.UUID, int] = {}
        self.fail = False
        self.deleted: list[uuid.UUID] = []

    async def index_snapshot(self, snapshot_id, dataset):
        if self.fail:
            raise RuntimeError("chroma down")
        self.counts[snapshot_id] = len(dataset.sites)
        return len(dataset.sites)

    async def snapshot_count(self, snapshot_id):
        return self.counts.get(snapshot_id, 0)

    async def delete_snapshot(self, snapshot_id):
        self.deleted.append(snapshot_id)
        self.counts.pop(snapshot_id, None)


@pytest.mark.asyncio
async def test_ingestion_is_idempotent_and_updates_snapshot(monkeypatch) -> None:
    monkeypatch.setattr(IPSIngestionService, "_invalidate_version_cache", staticmethod(lambda: _noop()))
    repository = FakeRepository()
    vectors = FakeVectorStore()
    service = IPSIngestionService(repository, vectors)  # type: ignore[arg-type]

    first = await service.ingest_rows([source_row()], fetched_at=datetime.now(UTC))
    repeated = await service.ingest_rows([source_row()], fetched_at=datetime.now(UTC))
    updated = await service.ingest_rows(
        [source_row(num_cantidad_capacidad_instalada="5")],
        fetched_at=datetime.now(UTC),
    )

    assert first["status"] == "activated"
    assert repeated["status"] == "already_active"
    assert repeated["snapshot_id"] == first["snapshot_id"]
    assert updated["status"] == "activated"
    assert updated["snapshot_id"] != first["snapshot_id"]
    assert repository.active is not None and str(repository.active.id) == updated["snapshot_id"]


@pytest.mark.asyncio
async def test_chroma_failure_keeps_previous_snapshot_active(monkeypatch) -> None:
    monkeypatch.setattr(IPSIngestionService, "_invalidate_version_cache", staticmethod(lambda: _noop()))
    repository = FakeRepository()
    vectors = FakeVectorStore()
    service = IPSIngestionService(repository, vectors)  # type: ignore[arg-type]
    first = await service.ingest_rows([source_row()])
    previous_id = repository.active.id if repository.active else None
    vectors.fail = True

    with pytest.raises(RuntimeError, match="chroma down"):
        await service.ingest_rows([source_row(num_cantidad_capacidad_instalada="9")])

    assert previous_id is not None
    assert repository.active is not None and repository.active.id == previous_id
    assert str(repository.active.id) == first["snapshot_id"]
    assert repository.failed


class FakePageSource:
    async def list_all_pages(self, *, page_size, max_pages):
        yield {"page": 1, "data": [source_row()] * page_size}
        yield {"page": 2, "data": [source_row(c_digo_sede="2")]}


class TruncatedPageSource:
    async def list_all_pages(self, *, page_size, max_pages):
        for page in range(1, max_pages + 1):
            yield {"page": page, "data": [source_row()] * page_size}


class StalePageSource:
    async def list_all_pages(self, *, page_size, max_pages):
        yield {"page": 1, "data": [source_row()], "stale": True}


@pytest.mark.asyncio
async def test_fetch_all_collects_every_page_and_rejects_silent_truncation() -> None:
    service = IPSIngestionService(FakeRepository(), FakeVectorStore())  # type: ignore[arg-type]
    rows = await service.fetch_all(FakePageSource(), page_size=2, max_pages=3)
    assert len(rows) == 3
    with pytest.raises(RuntimeError, match="IPS_PAGINATION_LIMIT_REACHED"):
        await service.fetch_all(TruncatedPageSource(), page_size=2, max_pages=2)
    with pytest.raises(RuntimeError, match="IPS_STALE_SOURCE_PAGE"):
        await service.fetch_all(StalePageSource(), page_size=2, max_pages=2)


async def _noop() -> None:
    return None


class TinyEmbeddings:
    model_name = "tiny"

    def embed_passages(self, texts):
        return [[1.0, 0.0] for _ in texts]

    def embed_queries(self, texts):
        return [[1.0, 0.0] for _ in texts]


class FakeCollection:
    def __init__(self) -> None:
        self.items = {}
        self.last_where = None

    async def upsert(self, *, ids, documents, embeddings, metadatas):
        for values in zip(ids, documents, embeddings, metadatas):
            self.items[values[0]] = values[1:]

    async def get(self, *, where, include):
        ids = [key for key, value in self.items.items() if value[2]["snapshot_id"] == where["snapshot_id"]]
        return {"ids": ids, "metadatas": [self.items[key][2] for key in ids]}

    async def delete(self, *, where):
        self.items = {
            key: value for key, value in self.items.items() if value[2]["snapshot_id"] != where["snapshot_id"]
        }

    async def query(self, *, query_embeddings, n_results, where, include):
        self.last_where = where
        first = next(iter(self.items.values()))
        return {
            "documents": [[first[0]]],
            "metadatas": [[first[2]]],
            "distances": [[0.1]],
        }


class FakeChromaClient:
    def __init__(self) -> None:
        self.collection = FakeCollection()
        self.name = None

    async def get_or_create_collection(self, name, metadata):
        self.name = name
        return self.collection


@pytest.mark.asyncio
async def test_semantic_index_uses_separate_collection_and_snapshot_filters() -> None:
    client = FakeChromaClient()
    store = IPSVectorStore(client=client, embeddings=TinyEmbeddings(), batch_size=10)
    dataset = normalize_dataset([source_row()])
    snapshot_id = uuid.uuid4()

    assert await store.index_snapshot(snapshot_id, dataset) == 1
    hits = await store.search(
        "hospital con camas",
        snapshot_id=snapshot_id,
        department="Amazonas",
        municipality="LETICIA",
    )

    assert client.name == "ips_facilities"
    assert client.collection.last_where == {
        "$and": [
            {"snapshot_id": str(snapshot_id)},
            {"department": "Amazonas"},
            {"municipality": "LETICIA"},
        ]
    }
    assert hits[0].site_code == "9100100019"
    assert "no representan disponibilidad actual" in semantic_document(dataset.sites[0])


class FakeToolRepository:
    def __init__(self, results, *, snapshot=None):
        self.results = results
        self.snapshot = bool(results) if snapshot is None else snapshot

    async def search(self, **kwargs):
        return self.results, len(self.results)

    async def details(self, site_code):
        return self.results[0] if self.results else None

    async def capacities(self, site_code):
        return self.results[0] if self.results else None

    async def active_snapshot(self):
        return SimpleNamespace(id=uuid.uuid4()) if self.snapshot else None

    async def sites_by_ids(self, site_ids):
        return self.results


class FakeToolVectors:
    async def search(self, *args, **kwargs):
        return [SimpleNamespace(site_id="site-1", score=0.91)]


@pytest.mark.asyncio
async def test_structured_and_semantic_tools_return_exact_records(monkeypatch) -> None:
    site = {"site_id": "site-1", "site_code": "9100100019", "site_name": "Hospital"}
    monkeypatch.setattr(ips_tools, "repository", FakeToolRepository([site]))
    monkeypatch.setattr(ips_tools, "vector_store", FakeToolVectors())
    context = ToolContext("request-test")

    structured = await ips_tools.search_ips(ips_tools.SearchIPSArgs(query="Hospital"), context)
    semantic = await ips_tools.semantic_search_ips(
        ips_tools.SemanticSearchIPSArgs(query="hospital con camas"), context
    )

    assert structured["source"] == "datos.gov.co"
    assert structured["results"][0]["site_code"] == "9100100019"
    assert semantic["results"][0]["semantic_score"] == 0.91


@pytest.mark.asyncio
async def test_ips_tools_report_zero_results_without_fallback(monkeypatch) -> None:
    monkeypatch.setattr(ips_tools, "repository", FakeToolRepository([], snapshot=True))
    context = ToolContext("request-test")

    structured = await ips_tools.search_ips(ips_tools.SearchIPSArgs(query="No existe"), context)
    details = await ips_tools.get_ips_details(ips_tools.IPSDetailsArgs(site_code="missing"), context)
    missing = FakeToolRepository([], snapshot=False)
    monkeypatch.setattr(ips_tools, "repository", missing)
    semantic = await ips_tools.semantic_search_ips(
        ips_tools.SemanticSearchIPSArgs(query="No existe"), context
    )
    without_snapshot = await ips_tools.search_ips(ips_tools.SearchIPSArgs(query="No existe"), context)

    assert structured == {
        "dataset_id": "s2ru-bqt6",
        "source": "datos.gov.co",
        "count": 0,
        "total": 0,
        "results": [],
        "status": "no_results",
    }
    assert details["status"] == "not_found" and details["site"] is None
    assert semantic["status"] == "no_active_snapshot"
    assert without_snapshot["status"] == "no_active_snapshot"
    assert without_snapshot["total"] == 0
