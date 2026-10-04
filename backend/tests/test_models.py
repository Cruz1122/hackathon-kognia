from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.dialects.postgresql import dialect as postgresql_dialect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.models import (
    Conversation,
    Message,
    MessageRole,
    Organization,
    User,
    UserRole,
)
from app.db.queries import create_conversation, get_conversation, list_messages


def test_models_create_expected_tables_foreign_keys_and_indexes() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    try:
        inspector = inspect(engine)
        assert set(inspector.get_table_names()) == {
            "agent_traces",
            "calls",
            "call_events",
            "conversations",
            "customers",
            "messages",
            "organizations",
            "objections",
            "opportunities",
            "product_interests",
            "products",
            "recordings",
            "transcript_segments",
            "users",
        }
        assert {
            (
                constraint["constrained_columns"][0],
                constraint["referred_table"],
                constraint["referred_columns"][0],
            )
            for constraint in inspector.get_foreign_keys("conversations")
        } == {
            ("organization_id", "organizations", "id"),
            ("created_by", "users", "id"),
            ("customer_id", "customers", "id"),
        }
        assert inspector.get_indexes("users")[0]["name"] == "ix_users_organization_id"
        assert {
            index["name"] for index in inspector.get_indexes("conversations")
        } == {"ix_conversations_organization_created_at"}
        assert {
            index["name"] for index in inspector.get_indexes("messages")
        } == {"ix_messages_conversation_created_at"}
        assert {
            index["name"] for index in inspector.get_indexes("customers")
        } == {"ix_customers_organization_id", "ix_customers_organization_phone"}
        assert {
            index["name"] for index in inspector.get_indexes("opportunities")
        } == {
            "ix_opportunities_organization_created_at",
            "ix_opportunities_organization_status_created_at",
            "ix_opportunities_organization_recovery_started_at",
            "ix_opportunities_conversation_id",
            "ix_opportunities_product_id",
        }
    finally:
        engine.dispose()


def test_model_relationships_and_defaults_persist_on_sqlite() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    try:
        organization = Organization(name="Acme", slug="acme")
        admin = User(
            organization=organization,
            email="admin@acme.test",
            password_hash="hash",
            role=UserRole.ADMIN,
        )
        conversation = Conversation(
            organization=organization,
            creator=admin,
            channel="web",
            status="open",
            messages=[
                Message(role=MessageRole.USER, content="Hola"),
                Message(role=MessageRole.ASSISTANT, content="¿En qué te ayudo?"),
            ],
        )

        with Session(engine) as session:
            session.add(conversation)
            session.commit()
            session.refresh(conversation)

            assert organization.id is not None
            assert admin.id is not None
            assert conversation.id is not None
            assert conversation.created_by == admin.id
            assert conversation.created_at is not None
            assert conversation.updated_at is not None
            assert [message.role for message in conversation.messages] == [
                MessageRole.USER,
                MessageRole.ASSISTANT,
            ]
    finally:
        engine.dispose()


def test_user_role_requires_matching_organization_scope() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    try:
        with Session(engine) as session:
            session.add(
                User(
                    email="invalid-admin.test",
                    password_hash="hash",
                    role=UserRole.ADMIN,
                )
            )
            with pytest.raises(IntegrityError):
                session.flush()
            session.rollback()

            session.add(
                User(
                    email="invalid-superadmin.test",
                    password_hash="hash",
                    role=UserRole.SUPERADMIN,
                    organization_id=uuid.uuid4(),
                )
            )
            with pytest.raises(IntegrityError):
                session.flush()
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_get_conversation_passes_tenant_filter() -> None:
    session = AsyncMock()
    expected = Conversation(id=uuid.uuid4())
    result = Mock()
    result.scalar_one_or_none.return_value = expected
    session.execute.return_value = result
    organization_id = uuid.uuid4()
    conversation_id = uuid.uuid4()

    assert (
        await get_conversation(
            session,
            organization_id=organization_id,
            conversation_id=conversation_id,
        )
        is expected
    )

    statement = session.execute.await_args.args[0]
    compiled = statement.compile(dialect=postgresql_dialect())
    assert "conversations.organization_id" in str(compiled)
    assert compiled.params["organization_id_1"] == organization_id
    assert compiled.params["id_1"] == conversation_id


@pytest.mark.asyncio
async def test_list_messages_joins_conversation_before_applying_tenant_filter() -> None:
    session = AsyncMock()
    result = Mock()
    result.scalars.return_value.all.return_value = []
    session.execute.return_value = result
    organization_id = uuid.uuid4()
    conversation_id = uuid.uuid4()

    assert (
        await list_messages(
            session,
            organization_id=organization_id,
            conversation_id=conversation_id,
        )
        == []
    )

    statement = session.execute.await_args.args[0]
    compiled = statement.compile(dialect=postgresql_dialect())
    sql = str(compiled)
    assert "JOIN conversations" in sql
    assert "conversations.organization_id" in sql
    assert compiled.params["organization_id_1"] == organization_id
    assert compiled.params["id_1"] == conversation_id


@pytest.mark.asyncio
async def test_create_conversation_rejects_foreign_admin() -> None:
    session = AsyncMock()
    session.add = Mock()
    creator = User(
        id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        email="foreign-admin@test.invalid",
        password_hash="hash",
        role=UserRole.ADMIN,
    )
    session.scalar.return_value = creator

    with pytest.raises(ValueError, match="does not belong"):
        await create_conversation(
            session,
            organization_id=uuid.uuid4(),
            created_by=creator.id,
            channel="web",
            status="open",
        )

    session.add.assert_not_called()
    session.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_conversation_allows_superadmin_creator() -> None:
    session = AsyncMock()
    session.add = Mock()
    creator = User(
        id=uuid.uuid4(),
        email="superadmin@test.invalid",
        password_hash="hash",
        role=UserRole.SUPERADMIN,
    )
    session.scalar.return_value = creator

    conversation = await create_conversation(
        session,
        organization_id=uuid.uuid4(),
        created_by=creator.id,
        channel="web",
        status="open",
    )

    assert conversation.created_by == creator.id
    assert conversation.organization_id is not None
    session.add.assert_called_once_with(conversation)
    session.flush.assert_awaited_once()
