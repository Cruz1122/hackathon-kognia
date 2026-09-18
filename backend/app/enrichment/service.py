from __future__ import annotations

import json
import logging
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..analytics.cache import bump_version
from ..config import get_model_chain
from ..db.models import Conversation, Message, Objection, Opportunity, Product, ProductInterest
from ..providers import llm_provider as default_llm
from ..providers.contracts import LLMProvider
from ..platform.queue import Job
from .schemas import EnrichmentResult

logger = logging.getLogger("hackathon.enrichment")


async def extract_insights(
    messages: list[dict[str, str]],
    products: list[dict[str, str]],
    *,
    llm: LLMProvider | None = None,
) -> EnrichmentResult:
    """Ask the model for signals only; commercial outcomes stay outside this function."""
    provider = llm or default_llm
    product_text = json.dumps(products, ensure_ascii=False)
    transcript = "\n".join(f"{item['role']}: {item['content']}" for item in messages)
    prompt = (
        "Extrae señales de esta conversación y devuelve únicamente JSON válido con estas claves: "
        "lost_reason (string o null), objections (array de {category, resolved}), "
        "product_interests (array de {product_id}). Categorías permitidas: price, competitor, timing, trust, features, other. "
        "No calcules métricas, no inventes importes y no cambies estados comerciales. "
        f"Productos disponibles: {product_text}\nTranscripción:\n{transcript}"
    )
    for config in get_model_chain():
        try:
            chunks: list[str] = []
            async for kind, payload in provider.stream(config, prompt, messages=None, tools=None):
                if kind == "token":
                    chunks.append(str(payload.get("text", "")))
            raw = "".join(chunks).strip()
            parsed = _json_object(raw)
            return EnrichmentResult.model_validate(parsed)
        except (ValidationError, json.JSONDecodeError, ValueError):
            logger.warning("LLM enrichment returned invalid structured output")
            continue
        except Exception:
            logger.exception("LLM enrichment attempt failed")
    raise RuntimeError("No valid enrichment output")


async def enrich_conversation(
    session_factory: async_sessionmaker[AsyncSession],
    job: Job,
    *,
    extractor: Any | None = None,
) -> None:
    async with session_factory() as session:
        conversation = await session.scalar(
            select(Conversation).where(
                Conversation.id == job.conversation_id,
                Conversation.organization_id == job.organization_id,
            ).with_for_update()
        )
        if conversation is None:
            return
        messages = list(
            (
                await session.scalars(
                    select(Message)
                    .where(Message.conversation_id == conversation.id)
                    .order_by(Message.created_at, Message.id)
                )
            ).all()
        )
        products = list(
            (
                await session.scalars(
                    select(Product).where(Product.organization_id == job.organization_id, Product.active.is_(True))
                )
            ).all()
        )
        product_ids = {product.id for product in products}
        if extractor is None:
            result = await extract_insights(
                [{"role": message.role.value, "content": message.content} for message in messages],
                [{"product_id": str(product.id), "name": product.name} for product in products],
            )
        else:
            result = await extractor(messages, products)
            if not isinstance(result, EnrichmentResult):
                result = EnrichmentResult.model_validate(result)

        invalid_products = [item.product_id for item in result.product_interests if item.product_id not in product_ids]
        if invalid_products:
            raise ValueError("Enrichment referenced an unavailable product")

        await session.execute(
            delete(Objection).where(
                Objection.organization_id == job.organization_id,
                Objection.conversation_id == conversation.id,
                Objection.source == "enrichment",
            )
        )
        unique_objections: dict[str, bool] = {}
        for objection in result.objections:
            unique_objections[objection.category] = objection.resolved
        now = datetime.now(UTC)
        session.add_all(
            [
                Objection(
                    organization_id=job.organization_id,
                    conversation_id=conversation.id,
                    category=category,
                    resolved=resolved,
                    resolved_at=now if resolved else None,
                    source="enrichment",
                )
                for category, resolved in unique_objections.items()
            ]
        )

        existing_interest_ids = set(
            (
                await session.scalars(
                    select(ProductInterest.product_id).where(
                        ProductInterest.organization_id == job.organization_id,
                        ProductInterest.conversation_id == conversation.id,
                    )
                )
            ).all()
        )
        unique_interest_ids = {item.product_id for item in result.product_interests}
        session.add_all(
            [
                ProductInterest(
                    organization_id=job.organization_id,
                    conversation_id=conversation.id,
                    product_id=product_id,
                )
                for product_id in unique_interest_ids
                if product_id not in existing_interest_ids
            ]
        )

        opportunity = await session.scalar(
            select(Opportunity).where(
                Opportunity.organization_id == job.organization_id,
                Opportunity.conversation_id == conversation.id,
            )
        )
        if opportunity is not None and opportunity.status == "lost":
            opportunity.lost_reason = result.lost_reason
        await bump_version(session, job.organization_id)
        await session.commit()


def _json_object(raw: str) -> dict[str, Any]:
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end < start:
        raise json.JSONDecodeError("object expected", raw, 0)
    value = json.loads(raw[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("object expected")
    return value


__all__ = ["EnrichmentResult", "enrich_conversation", "extract_insights"]
