from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock

import httpx
import jwt
import pytest
import pytest_asyncio

from app import main
from app.auth.passwords import hash_password, verify_password
from app.auth.tokens import JWT_ALGORITHM, JWT_TOKEN_TYPE, create_access_token
from app.db.models import Organization, User, UserRole
from app.db.session import get_db

TEST_SECRET = "test-jwt-secret-012345678901234567890123"


def _user(
    *,
    role: UserRole = UserRole.SUPERADMIN,
    organization_id: uuid.UUID | None = None,
    is_active: bool = True,
) -> User:
    return User(
        id=uuid.uuid4(),
        organization_id=organization_id,
        email=f"{uuid.uuid4().hex}@test.invalid",
        password_hash=hash_password("correct horse battery staple"),
        role=role,
        is_active=is_active,
    )


@pytest.fixture
def fake_db(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    session = AsyncMock()
    session.add = Mock()

    async def refresh(instance: object) -> None:
        if getattr(instance, "id", None) is None:
            instance.id = uuid.uuid4()
        if isinstance(instance, User) and instance.is_active is None:
            instance.is_active = True

    session.refresh.side_effect = refresh

    async def override_db():
        yield session

    monkeypatch.setattr(main.app, "dependency_overrides", {get_db: override_db})
    return session


@pytest_asyncio.fixture
async def client():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=main.app),
        base_url="http://test",
    ) as test_client:
        yield test_client


