from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

MAX_TEXT = 2000
MAX_MESSAGES = 40
MAX_HITS = 8


def _clip(value: Any, limit: int = MAX_TEXT) -> str:
    text = value if isinstance(value, str) else str(value)
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


def _clip_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    clipped: list[dict[str, Any]] = []
    for message in messages[:MAX_MESSAGES]:
        item: dict[str, Any] = {"role": message.get("role")}
        if message.get("content") is not None:
            item["content"] = _clip(message.get("content"))
        if message.get("name"):
            item["name"] = message["name"]
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
        "content": _clip(getattr(hit, "content", "") or "", 600),
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

    _t0: float = field(default_factory=time.perf_counter)
    _turn: dict[str, Any] | None = None

    def _elapsed(self) -> int:
        return int((time.perf_counter() - self._t0) * 1000)

    @property
    def duration_ms(self) -> int:
        return self._elapsed()

    def start(self, prompt: str, history: list[dict[str, Any]] | None, tools_available: list[str]) -> None:
        self.prompt = prompt
        self.history = _clip_messages(list(history or []))
        self.tools_available = list(tools_available)
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
        attributes["text"] = _clip(str(attributes.get("text") or "") + text)

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
        return self.span(
            "llm.request",
            {
                "provider": provider,
                "model": model,
                "attempt": attempt,
                "round": round_index,
                "messages": _clip_messages(messages),
                "tools": list(tools),
                "first_token_ms": None,
                "tool_calls": [],
            },
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
        self.close_span(span, result=_clip(result), ok=ok, inputs=inputs, outputs=outputs)

    def record_retrieval(self, *, used_rag: bool, topic: str | None, hits: list[Any] | None) -> None:
        span = self.span(
            "rag.retrieve",
            {
                "used_rag": bool(used_rag),
                "topic": topic,
                "hits": [_clip_hit(hit) for hit in (hits or [])][:MAX_HITS],
            },
        )
        self.close_span(span)

    def record_error(self, *, provider: str | None, model: str | None, attempt: int, message: str) -> None:
        span = self.span(
            "provider.error",
            {"provider": provider, "model": model, "attempt": attempt, "message": _clip(message)},
        )
        self.close_span(span)

    def finish(self, *, answer: str, provider: str | None, model: str | None, status: str = "ok") -> None:
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
            "tools_available": self.tools_available,
            "answer": self.answer,
            "spans": self.spans,
            "usage": {
                "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens,
                "total_tokens": self.total_tokens,
                "llm_calls": self.llm_calls,
            },
        }
