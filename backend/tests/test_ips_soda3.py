from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from app.ips_soda3.cache import RedisCache
from app.ips_soda3.client import SODA3_URL, Soda3Client, Soda3Error
from app.ips_soda3.config import IPSSettings
from app.ips_soda3.service import IPSService, agent_where, literal, where_clause


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.ttls: dict[str, int] = {}
        self.failing = False

    async def get(self, key: str) -> str | None:
        if self.failing:
            raise ConnectionError("redis down")
        return self.values.get(key)

    async def set(self, key: str, value: str, ex: int) -> None:
        if self.failing:
            raise ConnectionError("redis down")
        self.values[key] = value
        self.ttls[key] = ex


async def service_from_handler(
    handler,
    redis: FakeRedis | None = None,
    attempts: int = 1,
) -> tuple[IPSService, httpx.AsyncClient]:
    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport)
    cache = RedisCache(redis or FakeRedis())
    service = IPSService(
        Soda3Client(app_token="test-token", http=http, max_attempts=attempts),
        cache,
    )
    return service, http


@pytest.mark.asyncio
async def test_cache_hit_avoids_second_post() -> None:
    hits: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == SODA3_URL
        assert request.method == "POST"
        assert request.headers["X-App-Token"] == "test-token"
        hits.append(json.loads(request.content))
        return httpx.Response(
            200,
            json=[{"departamento": "Caldas", "nombre_prestador": "Hospital X"}],
        )

    service, http = await service_from_handler(handler)
    try:
        first = await service.list_ips(departamento="Caldas", page_size=10)
        second = await service.list_ips(departamento="Caldas", page_size=10)
        assert first["cached"] is False
        assert second["cached"] is True and second["stale"] is False
        assert second["data"] == first["data"]
        assert len(hits) == 1
        assert hits[0]["page"] == {"pageNumber": 1, "pageSize": 10}
        assert hits[0]["includeSynthetic"] is False
    finally:
        await http.aclose()


@pytest.mark.asyncio
async def test_filters_and_pages_have_distinct_cache_keys() -> None:
    hits: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hits.append(json.loads(request.content))
        return httpx.Response(200, json=[{"id": str(len(hits))}])

    service, http = await service_from_handler(handler)
    try:
        await service.list_ips(departamento="Caldas", page=1)
        await service.list_ips(departamento="Caldas", page=2)
        await service.list_ips(departamento="Risaralda", page=1)
        assert len(hits) == 3
    finally:
        await http.aclose()


@pytest.mark.asyncio
async def test_empty_results_use_short_ttl() -> None:
    fake = FakeRedis()

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    service, http = await service_from_handler(handler, redis=fake)
    try:
        result = await service.list_ips()
        assert result["data"] == []
        fresh_keys = [key for key in fake.ttls if not key.endswith(":stale")]
        assert len(fresh_keys) == 1
        assert fake.ttls[fresh_keys[0]] == 300
        assert fake.ttls[fresh_keys[0] + ":stale"] == 86400
    finally:
        await http.aclose()


@pytest.mark.asyncio
async def test_stale_fallback_after_transient_upstream_error() -> None:
    calls = 0
    fake = FakeRedis()

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(200, json=[{"nombre_prestador": "X"}])
        return httpx.Response(503, json={"error": "maintenance"})

    service, http = await service_from_handler(handler, fake)
    try:
        live = await service.list_ips()
        fresh_keys = [key for key in fake.values if not key.endswith(":stale")]
        fake.values.pop(fresh_keys[0])
        stale = await service.list_ips()
        assert stale["cached"] and stale["stale"]
        assert stale["data"] == live["data"]
        assert calls == 2
    finally:
        await http.aclose()


@pytest.mark.asyncio
async def test_auth_error_is_not_retried_or_hidden_by_stale() -> None:
    fake = FakeRedis()
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(403)

    service, http = await service_from_handler(handler, fake, attempts=3)
    try:
        with pytest.raises(Soda3Error) as error:
            await service.list_ips()
        assert error.value.status_code == 403 and not error.value.transient
        assert calls == 1
    finally:
        await http.aclose()


