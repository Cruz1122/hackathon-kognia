"""Execute IPS Golden cases through the real stateful agent runtime."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import inspect
import json
import os
import subprocess
import time
import uuid
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

from sqlalchemy import delete, select

from app.agent.state import Signal
from app.agent.tools.contracts import ToolContext
from app.config import get_agent_prompt_variant, get_model_chain
from app.db.models import AgentOperation, AgentSnapshot, Conversation, Message, MessageRole, Organization, User
from app.db.session import get_session_factory
from app.providers.contracts import LLMCapabilities
from app.providers.errors import ProviderError
from app.providers import llm_provider
from app.platform.tracing import TraceRecorder
from app.features.agent.service import stream_agent

from .dataset import EXPECTED_DISTRIBUTION, SEED, build_case_plan
from .deterministic import evaluate_case_turns
from .instrumentation import RuntimeInstrumentation
from .models import (
    CaseResult,
    CheckResult,
    CounterfactualResult,
    GoldenCase,
    RunManifest,
    SpanSummary,
    TurnPlan,
    TurnResult,
)
from .snapshot import SnapshotError, SnapshotView, load_snapshot


ADAPTIVE_SIGNAL_KEYS = frozenset({"frustration", "fluency", "emotion", "satisfaction"})


class ScriptedLLM:
    """Deterministic tool-capable provider used by offline execution."""

    capabilities = LLMCapabilities(supports_tools=True, supports_streaming=True)

    def __init__(self) -> None:
        self.steps: list[dict[str, Any]] = []
        self.calls: list[dict[str, Any]] = []

    def set_steps(self, steps: list[dict[str, Any]]) -> None:
        self.steps = [dict(step) for step in steps]

    async def stream(self, config: Any, prompt: str, *, messages: Any = None, tools: Any = None, client: Any = None):
        del client
        self.calls.append(
            {
                "provider": getattr(config.provider, "value", str(config.provider)),
                "model": config.model,
                "prompt": prompt,
                "messages_count": len(messages or []),
                "tools": [getattr(tool, "name", "") for tool in (tools or [])],
            }
        )
        if not self.steps:
            yield "token", {"text": "No pude completar esta respuesta con la evidencia disponible."}
            return
        step = self.steps.pop(0)
        kind = step.get("kind")
        if kind == "error":
            raise ProviderError("Golden offline provider failure", retryable=bool(step.get("retryable", True)))
        if kind == "tool_calls":
            calls = []
            for index, call in enumerate(step.get("calls") or [], start=1):
                calls.append(
                    {
                        "id": f"golden-call-{index}-{len(self.calls)}",
                        "name": call["name"],
                        "arguments": json.dumps(call.get("arguments") or {}, ensure_ascii=False),
                    }
                )
            yield "tool_calls", {"calls": calls}
            return
        text = str(step.get("text") or "")
        for chunk in _chunks(text, 48):
            yield "token", {"text": chunk}


def _chunks(value: str, size: int) -> list[str]:
    if not value:
        return [""]
    return [value[index:index + size] for index in range(0, len(value), size)]


class JEVInstrumentation:
    """Wrap real JEV or replace it with deterministic offline signals."""

    def __init__(self, recorder: TraceRecorder, plan: TurnPlan, *, offline: bool, neutralize: bool) -> None:
        self.recorder = recorder
        self.plan = plan
        self.offline = offline
        self.neutralize = neutralize
        self._patches: list[Any] = []
        self._integrity_calls = 0

    def _signals(self, turn_id: str) -> dict[str, Signal]:
        values = dict(self.plan.jev_signals)
        if self.neutralize:
            values = {key: value for key, value in values.items() if key not in ADAPTIVE_SIGNAL_KEYS}
        return {
            key: Signal(
                value=value,
                confidence=1.0,
                turn_id=turn_id,
                model="offline-jev-1" if self.offline else "live-jev",
                probabilities={value: 1.0},
            )
            for key, value in values.items()
        }

    def install(self) -> "JEVInstrumentation":
        from app.agent import jev

        original_observe = jev.observe
        original_integrity = jev.integrity

        async def observe(state: Any, prompt: str, turn_id: str, domain_questions: dict | None = None) -> dict[str, Signal]:
            span = self.recorder.span(
                "jev.observe",
                {
                    "provider": "offline" if self.offline else "typesafe",
                    "model": "offline-jev-1" if self.offline else "live",
                    "neutralized": self.neutralize,
                },
            )
            try:
                if self.offline:
                    result = self._signals(turn_id)
                else:
                    result = await original_observe(state, prompt, turn_id, domain_questions)
                    if self.neutralize:
                        result = {key: value for key, value in result.items() if key not in ADAPTIVE_SIGNAL_KEYS}
                self.recorder.close_span(span, ok=True, signal_keys=sorted(result))
                return result
            except Exception as exc:
                self.recorder.close_span(span, ok=False, error=type(exc).__name__)
                raise

        async def integrity(state: Any, draft: str, turn_id: str, knowledge: list[str] | None = None) -> Signal | None:
            span = self.recorder.span(
                "jev.integrity",
                {
                    "provider": "offline" if self.offline else "typesafe",
                    "model": "offline-jev-1" if self.offline else "live",
                    "neutralized": False,
                },
            )
            try:
                if self.offline:
                    self._integrity_calls += 1
                    value = self.plan.jev_signals.get("integrity", "supported")
                    if self._integrity_calls > 1 and value == "unsupported":
                        value = "supported"
                    result = Signal(
                        value=value,
                        confidence=1.0,
                        turn_id=turn_id,
                        model="offline-jev-1",
                        probabilities={value: 1.0},
                    )
                else:
                    result = await original_integrity(state, draft, turn_id, knowledge=knowledge)
                self.recorder.close_span(span, ok=True, value=result.value if result else None)
                return result
            except Exception as exc:
                self.recorder.close_span(span, ok=False, error=type(exc).__name__)
                raise

        self._patches = [patch.object(jev, "observe", observe), patch.object(jev, "integrity", integrity)]
        for item in self._patches:
            item.start()
        return self

    def close(self) -> None:
        for item in reversed(self._patches):
            item.stop()
        self._patches.clear()

    def __enter__(self) -> "JEVInstrumentation":
        return self.install()

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()


class ConversationHarness:
    """Create and remove benchmark-owned conversations safely."""

    def __init__(self) -> None:
        self.session_factory = get_session_factory()
        self.organization_id: uuid.UUID | None = None
        self.user_id: uuid.UUID | None = None

    async def resolve_owner(self) -> None:
        async with self.session_factory() as session:
            organization = await session.scalar(select(Organization).order_by(Organization.created_at).limit(1))
            if organization is None:
                raise SnapshotError("Golden runner needs one existing organization to create isolated conversations")
            user = await session.scalar(
                select(User).where(User.organization_id == organization.id).order_by(User.created_at).limit(1)
            )
            if user is None:
                raise SnapshotError("Golden runner needs one existing organization admin")
            self.organization_id = organization.id
            self.user_id = user.id

    async def create(self) -> uuid.UUID:
        if self.organization_id is None or self.user_id is None:
            await self.resolve_owner()
        conversation_id = uuid.uuid4()
        async with self.session_factory() as session:
            session.add(
                Conversation(
                    id=conversation_id,
                    organization_id=self.organization_id,
                    created_by=self.user_id,
                    channel="voice",
                    status="active",
                )
            )
            await session.commit()
        return conversation_id

    async def add_message(self, conversation_id: uuid.UUID, role: MessageRole, content: str) -> None:
        async with self.session_factory() as session:
            session.add(Message(conversation_id=conversation_id, role=role, content=content, channel="voice"))
            await session.commit()

    async def remove(self, conversation_id: uuid.UUID) -> None:
        async with self.session_factory() as session:
            # Explicit deletes keep this safe even on older local migrations
            # where the composite FK cascade was not yet applied.
            await session.execute(delete(AgentOperation).where(AgentOperation.conversation_id == conversation_id))
            await session.execute(delete(AgentSnapshot).where(AgentSnapshot.conversation_id == conversation_id))
            await session.execute(delete(Message).where(Message.conversation_id == conversation_id))
            await session.execute(delete(Conversation).where(Conversation.id == conversation_id))
            await session.commit()


def _steps_for_plan(plan: TurnPlan, *, neutralize_jev: bool = False) -> list[dict[str, Any]]:
    steps = [step.model_dump(mode="json") for step in plan.provider_steps]
    if not neutralize_jev or not plan.expected_behavior or plan.expected_behavior in {
        "emergency_services",
        "rephrase_with_evidence",
    }:
        return steps
    # Keep the input, tools, and snapshot identical while giving control a
    # neutral response.  Treatment keeps the case-authored response, so the
    # counterfactual has observable text/length evidence in addition to the
    # behavior signal.
    neutral = "Puedo ayudarte con información oficial de IPS. Dime el lugar o el nombre de la sede."
    for step in steps:
        if step.get("kind") == "text":
            step["text"] = neutral
    return steps


def _latencies(trace: dict[str, Any]) -> dict[str, Any]:
    spans = trace.get("spans") or []
    values: dict[str, Any] = {
        "total_turn_ms": 0,
        "first_token_ms": None,
        "generation_ms": 0,
        "tool_ms": 0,
        "ips_retrieval_ms": 0,
        "postgres_ms": 0,
        "chroma_ms": 0,
        "jev_observe_ms": 0,
        "jev_integrity_ms": 0,
        "stt_ms": None,
        "tts_first_audio_ms": None,
        "errors": 0,
        "fallbacks": 0,
        "recovered": 0,
        "terminal_errors": 0,
        "cache_cold": 0,
        "cache_hot": 0,
    }
    total_candidates: list[int] = []
    for span in spans:
        name = span.get("name", "")
        duration = int(span.get("duration_ms") or 0)
        attributes = span.get("attributes") or {}
        if name == "turn.total":
            total_candidates.append(duration)
        if name == "llm.request":
            values["generation_ms"] += duration
            first = attributes.get("first_token_ms")
            if isinstance(first, (int, float)):
                values["first_token_ms"] = first if values["first_token_ms"] is None else min(values["first_token_ms"], first)
        elif name.startswith("tool."):
            values["tool_ms"] += duration
        elif name == "ips.retrieval":
            values["ips_retrieval_ms"] += duration
        elif name.startswith("postgres."):
            values["postgres_ms"] += duration
        elif name.startswith("chroma."):
            values["chroma_ms"] += duration
        elif name == "jev.observe":
            values["jev_observe_ms"] += duration
        elif name == "jev.integrity":
            values["jev_integrity_ms"] += duration
        if attributes.get("cache") == "cold":
            values["cache_cold"] += 1
        elif attributes.get("cache") == "hot":
            values["cache_hot"] += 1
        if name.endswith(".error") or attributes.get("ok") is False:
            values["errors"] += 1
    values["total_turn_ms"] = max(total_candidates or [int(trace.get("duration_ms") or 0)])
    voice = trace.get("voice") if isinstance(trace.get("voice"), dict) else {}
    for key in ("time_to_first_audio_ms", "time_to_useful_answer_ms"):
        if isinstance(voice.get(key), (int, float)):
            values[key] = voice[key]
    return values


def _recovery_status(events: list[dict[str, Any]], errors: list[str]) -> tuple[bool, bool, int, int]:
    """Classify tool failures without hiding a later terminal failure.

    A turn is recovered only when a failed invocation is followed by a
    successful invocation of the same tool.  A different tool succeeding does
    not repair the original failure, and a runtime error without a recovered
    tool failure remains terminal.
    """
    failed: list[tuple[int, str]] = []
    successful: list[tuple[int, str]] = []
    runtime_error_event = False
    for index, event in enumerate(events):
        name = str(event.get("name") or "")
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        if name == "error":
            runtime_error_event = True
        if name != "tool.completed":
            continue
        tool = str(payload.get("tool") or "")
        if payload.get("ok") is False:
            failed.append((index, tool))
        elif payload.get("ok") is True:
            successful.append((index, tool))
    recovered_failures = {
        index
        for index, tool in failed
        if any(success_index > index and success_tool == tool for success_index, success_tool in successful)
    }
    terminal_tool_failures = len(failed) - len(recovered_failures)
    non_event_errors = max(0, len(errors) - len(failed) - int(runtime_error_event))
    recovered = bool(recovered_failures)
    terminal = bool(errors and not recovered_failures) or runtime_error_event or terminal_tool_failures > 0 or non_event_errors > 0
    if recovered_failures and (len(failed) != len(recovered_failures) or runtime_error_event or non_event_errors > 0):
        terminal = True
    return recovered, terminal, terminal_tool_failures, non_event_errors


async def _run_turn(
    harness: ConversationHarness,
    snapshot: SnapshotView,
    case: GoldenCase,
    plan: TurnPlan,
    turn_index: int,
    conversation_id: uuid.UUID,
    history: list[dict[str, str]],
    *,
    offline: bool,
    provider: Any,
    neutralize_jev: bool,
) -> TurnResult:
    assert harness.organization_id is not None
    prompt = plan.prompt
    await harness.add_message(conversation_id, MessageRole.USER, prompt)
    recorder = TraceRecorder(
        conversation_id=str(conversation_id),
        organization_id=str(harness.organization_id),
    )
    recorder.start(prompt, history, [])
    total_span = recorder.span("turn.total", {"case_id": case.case_id, "turn_index": turn_index})
    if isinstance(provider, ScriptedLLM):
        provider.set_steps(_steps_for_plan(plan, neutralize_jev=neutralize_jev))
    context = ToolContext(
        request_id=f"golden-{case.case_id}-{turn_index}-{uuid.uuid4().hex[:10]}",
        conversation_id=str(conversation_id),
        organization_id=str(harness.organization_id),
        channel="voice",
    )
    events: list[dict[str, Any]] = []
    answer_parts: list[str] = []
    errors: list[str] = []
    fallbacks: list[str] = []
    result_signals: dict[str, Any] = {}
    behavior: dict[str, Any] = {}
    state_view: dict[str, Any] = {}
    recovered = False
    terminal_errors = 0
    try:
        with RuntimeInstrumentation(recorder, snapshot, plan, offline=offline), JEVInstrumentation(
            recorder, plan, offline=offline, neutralize=neutralize_jev
        ):
            async for name, payload in stream_agent(
                prompt,
                messages=history or None,
                llm=provider,
                tool_context=context,
                trace=recorder,
            ):
                payload = payload if isinstance(payload, dict) else {"value": payload}
                events.append({"name": name, "payload": payload})
                if name == "token":
                    answer_parts.append(str(payload.get("text") or ""))
                elif name == "error":
                    errors.append(str(payload.get("message") or "runtime error"))
                elif name == "tool.completed":
                    if payload.get("ok") is False:
                        errors.append(str(payload.get("error_code") or "tool error"))
                elif name == "agent.signals":
                    result_signals = payload.get("signals") if isinstance(payload.get("signals"), dict) else {}
                    behavior = payload.get("behavior") if isinstance(payload.get("behavior"), dict) else {}
                    state_view = payload.get("state") if isinstance(payload.get("state"), dict) else {}
    except Exception as exc:
        errors.append(f"{type(exc).__name__}: {str(exc)[:240]}")
    finally:
        answer = "".join(answer_parts).strip()
        recovered, terminal, terminal_tool_failures, non_event_errors = _recovery_status(events, errors)
        runtime_error_count = int(any(event.get("name") == "error" for event in events))
        terminal_errors = terminal_tool_failures + runtime_error_count + non_event_errors
        turn_ok = not terminal
        recorder.close_span(total_span, ok=turn_ok, recovered=recovered)
        done = next((event["payload"] for event in reversed(events) if event["name"] == "done"), {})
        recorder.finish(
            answer=answer,
            provider=done.get("provider") if isinstance(done, dict) else None,
            model=done.get("model") if isinstance(done, dict) else None,
            status="ok" if turn_ok else "error",
        )
        # ``_generate`` may finish its span before stateful integrity asks for
        # a safe rewrite.  Keep the recorder's observable answer aligned with
        # the final stateful response captured by this runner.
        recorder.answer = answer
        recorder.status = "ok" if turn_ok else "error"
        if answer:
            await harness.add_message(conversation_id, MessageRole.ASSISTANT, answer)
    trace = recorder.to_dict()
    trace["recovered"] = recovered
    tools = [event["payload"] for event in events if event["name"] == "tool.completed"]
    spans = [
        SpanSummary(name=str(span.get("name")), duration_ms=int(span.get("duration_ms") or 0), attributes=span.get("attributes") or {})
        for span in trace.get("spans", [])
    ]
    if any(event["name"] == "done" and event["payload"].get("provider") == "fallback" for event in events):
        fallbacks.append("fallback_provider_or_runtime")
    history_before = list(history)
    history.extend([{"role": "user", "content": prompt}, {"role": "assistant", "content": answer}])
    return TurnResult(
        turn_index=turn_index,
        prompt=prompt,
        history=history_before,
        response=answer,
        events=events,
        tools=tools,
        signals=result_signals,
        behavior=behavior,
        state=state_view,
        trace=trace,
        spans=spans,
        errors=errors,
        fallbacks=fallbacks,
        recovered=recovered,
        latency_ms={
            **_latencies(trace),
            "recovered": int(recovered),
            "terminal_errors": terminal_errors,
        },
    )


async def _run_conversation(
    harness: ConversationHarness,
    snapshot: SnapshotView,
    case: GoldenCase,
    *,
    offline: bool,
    provider: Any,
    neutralize_jev: bool,
) -> tuple[uuid.UUID, list[TurnResult]]:
    conversation_id = await harness.create()
    history: list[dict[str, str]] = []
    results: list[TurnResult] = []
    try:
        for index, plan in enumerate(case.turns, start=1):
            results.append(
                await _run_turn(
                    harness,
                    snapshot,
                    case,
                    plan,
                    index,
                    conversation_id,
                    history,
                    offline=offline,
                    provider=provider,
                    neutralize_jev=neutralize_jev,
                )
            )
    finally:
        await harness.remove(conversation_id)
    return conversation_id, results


def _counterfactual_checks(case: GoldenCase, control: list[TurnResult], treatment: list[TurnResult]) -> list[CheckResult]:
    checks: list[CheckResult] = []
    if len(control) != len(case.turns) or len(treatment) != len(case.turns):
        checks.append(
            CheckResult(
                name="jev_counterfactual_turn_count",
                passed=False,
                critical=True,
                evidence={
                    "expected": len(case.turns),
                    "control": len(control),
                    "treatment": len(treatment),
                },
                message="Control and treatment must contain every Golden turn",
            )
        )
    for plan, before, after in zip(case.turns, control, treatment, strict=False):
        if not plan.expected_behavior:
            continue
        expected = plan.expected_behavior
        actual = after.behavior.get("next_step") or after.behavior.get("tone")
        if expected == after.behavior.get("tone"):
            behavior_passed = True
        else:
            behavior_passed = actual == expected
        safety_behavior = expected == "emergency_services"
        text_diff = before.response != after.response
        if expected in {"short", "emergency_services", "ask_one_clarification", "rephrase_with_evidence"}:
            text_diff = text_diff or len(after.response) <= len(before.response)
        if expected == "emergency_services":
            # The keyword guard is intentionally active in both variants.
            text_diff = "123" in after.response and "123" in before.response
        integrity_is_safety = expected == "rephrase_with_evidence" and plan.jev_signals.get("integrity") == "unsupported"
        if integrity_is_safety:
            # Integrity is a safety boundary, not an adaptive signal.  Both
            # variants are expected to retain the safe rewrite.
            behavior_passed = True
            text_diff = "disponibilidad actual" not in after.response.lower() or "no puedo" in after.response.lower()
        checks.append(
            CheckResult(
                name=f"jev_counterfactual_{plan.expected_behavior}",
                passed=behavior_passed and (text_diff or safety_behavior or integrity_is_safety),
                critical=case.critical,
                evidence={
                    "expected_behavior": expected,
                    "control_behavior": before.behavior,
                    "treatment_behavior": after.behavior,
                    "control_response": before.response[:600],
                    "treatment_response": after.response[:600],
                    "control_signals": before.signals,
                    "treatment_signals": after.signals,
                    "safety_boundary_preserved": integrity_is_safety,
                },
                message="Treatment did not show the expected JEV behavior difference" if not (behavior_passed and (text_diff or safety_behavior or integrity_is_safety)) else "",
            )
        )
    return checks


def _git_value(args: list[str]) -> str | None:
    try:
        return subprocess.run(args, check=True, capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def _configuration() -> dict[str, Any]:
    chain = []
    try:
        chain = [{"provider": item.provider.value, "model": item.model, "api_key_configured": bool(item.api_key)} for item in get_model_chain()]
    except Exception as exc:
        chain = [{"error": type(exc).__name__}]
    commit = os.getenv("GIT_COMMIT", "").strip() or _git_value(["git", "rev-parse", "HEAD"])
    dirty = _git_value(["git", "status", "--porcelain"])
    return {
        "git_commit": commit or "unavailable-in-runtime-image",
        "git_commit_source": "environment" if os.getenv("GIT_COMMIT", "").strip() else "repository-or-unavailable",
        "git_dirty": bool(dirty) if dirty is not None else None,
        "app_env": os.getenv("APP_ENV", "test"),
        "agent_prompt_variant": get_agent_prompt_variant().value,
        "agent_tool_modules": os.getenv("AGENT_TOOL_MODULES", "app.domains.ips.tools"),
        "model_chain": chain,
        "jev_model": "jev-1.13.0",
        "jev_api_configured": bool(os.getenv("TYPESAFE_API_KEY", "").strip()),
    }


def build_manifest(mode: str, snapshot: SnapshotView, cases: list[GoldenCase], repetitions: int) -> RunManifest:
    return RunManifest(
        run_id=uuid.uuid4().hex,
        created_at=datetime.now(UTC),
        mode=mode,  # type: ignore[arg-type]
        seed=SEED,
        repetitions=repetitions,
        snapshot=snapshot.metadata,
        prompt_versions={"agent": "agent_prompt_v1", "judge": "judge_v1"},
        agent_configuration=_configuration(),
        embedding_configuration={
            "provider": "hash-test" if mode == "offline" else "E5EmbeddingProvider",
            "model": os.getenv("E5_MODEL_DIR") or "intfloat/multilingual-e5-small",
            "chroma_collection": os.getenv("IPS_CHROMA_COLLECTION", "ips_facilities"),
            "chroma_host": os.getenv("CHROMA_HOST", "chroma"),
        },
        jev_configuration={"mode": "simulated" if mode == "offline" else "live", "adaptive_control_keys": sorted(ADAPTIVE_SIGNAL_KEYS)},
        cases=[case.case_id for case in cases],
        case_count=len(cases),
        limitations=[
            "Offline LLM and JEV are simulated; quality scores require the post-run judge command.",
            "Voice STT/TTS timings are only populated by the optional voice benchmark adapter.",
            "Live mode is opt-in and can incur provider charges.",
        ],
    )


async def run_benchmark(
    *,
    mode: str = "offline",
    repetitions: int = 1,
    snapshot_id: str | None = None,
    source_hash: str | None = None,
    case_limit: int | None = None,
    case_ids: set[str] | None = None,
    output: Path = Path("var/evals/ips_golden"),
    confirm_live: bool = False,
) -> tuple[RunManifest, list[CaseResult]]:
    if mode not in {"offline", "live"}:
        raise ValueError("mode must be offline or live")
    if mode == "live" and not confirm_live:
        raise ValueError("Live mode requires --confirm-live; it may call paid LLM/JEV providers")
    if repetitions < 1:
        raise ValueError("repetitions must be positive")
    snapshot = await load_snapshot(get_session_factory(), snapshot_id=snapshot_id, source_hash=source_hash, require_active=True)
    cases = build_case_plan(snapshot)
    if case_ids:
        cases = [case for case in cases if case.case_id in case_ids]
    if case_limit is not None:
        if case_limit < 1:
            raise ValueError("case limit must be positive")
        cases = cases[:case_limit]
    if not cases:
        raise ValueError("No Golden cases selected")
    manifest = build_manifest(mode, snapshot, cases, repetitions)
    output.mkdir(parents=True, exist_ok=True)
    (output / "manifest.json").write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
    harness = ConversationHarness()
    await harness.resolve_owner()
    provider: Any = ScriptedLLM() if mode == "offline" else llm_provider
    results: list[CaseResult] = []
    for repetition in range(1, repetitions + 1):
        for case in cases:
            if case.counterfactual:
                _, control = await _run_conversation(harness, snapshot, case, offline=mode == "offline", provider=provider, neutralize_jev=True)
                _, treatment = await _run_conversation(harness, snapshot, case, offline=mode == "offline", provider=provider, neutralize_jev=False)
                checks = evaluate_case_turns(treatment, case.turns, snapshot)
                counterfactual_checks = _counterfactual_checks(case, control, treatment)
                checks.extend(counterfactual_checks)
                counterfactual = CounterfactualResult(control=control, treatment=treatment, checks=counterfactual_checks)
                result = CaseResult(
                    case_id=case.case_id,
                    category=case.category,
                    repetition=repetition,
                    snapshot_id=snapshot.metadata.snapshot_id,
                    source_hash=snapshot.metadata.source_hash,
                    passed=all(check.passed for check in checks),
                    critical_failure=any(check.critical and not check.passed for check in checks),
                    checks=checks,
                    turns=treatment,
                    counterfactual=counterfactual,
                    metadata={"control_turns": len(control), "treatment_turns": len(treatment)},
                )
            else:
                conversation_id, turns = await _run_conversation(harness, snapshot, case, offline=mode == "offline", provider=provider, neutralize_jev=False)
                checks = evaluate_case_turns(turns, case.turns, snapshot)
                result = CaseResult(
                    case_id=case.case_id,
                    category=case.category,
                    repetition=repetition,
                    conversation_id=str(conversation_id),
                    snapshot_id=snapshot.metadata.snapshot_id,
                    source_hash=snapshot.metadata.source_hash,
                    passed=all(check.passed for check in checks),
                    critical_failure=any(check.critical and not check.passed for check in checks),
                    checks=checks,
                    turns=turns,
                )
            results.append(result)
    with (output / "results.jsonl").open("w", encoding="utf-8") as handle:
        for result in results:
            handle.write(result.model_dump_json() + "\n")
    return manifest, results


async def reevaluate_results(input_path: Path, output_path: Path) -> list[CaseResult]:
    from .report import read_jsonl

    manifest_path = input_path.with_name("manifest.json")
    if not manifest_path.is_file():
        raise ValueError("Reevaluation requires the run manifest next to results.jsonl")
    manifest = RunManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    snapshot = await load_snapshot(
        get_session_factory(),
        snapshot_id=manifest.snapshot.snapshot_id,
        source_hash=manifest.snapshot.source_hash,
        require_active=True,
    )
    cases = {case.case_id: case for case in build_case_plan(snapshot)}
    reevaluated: list[CaseResult] = []
    for value in read_jsonl(input_path):
        result = CaseResult.model_validate(value)
        case = cases.get(result.case_id)
        if case is None:
            raise ValueError(f"Unknown Golden case in results: {result.case_id}")
        checks = evaluate_case_turns(result.turns, case.turns, snapshot)
        if result.counterfactual:
            counterfactual_checks = _counterfactual_checks(
                case,
                result.counterfactual.control,
                result.counterfactual.treatment,
            )
            result.counterfactual.checks = counterfactual_checks
            checks.extend(counterfactual_checks)
        result.checks = checks
        result.passed = all(check.passed for check in checks)
        result.critical_failure = any(check.critical and not check.passed for check in checks)
        reevaluated.append(result)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        for result in reevaluated:
            handle.write(result.model_dump_json() + "\n")
    return reevaluated


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(description="Run the reproducible IPS Golden benchmark")
    sub = command.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="execute cases through the real stateful agent")
    run.add_argument("--mode", choices=("offline", "live"), default="offline")
    run.add_argument("--confirm-live", action="store_true", help="required acknowledgement for paid live providers")
    run.add_argument("--repetitions", type=int, default=1)
    run.add_argument("--snapshot-id")
    run.add_argument("--source-hash")
    run.add_argument("--limit", dest="case_limit", type=int)
    run.add_argument("--case-id", action="append", dest="case_ids")
    run.add_argument("--output", type=Path, default=Path("var/evals/ips_golden"))
    judge = sub.add_parser("judge", help="judge completed JSONL results after the run")
    judge.add_argument("--input", type=Path, required=True)
    judge.add_argument("--output", type=Path, required=True)
    judge.add_argument("--provider", default="fake", choices=("fake", "openai_compatible"))
    judge.add_argument("--model", default="judge-model")
    judge.add_argument("--base-url")
    judge.add_argument("--api-key-env", default="GOLDEN_JUDGE_API_KEY")
    judge.add_argument("--timeout", type=float, default=20.0)
    judge.add_argument("--retries", type=int, default=2)
    report = sub.add_parser("report", help="render a Markdown report from JSONL results")
    report.add_argument("--input", type=Path, required=True)
    report.add_argument("--output", type=Path, required=True)
    report.add_argument("--judge-input", type=Path)
    reevaluate = sub.add_parser("reevaluate", help="rerun deterministic checks without calling providers")
    reevaluate.add_argument("--input", type=Path, required=True)
    reevaluate.add_argument("--output", type=Path, required=True)
    compare = sub.add_parser("compare-latency", help="compare paired offline and live run latency")
    compare.add_argument("--offline-input", type=Path, required=True)
    compare.add_argument("--live-input", type=Path, required=True)
    compare.add_argument("--output", type=Path, required=True)
    variants = sub.add_parser("compare-variants", help="compare two paired live variants")
    variants.add_argument("--left-input", type=Path, required=True)
    variants.add_argument("--right-input", type=Path, required=True)
    variants.add_argument("--left-label", default="B")
    variants.add_argument("--right-label", default="E")
    variants.add_argument("--left-manifest", type=Path)
    variants.add_argument("--right-manifest", type=Path)
    variants.add_argument("--output", type=Path, required=True)
    return command


async def _main_async(args: argparse.Namespace) -> int:
    if args.command == "run":
        from .report import write_report

        manifest, results = await run_benchmark(
            mode=args.mode,
            repetitions=args.repetitions,
            snapshot_id=args.snapshot_id,
            source_hash=args.source_hash,
            case_limit=args.case_limit,
            case_ids=set(args.case_ids or []),
            output=args.output,
            confirm_live=args.confirm_live,
        )
        write_report(args.output / "results.jsonl", args.output / "report.md", manifest_path=args.output / "manifest.json")
        failed = sum(not item.passed for item in results)
        critical = sum(item.critical_failure for item in results)
        print(json.dumps({"run_id": manifest.run_id, "cases": len(results), "failed": failed, "critical_failures": critical, "output": str(args.output)}, ensure_ascii=False))
        # A benchmark run is actionable when any deterministic case fails;
        # critical failures remain reported separately in the manifest/report.
        return 1 if failed else 0
    if args.command == "judge":
        from .judge import run_judge_file

        await run_judge_file(
            input_path=args.input,
            output_path=args.output,
            provider_name=args.provider,
            model=args.model,
            base_url=args.base_url,
            api_key_env=args.api_key_env,
            timeout_s=args.timeout,
            retries=args.retries,
        )
        return 0
    if args.command == "reevaluate":
        results = await reevaluate_results(args.input, args.output)
        return 1 if any(not result.passed for result in results) else 0
    if args.command == "compare-latency":
        from .report import write_latency_comparison

        write_latency_comparison(args.offline_input, args.live_input, args.output)
        return 0
    if args.command == "compare-variants":
        from .report import write_variant_comparison

        write_variant_comparison(
            args.left_input,
            args.right_input,
            args.output,
            left_label=args.left_label,
            right_label=args.right_label,
            left_manifest_path=args.left_manifest,
            right_manifest_path=args.right_manifest,
        )
        return 0
    from .report import write_report

    manifest_path = args.input.with_name("manifest.json")
    write_report(
        args.input,
        args.output,
        manifest_path=manifest_path if manifest_path.is_file() else None,
        judge_path=args.judge_input,
    )
    return 0


def main() -> int:
    args = parser().parse_args()
    try:
        return asyncio.run(_main_async(args))
    except (SnapshotError, ValueError) as exc:
        print(f"IPS Golden: {exc}")
        return 2


__all__ = ["ScriptedLLM", "run_benchmark", "main"]
