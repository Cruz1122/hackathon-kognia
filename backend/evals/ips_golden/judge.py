"""Post-run, provider-independent LLM-as-a-Judge for IPS Golden output."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Protocol

import httpx
from pydantic import BaseModel, Field

from .models import CaseResult, JudgeScore
from .report import read_jsonl

PROMPT_VERSION = "judge_v1"
PROMPT_PATH = Path(__file__).with_name("prompts") / "judge_v1.md"


class JudgeConfig(BaseModel):
    provider: str = "fake"
    model: str = "judge-model"
    base_url: str | None = None
    api_key_env: str = "GOLDEN_JUDGE_API_KEY"
    timeout_s: float = Field(default=20.0, gt=0)
    retries: int = Field(default=2, ge=0, le=5)


class JudgeProvider(Protocol):
    async def score(self, payload: dict[str, Any]) -> dict[str, Any]: ...


def _prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def _judge_input(result: CaseResult) -> dict[str, Any]:
    """Provide conversation evidence without sending snapshot ground truth."""

    turns = result.turns
    if result.counterfactual:
        turns = result.counterfactual.treatment
    return {
        "case_id": result.case_id,
        "category": result.category,
        "turns": [
            {
                "prompt": turn.prompt,
                "history": turn.history,
                "response": turn.response,
                "signals": turn.signals,
                "behavior": turn.behavior,
                "events": [event.get("name") for event in turn.events],
            }
            for turn in turns
        ],
    }


class FakeJudge:
    """Offline judge for contract tests; no network and no factual override."""

    async def score(self, payload: dict[str, Any]) -> dict[str, Any]:
        turns = payload.get("turns") or []
        responses = [str(turn.get("response") or "") for turn in turns]
        nonempty = bool(responses) and all(responses)
        return {
            "clarity": 3 if nonempty else 1,
            "patient_usefulness": 3 if nonempty else 1,
            "context_continuity": 3 if len(turns) <= 1 or all(turn.get("history") is not None for turn in turns) else 1,
            "behavior_adaptation": 3 if any((turn.get("behavior") or {}).get("next_step") not in {None, "continue"} for turn in turns) else 2,
            "justification": "Evaluación offline de contrato; no determina la verdad factual del snapshot.",
        }


class OpenAICompatibleJudge:
    """Small HTTP client with an independent model/provider configuration."""

    def __init__(self, config: JudgeConfig) -> None:
        self.config = config
        if not config.base_url:
            raise ValueError("openai_compatible judge requires --base-url")
        self.api_key = os.getenv(config.api_key_env, "").strip()
        if not self.api_key:
            raise ValueError(f"{config.api_key_env} is not configured")

    async def score(self, payload: dict[str, Any]) -> dict[str, Any]:
        request = {
            "model": self.config.model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": _prompt()},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            "response_format": {"type": "json_object"},
        }
        url = self.config.base_url.rstrip("/") + "/chat/completions"
        timeout = httpx.Timeout(self.config.timeout_s)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(url, headers={"Authorization": f"Bearer {self.api_key}"}, json=request)
            response.raise_for_status()
            body = response.json()
        content = body["choices"][0]["message"]["content"]
        if isinstance(content, list):
            content = "".join(str(item.get("text") or "") for item in content if isinstance(item, dict))
        return _parse_json(str(content))


def _parse_json(value: str) -> dict[str, Any]:
    text = value.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError("judge response is not an object")
    return parsed


def _provider(config: JudgeConfig) -> JudgeProvider:
    if config.provider == "fake":
        return FakeJudge()
    if config.provider == "openai_compatible":
        return OpenAICompatibleJudge(config)
    raise ValueError(f"Unsupported judge provider: {config.provider}")


async def judge_one(result: CaseResult, config: JudgeConfig) -> JudgeScore:
    provider = _provider(config)
    payload = _judge_input(result)
    last_error: Exception | None = None
    for attempt in range(config.retries + 1):
        try:
            async with asyncio.timeout(config.timeout_s + 1):
                raw = await provider.score(payload)
            return JudgeScore(
                clarity=raw["clarity"],
                patient_usefulness=raw["patient_usefulness"],
                context_continuity=raw["context_continuity"],
                behavior_adaptation=raw["behavior_adaptation"],
                justification=str(raw["justification"])[:1200],
                provider=config.provider,
                model=config.model,
                prompt_version=PROMPT_VERSION,
            )
        except Exception as exc:
            last_error = exc
            if attempt < config.retries:
                await asyncio.sleep(min(0.5 * (2**attempt), 2.0))
    raise RuntimeError(f"judge failed after {config.retries + 1} attempt(s): {type(last_error).__name__}") from last_error


async def run_judge_file(
    *,
    input_path: Path,
    output_path: Path,
    provider_name: str = "fake",
    model: str = "judge-model",
    base_url: str | None = None,
    api_key_env: str = "GOLDEN_JUDGE_API_KEY",
    timeout_s: float = 20.0,
    retries: int = 2,
) -> None:
    config = JudgeConfig(
        provider=provider_name,
        model=model,
        base_url=base_url,
        api_key_env=api_key_env,
        timeout_s=timeout_s,
        retries=retries,
    )
    results = [CaseResult.model_validate(value) for value in read_jsonl(input_path)]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        for result in results:
            row: dict[str, Any] = {"case_id": result.case_id, "repetition": result.repetition}
            try:
                row["judge"] = (await judge_one(result, config)).model_dump(mode="json")
            except Exception as exc:
                row["judge"] = None
                row["error"] = type(exc).__name__
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


__all__ = ["JudgeConfig", "JudgeScore", "PROMPT_VERSION", "judge_one", "run_judge_file"]