@pytest.mark.asyncio
async def test_429_retries_then_recovers() -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429 if calls == 1 else 200, json=[])

    service, http = await service_from_handler(handler, attempts=2)
    try:
        value = await service.list_ips()
        assert value["data"] == []
        assert calls == 2
    finally:
        await http.aclose()


@pytest.mark.asyncio
async def test_redis_down_still_calls_upstream() -> None:
    fake = FakeRedis()
    fake.failing = True
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=[{"ok": True}])

    service, http = await service_from_handler(handler, fake)
    try:
        first = await service.list_ips()
        second = await service.list_ips()
        assert first["data"] == [{"ok": True}]
        assert second["data"] == [{"ok": True}]
        assert calls == 2
    finally:
        await http.aclose()


@pytest.mark.asyncio
async def test_count_is_labeled_as_rows_not_unique_ips() -> None:
    payloads: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(request.content))
        return httpx.Response(200, json=[{"total": "41023"}])

    service, http = await service_from_handler(handler)
    try:
        result = await service.count_records(departamento="Caldas")
        assert result["total_registros"] == 41023
        assert result["metric"] == "filas_del_dataset_no_ips_unicas"
        assert "page" not in payloads[0]
        assert "WHERE departamento = 'Caldas'" in payloads[0]["query"]
    finally:
        await http.aclose()


@pytest.mark.asyncio
async def test_admin_iterator_stops_at_short_page() -> None:
    pages: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        page_number = body["page"]["pageNumber"]
        pages.append(page_number)
        rows = [{"id": 1}, {"id": 2}] if page_number == 1 else [{"id": 3}]
        return httpx.Response(200, json=rows)

    service, http = await service_from_handler(handler)
    try:
        results = [result async for result in service.list_all_pages(page_size=2)]
        assert pages == [1, 2]
        assert [len(result["data"]) for result in results] == [2, 1]
    finally:
        await http.aclose()


def test_agent_where_searches_names_without_vector_filters() -> None:
    clause = agent_where(query="Hospital", municipality="Leticia", site_code="9100100019")
    assert "nombre_prestador" in clause
    assert "nom_sede_ips" in clause
    assert "c_digo_sede = '9100100019'" in clause
    assert "chroma" not in clause


def test_literal_escaping_and_safe_filters() -> None:
    assert literal("O'Higgins") == "'O''Higgins'"
    assert "departamento = 'O''Higgins'" in where_clause(departamento="O'Higgins")
    with pytest.raises(ValueError):
        where_clause(nombre="%")
    with pytest.raises(ValueError):
        literal("A\nB")
    with pytest.raises(ValueError):
        literal(" " * 101)


@pytest.mark.asyncio
async def test_invalid_page_is_rejected_before_network_call() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("no se debe hacer HTTP")

    service, http = await service_from_handler(handler)
    try:
        with pytest.raises(ValueError):
            await service.list_ips(page=0)
        with pytest.raises(ValueError):
            await service.list_ips(page_size=501)
    finally:
        await http.aclose()


@pytest.mark.asyncio
async def test_concurrent_identical_miss_makes_one_post() -> None:
    hits = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal hits
        hits += 1
        await asyncio.sleep(0.01)
        return httpx.Response(200, json=[{"nombre_prestador": "X"}])

    service, http = await service_from_handler(handler)
    try:
        results = await asyncio.gather(*(service.list_ips(departamento="Caldas") for _ in range(10)))
        assert hits == 1
        assert sum(not result["cached"] for result in results) == 1
    finally:
        await http.aclose()


def test_settings_accept_user_key_and_official_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("API_KEY_SODA3", raising=False)
    monkeypatch.setenv("SODA_APP_TOKEN", "official-token")
    assert IPSSettings.from_env().app_token == "official-token"
    monkeypatch.setenv("API_KEY_SODA3", "user-token")
    assert IPSSettings.from_env().app_token == "user-token"
