from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

MAX_MESSAGES = 40
MAX_HITS = 8


def _text(value: Any) -> str:
    text = value if isinstance(value, str) else str(value)
    return text


def _messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    clipped: list[dict[str, Any]] = []
    for message in messages[:MAX_MESSAGES]:
        item: dict[str, Any] = {"role": message.get("role")}
        if message.get("content") is not None:
            item["content"] = _text(message.get("content"))
        if message.get("name"):
            item["name"] = message["name"]
        if message.get("tool_call_id"):
            item["tool_call_id"] = message["tool_call_id"]
        if message.get("tool_calls"):
            item["tool_calls"] = message["tool_calls"]
        clipped.append(item)
    return clipped


def _clip_hit(hit: Any) -> dict[str, Any]:
    metadata = getattr(hit, "metadata", None) or {}
    return {
        "chunk_id": getattr(hit, "chunk_id", None),
        "source": getattr(hit, "source", None),
        "score": getattr(hit, "score", None),
        "section": metadata.get("section") or metadata.get("heading_path"),
        "document": metadata.get("document_title") or metadata.get("source_filename"),
        "content": _text(getattr(hit, "content", "") or ""),
    }


@dataclass
class TraceRecorder:
    """Accumulates OTel-like spans for one agent turn.

    The recorder never raises into the agent loop: instrumentation is best-effort
    so the voice path stays intact even if tracing is unavailable.
    """

    call_id: str | None = None
    conversation_id: str | None = None
    organization_id: str | None = None
    trace_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    prompt: str = ""
    history: list[dict[str, Any]] = field(default_factory=list)
    history_total: int = 0
    tools_available: list[str] = field(default_factory=list)
    provider: str | None = None
    model: str | None = None
    status: str = "ok"
    answer: str = ""
    spans: list[dict[str, Any]] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    llm_calls: int = 0
    voice: dict[str, Any] = field(default_factory=dict)
    jev_usage: list[dict[str, Any]] = field(default_factory=list)

    _t0: float = field(default_factory=time.perf_counter)
    _turn: dict[str, Any] | None = None
    _finished: bool = False

    def _elapsed(self) -> int:
        return int((time.perf_counter() - self._t0) * 1000)

    @property
    def duration_ms(self) -> int:
        return self._elapsed()

    def start(self, prompt: str, history: list[dict[str, Any]] | None, tools_available: list[str]) -> None:
        self.prompt = prompt
        raw_history = list(history or [])
        self.history_total = len(raw_history)
        self.history = _messages(raw_history)
        self.tools_available = list(tools_available)
        # Transports initialize the recorder before entering the policy/runtime
        # coordinator.  The model path calls ``start`` again once it knows the
        # concrete tool list, so keep one root span for the complete turn.
        if self._turn is None:
            self._turn = self.span("agent.turn")

    def span(self, name: str, attributes: dict[str, Any] | None = None) -> dict[str, Any]:
        span: dict[str, Any] = {
            "name": name,
            "start_ms": self._elapsed(),
            "duration_ms": 0,
            "attributes": attributes or {},
        }
        self.spans.append(span)
        return span

    def close_span(self, span: dict[str, Any] | None, **attributes: Any) -> None:
        if span is None:
            return
        span["duration_ms"] = max(0, self._elapsed() - int(span["start_ms"]))
        if attributes:
            span["attributes"].update(attributes)
        if span.get("name") == "llm.request":
            # Keep an application-level response separate from the request
            # payload. Provider HTTP/SSE envelopes are intentionally not
            # persisted; this is the observable result consumed by replay.
            raw_response = span["attributes"].get("response")
            response = dict(raw_response) if isinstance(raw_response, dict) else {}
            response["text"] = _text(span["attributes"].get("text") or "")
            response["tool_calls"] = span["attributes"].get("tool_calls") or []
            usage = {
                key: int(span["attributes"][key])
                for key in ("prompt_tokens", "completion_tokens", "total_tokens")
                if isinstance(span["attributes"].get(key), (int, float))
            }
            if usage:
                response["usage"] = usage
            span["attributes"]["response"] = response

    def note_first_token(self, span: dict[str, Any] | None) -> None:
        if span is None:
            return
        attributes = span["attributes"]
        if attributes.get("first_token_ms") is None:
            attributes["first_token_ms"] = max(0, self._elapsed() - int(span["start_ms"]))

    def note_text(self, span: dict[str, Any] | None, text: str) -> None:
        if span is None:
            return
        attributes = span["attributes"]
        attributes["text"] = _text(str(attributes.get("text") or "") + text)

    def note_usage(
        self,
        span: dict[str, Any] | None,
        *,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
    ) -> None:
        prompt_tokens = max(0, int(prompt_tokens))
        completion_tokens = max(0, int(completion_tokens))
        total_tokens = max(0, int(total_tokens)) or prompt_tokens + completion_tokens
        if total_tokens == 0 and prompt_tokens == 0 and completion_tokens == 0:
            return
        self.prompt_tokens += prompt_tokens
        self.completion_tokens += completion_tokens
        self.total_tokens += total_tokens
        self.llm_calls += 1
        if span is not None:
            span["attributes"].update(
                {
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": total_tokens,
                }
            )

    def start_llm(
        self,
        *,
        provider: str,
        model: str,
        attempt: int,
        round_index: int,
        messages: list[dict[str, Any]],
        tools: list[str],
    ) -> dict[str, Any]:
        raw_messages = list(messages)
        captured_messages = _messages(raw_messages)
        attributes = {
            "provider": provider,
            "model": model,
            "attempt": attempt,
            "round": round_index,
            "messages": captured_messages,
            "messages_captured": len(captured_messages),
            "tools": list(tools),
            "first_token_ms": None,
            "tool_calls": [],
        }
        if len(raw_messages) > MAX_MESSAGES:
            attributes.update({"messages_limited": True, "messages_total": len(raw_messages)})
        return self.span(
            "llm.request",
            attributes,
        )

    def start_tool(self, *, name: str, tool_call_id: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return self.span(f"tool.{name}", {"tool_call_id": tool_call_id, "arguments": arguments})

    def close_tool(
        self,
        span: dict[str, Any] | None,
        *,
        result: str,
        ok: bool,
        inputs: list[dict[str, Any]],
        outputs: list[dict[str, Any]],
    ) -> None:
        self.close_span(span, result=_text(result), ok=ok, inputs=inputs, outputs=outputs)

    def record_retrieval(self, *, used_rag: bool, topic: str | None, hits: list[Any] | None) -> None:
        raw_hits = list(hits or [])
        captured_hits = [_clip_hit(hit) for hit in raw_hits[:MAX_HITS]]
        attributes: dict[str, Any] = {
            "used_rag": bool(used_rag),
            "topic": topic,
            "hits": captured_hits,
            "hits_captured": len(captured_hits),
        }
        if len(raw_hits) > MAX_HITS:
            attributes.update({"hits_limited": True, "hits_total": len(raw_hits)})
        span = self.span(
            "rag.retrieve",
            attributes,
        )
        self.close_span(span)

    def record_error(self, *, provider: str | None, model: str | None, attempt: int, message: str) -> None:
        span = self.span(
            "provider.error",
            {"provider": provider, "model": model, "attempt": attempt, "message": _text(message)},
        )
        self.close_span(span)

    def record_voice_timing(self, *, first_audio_ms: int | None, useful_answer_ms: int | None) -> None:
        """Keep perceived voice latency separate from model generation latency."""
        self.voice = {
            "time_to_first_audio_ms": first_audio_ms,
            "time_to_useful_answer_ms": useful_answer_ms,
        }

    def finish(self, *, answer: str, provider: str | None, model: str | None, status: str = "ok") -> None:
        if self._finished:
            return
        self._finished = True
        self.answer = answer
        self.provider = provider
        self.model = model
        self.status = status
        self.close_span(
            self._turn,
            provider=provider,
            model=model,
            status=status,
            tools_available=self.tools_available,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "call_id": self.call_id,
            "conversation_id": self.conversation_id,
            "started_at": self.started_at.isoformat(),
            "duration_ms": self.duration_ms,
            "provider": self.provider,
            "model": self.model,
            "status": self.status,
            "prompt": self.prompt,
            "history": self.history,
            "history_captured": len(self.history),
            **({"history_limited": True, "history_total": self.history_total} if self.history_total > MAX_MESSAGES else {}),
            "tools_available": self.tools_available,
            "answer": self.answer,
            "voice": self.voice,
            "jev_usage": list(self.jev_usage),
            "spans": self.spans,
            "usage": {
                "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens,
                "total_tokens": self.total_tokens,
                "llm_calls": self.llm_calls,
            },
        }
