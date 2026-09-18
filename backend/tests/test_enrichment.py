from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock

import pytest

from app.enrichment.schemas import EnrichmentResult
from app.enrichment.service import _json_object, extract_insights


def test_enrichment_output_is_strict_and_validated() -> None:
    result = EnrichmentResult.model_validate({"lost_reason": "price", "objections": [{"category": "price", "resolved": True}], "product_interests": []})
    assert result.objections[0].resolved is True
    with pytest.raises(ValueError):
        EnrichmentResult.model_validate({"objections": [{"category": "made_up", "resolved": False}]})
    with pytest.raises(ValueError):
        EnrichmentResult.model_validate({"unknown": True})


def test_json_object_extracts_structured_output_only() -> None:
    assert _json_object('```json {"lost_reason":null} ```') == {"lost_reason": None}
    with pytest.raises(json.JSONDecodeError):
        _json_object("no object")


@pytest.mark.asyncio
async def test_extract_insights_validates_provider_output(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import ModelConfig, Provider

    class ProviderFake:
        async def stream(self, config, prompt, *, messages=None, tools=None):
            assert config.provider is Provider.OPENAI
            assert messages is None and tools is None and "No calcules métricas" in prompt
            yield "token", {"text": '{"lost_reason":null,"objections":[],"product_interests":[]}'}

    monkeypatch.setattr(
        "app.enrichment.service.get_model_chain",
        lambda: (ModelConfig(Provider.OPENAI, "test", "key", "http://test"),),
    )
    result = await extract_insights([{"role": "user", "content": "hola"}], [], llm=ProviderFake())
    assert result == EnrichmentResult()


@pytest.mark.asyncio
async def test_extract_insights_uses_next_provider_after_invalid_output(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import ModelConfig, Provider

    class ProviderFake:
        def __init__(self) -> None:
            self.calls = 0

        async def stream(self, config, prompt, *, messages=None, tools=None):
            del prompt, messages, tools
            self.calls += 1
            text = "not json" if self.calls == 1 else '{"lost_reason":null,"objections":[],"product_interests":[]}'
            yield "token", {"text": text}

    provider = ProviderFake()
    monkeypatch.setattr(
        "app.enrichment.service.get_model_chain",
        lambda: (
            ModelConfig(Provider.OPENAI, "first", "key", "http://test"),
            ModelConfig(Provider.OPENAI, "second", "key", "http://test"),
        ),
    )
    assert await extract_insights([], [], llm=provider) == EnrichmentResult()
    assert provider.calls == 2
