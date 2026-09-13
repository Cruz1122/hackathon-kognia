import httpx
import pytest

from app import main


@pytest.mark.asyncio
async def test_health_live_does_not_require_model_readiness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(main, "sherpa_status", "error")
    monkeypatch.setattr(main, "tts_status", "starting")
    monkeypatch.setattr(main, "db_status", "error")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=main.app),
        base_url="http://test",
    ) as client:
        response = await client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "sherpa": "error",
        "tts": "starting",
        "db": "error",
    }


@pytest.mark.asyncio
async def test_health_ready_requires_sherpa_and_piper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(main, "sherpa_status", "ready")
    monkeypatch.setattr(main, "tts_status", "error")
    monkeypatch.setattr(main, "db_status", "ready")
    monkeypatch.setattr(main, "check_database", _database_ready)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=main.app),
        base_url="http://test",
    ) as client:
        response = await client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "ok",
        "sherpa": "ready",
        "tts": "error",
        "db": "ready",
    }


@pytest.mark.asyncio
async def test_health_ready_is_ok_when_both_models_are_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(main, "sherpa_status", "ready")
    monkeypatch.setattr(main, "tts_status", "ready")
    monkeypatch.setattr(main, "db_status", "ready")
    monkeypatch.setattr(main, "check_database", _database_ready)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=main.app),
        base_url="http://test",
    ) as client:
        response = await client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "sherpa": "ready",
        "tts": "ready",
        "db": "ready",
    }


@pytest.mark.asyncio
async def test_health_ready_requires_database(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main, "sherpa_status", "ready")
    monkeypatch.setattr(main, "tts_status", "ready")
    monkeypatch.setattr(main, "db_status", "error")
    monkeypatch.setattr(main, "check_database", _database_unavailable)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=main.app),
        base_url="http://test",
    ) as client:
        response = await client.get("/health/ready")

    assert response.status_code == 503
    assert response.json()["db"] == "error"


@pytest.mark.asyncio
async def test_health_ready_recovers_when_database_becomes_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(main, "sherpa_status", "ready")
    monkeypatch.setattr(main, "tts_status", "ready")
    monkeypatch.setattr(main, "db_status", "error")
    monkeypatch.setattr(main, "check_database", _database_ready)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=main.app),
        base_url="http://test",
    ) as client:
        response = await client.get("/health/ready")

    assert response.status_code == 200
    assert response.json()["db"] == "ready"


async def _database_ready() -> None:
    return None


async def _database_unavailable() -> None:
    raise RuntimeError("database unavailable")