def _token(user: User, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("JWT_SECRET_KEY", TEST_SECRET)
    return create_access_token(user.id)


@pytest.mark.asyncio
async def test_password_hash_is_one_way_and_verifies() -> None:
    password = "correct horse battery staple"
    password_hash = hash_password(password)

    assert password_hash != password
    assert password_hash.startswith("$2b$")
    assert verify_password(password, password_hash)
    assert not verify_password("wrong password", password_hash)


@pytest.mark.asyncio
async def test_login_returns_access_token_with_user_subject(
    client: httpx.AsyncClient,
    fake_db: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JWT_SECRET_KEY", TEST_SECRET)
    user = _user()
    user.email = "admin@example.test"
    fake_db.scalar.return_value = user

    response = await client.post(
        "/auth/login",
        json={"email": " ADMIN@EXAMPLE.TEST ", "password": "correct horse battery staple"},
    )

    assert response.status_code == 200
    body = response.json()
    claims = jwt.decode(body["access_token"], TEST_SECRET, algorithms=[JWT_ALGORITHM])
    assert claims["sub"] == str(user.id)
    assert claims["type"] == JWT_TOKEN_TYPE
    assert claims["exp"] > datetime.now(UTC).timestamp()
    assert body["user"]["email"] == "admin@example.test"
    assert "password_hash" not in body["user"]


@pytest.mark.asyncio
async def test_login_rejects_wrong_password_with_uniform_error(
    client: httpx.AsyncClient,
    fake_db: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JWT_SECRET_KEY", TEST_SECRET)
    fake_db.scalar.return_value = _user()

    response = await client.post(
        "/auth/login",
        json={"email": fake_db.scalar.return_value.email, "password": "wrong password"},
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid email or password."}


@pytest.mark.asyncio
async def test_login_rejects_missing_user_without_leaking_credentials(
    client: httpx.AsyncClient,
    fake_db: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JWT_SECRET_KEY", TEST_SECRET)
    fake_db.scalar.return_value = None

    response = await client.post(
        "/auth/login",
        json={"email": "missing@example.test", "password": "correct horse battery staple"},
    )

    assert response.status_code == 401
    assert "correct horse battery staple" not in response.text
    assert TEST_SECRET not in response.text


@pytest.mark.asyncio
async def test_refresh_returns_new_access_token_for_active_user(
    client: httpx.AsyncClient,
    fake_db: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = _user(role=UserRole.ADMIN, organization_id=uuid.uuid4())
    fake_db.get.return_value = user
    token = _token(user, monkeypatch)

    response = await client.post("/auth/refresh", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    body = response.json()
    claims = jwt.decode(body["access_token"], TEST_SECRET, algorithms=[JWT_ALGORITHM])
    assert claims["sub"] == str(user.id)
    assert claims["type"] == JWT_TOKEN_TYPE
    assert body["expires_in"] > 0
    assert body["user"]["id"] == str(user.id)
    assert "password_hash" not in body["user"]


@pytest.mark.asyncio
async def test_refresh_rejects_missing_or_invalid_token(
    client: httpx.AsyncClient,
    fake_db: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JWT_SECRET_KEY", TEST_SECRET)

    missing = await client.post("/auth/refresh")
    invalid = await client.post("/auth/refresh", headers={"Authorization": "Bearer not-a-token"})

    assert missing.status_code == 401
    assert invalid.status_code == 401


@pytest.mark.asyncio
async def test_auth_me_accepts_valid_token_and_rejects_disabled_user(
    client: httpx.AsyncClient,
    fake_db: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = _user(role=UserRole.ADMIN, organization_id=uuid.uuid4())
    fake_db.get.return_value = user
    token = _token(user, monkeypatch)

    response = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert response.json()["id"] == str(user.id)
    assert response.json()["organization_id"] == str(user.organization_id)

    user.is_active = False
    disabled = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert disabled.status_code == 401
    assert disabled.json() == {"detail": "Invalid authentication credentials."}


@pytest.mark.asyncio
async def test_auth_me_rejects_invalid_and_expired_tokens(
    client: httpx.AsyncClient,
    fake_db: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = _user()
    fake_db.get.return_value = user
    monkeypatch.setenv("JWT_SECRET_KEY", TEST_SECRET)
    expired = jwt.encode(
        {
            "sub": str(user.id),
            "type": JWT_TOKEN_TYPE,
            "exp": datetime.now(UTC) - timedelta(minutes=1),
        },
        TEST_SECRET,
        algorithm=JWT_ALGORITHM,
    )

    invalid = await client.get("/auth/me", headers={"Authorization": "Bearer not-a-token"})
    expired_response = await client.get(
        "/auth/me", headers={"Authorization": f"Bearer {expired}"}
    )
    missing = await client.get("/auth/me")

    assert invalid.status_code == 401
    assert expired_response.status_code == 401
    assert missing.status_code == 401
    assert fake_db.get.await_count == 0


@pytest.mark.asyncio
async def test_auth_requires_configured_jwt_secret_without_logging_it(
    client: httpx.AsyncClient,
    fake_db: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    user = _user()
    fake_db.scalar.return_value = user

    response = await client.post(
        "/auth/login",
        json={"email": user.email, "password": "correct horse battery staple"},
    )

    assert response.status_code == 500
    assert response.json() == {"detail": "Authentication is not configured."}
    assert TEST_SECRET not in caplog.text


@pytest.mark.asyncio
async def test_only_superadmin_can_create_organization_or_admin(
    client: httpx.AsyncClient,
    fake_db: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = _user(role=UserRole.ADMIN, organization_id=uuid.uuid4())
    fake_db.get.return_value = admin
    token = _token(admin, monkeypatch)

    organization_response = await client.post(
        "/organizations",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Blocked", "slug": "blocked"},
    )
    admin_response = await client.post(
        f"/organizations/{uuid.uuid4()}/admins",
        headers={"Authorization": f"Bearer {token}"},
        json={"email": "blocked@example.test", "password": "correct horse battery staple"},
    )

    assert organization_response.status_code == 403
    assert admin_response.status_code == 403
    fake_db.add.assert_not_called()


@pytest.mark.asyncio
async def test_superadmin_can_create_organization_and_admin_without_returning_hash(
    client: httpx.AsyncClient,
    fake_db: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    superadmin = _user()
    organization = Organization(id=uuid.uuid4(), name="Acme", slug="acme")

    async def get(model: type[object], _object_id: uuid.UUID) -> object | None:
        if model is User:
            return superadmin
        if model is Organization:
            return organization
        return None

    fake_db.get.side_effect = get
    token = _token(superadmin, monkeypatch)

    organization_response = await client.post(
        "/organizations",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "  Acme   Support ", "slug": "Acme_Support"},
    )
    admin_response = await client.post(
        f"/organizations/{organization.id}/admins",
        headers={"Authorization": f"Bearer {token}"},
        json={"email": " ADMIN@ACME.TEST ", "password": "correct horse battery staple"},
    )

    assert organization_response.status_code == 201
    assert organization_response.json()["name"] == "Acme Support"
    assert organization_response.json()["slug"] == "acme-support"
    assert admin_response.status_code == 201
    assert admin_response.json()["email"] == "admin@acme.test"
    assert admin_response.json()["organization_id"] == str(organization.id)
    assert "password_hash" not in admin_response.json()
    created_admin = fake_db.add.call_args.args[0]
    assert isinstance(created_admin, User)
    assert created_admin.password_hash != "correct horse battery staple"
    assert verify_password("correct horse battery staple", created_admin.password_hash)
