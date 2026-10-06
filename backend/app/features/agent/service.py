import re
from collections.abc import AsyncIterator, Sequence
from datetime import timedelta
from typing import Any

from ...config import AppEnv, Provider, get_app_env, get_model_chain
from ...providers import ProviderError, llm_provider as default_llm
from ...providers.contracts import LLMProvider
from ...agent.tools.contracts import ToolContext
from ...agent.tools.loader import load_tool_registry
from ...platform.rag.chunking import chunk_heading
from ...platform.rag.citations import build_knowledge_context
from ...platform.rag.contracts import RetrievalHit
from ...platform.rag.runtime import retriever as rag_retriever
from .tools import (
    CANONICAL_TOOLS,
    describe_tool_done,
    describe_tool_start,
    parse_arguments,
    present_tool_inputs,
    present_tool_outputs,
)

Message = dict[str, Any]
MAX_TOOL_ROUNDS = 4
# Ventana de contexto enviada al proveedor, medida en turnos completos: cada
# turno empieza en un mensaje "user" y arrastra lo que venga hasta el siguiente
# "user" (así los fragmentos de habla consecutivos no rompen la ventana).
MAX_CONTEXT_TURNS = 6
TOOL_REGISTRY = load_tool_registry()
_RAG_STOP_WORDS = frozenset(
    "a al algo aquí asistirte avísame avisame caracter caracteres claro como con "
    "contenido cuando de del dime donde el en encontré encontre es esta este "
    "ejemplo generar hay hola hoy información informacion la las listo lo lorem "
    "ipsum los más me necesitas para por pregunta puedes puedo que qué realizar "
    "se sección seccion si siguiente su sus te texto textos tu tus un una y".split()
)
_CLARIFICATION_MARKERS = (
    "mensaje se cortó",
    "no entendí",
    "no te entendí",
    "puedes aclarar",
    "puedes completar",
    "puedes repetir",
    "qué necesitas",
)
_CAPABILITY_MARKERS = (
    "puedo ayudarte",
    "estoy aquí listo",
    "en qué puedo",
    "en que puedo",
    "qué puedo hacer",
    "que puedo hacer",
    "si tienes alguna pregunta",
)
_MIN_KNOWLEDGE_TERMS = 2


def normalize_relative_booking_date(
    tool: str,
    arguments: dict[str, Any],
    prompt: str,
    history: Sequence[Message] | None = None,
) -> dict[str, Any]:
    """Anchor an explicit relative booking date to the configured demo timezone.

    Models can use their provider's UTC date instead of the local operational
    date even when the latter is in the system context. Only an unambiguous date
    expression in the current message overrides the model argument. A bare
    ``mañana`` answering an AM/PM question keeps its time-of-day meaning.
    """
    if tool not in {'check_availability', 'create_booking'} or 'date' not in arguments:
        return arguments
    folded = ' '.join(prompt.casefold().split())
    previous = next((str(item.get('content') or '').casefold() for item in reversed(history or [])
                     if item.get('role') == 'assistant'), '')
    am_pm_answer = folded in {'mañana', 'de mañana', 'por la mañana'} and (
        'de la mañana o de la noche' in previous or 'mañana o de la noche' in previous
    )
    days: int | None = None
    if re.search(r'\bpasado\s+mañana\b', folded):
        days = 2
    elif not am_pm_answer and (
        re.search(r'\bpara\s+(?:el\s+)?(?:d[ií]a\s+de\s+)?mañana\b', folded)
        or re.search(r'\bmañana\s*(?:,|a\s+la|a\s+las)\b', folded)
        or folded == 'mañana'
    ):
        days = 1
    elif re.search(r'\bpara\s+(?:el\s+)?(?:d[ií]a\s+de\s+)?hoy\b', folded):
        days = 0
    if days is None:
        return arguments
    from ...agent.state import local_now
    return {**arguments, 'date': (local_now().date() + timedelta(days=days)).isoformat()}


def context_window(
    messages: Sequence[Message] | None,
    limit: int = MAX_CONTEXT_TURNS,
) -> list[Message]:
    """Return the last `limit` complete turns of user/assistant history.

    A turn starts at a ``user`` message and includes every following message
    until the next ``user``. This keeps consecutive ``user`` fragments together
    with the assistant reply that answered them, instead of cutting mid-turn.
    Non-conversational roles (system/tool) are dropped.
    """
    if not messages or limit <= 0:
        return []
    conversational = [
        message
        for message in messages
        if message.get("role") in {"user", "assistant"} and message.get("content")
    ]
    if not conversational:
        return []
    boundaries = [index for index, message in enumerate(conversational) if message["role"] == "user"]
    if len(boundaries) <= limit:
        return conversational
    return conversational[boundaries[-limit] :]


async def stream_agent(
    prompt: str,
    *,
    messages: Sequence[Message] | None = None,
    llm: LLMProvider | None = None,
    tool_context: ToolContext | None = None,
) -> AsyncIterator[tuple[str, dict[str, Any]]]:
    if tool_context and tool_context.conversation_id and tool_context.organization_id:
        from ...agent.runtime import stateful_stream
        async for event in stateful_stream(prompt, messages=messages, llm=llm,
                                          tool_context=tool_context, generate=_generate):
            yield event
        return
    # Compatibility path is read-only: ToolRegistry rejects every write.
    async for event in _generate(prompt, messages=messages, llm=llm, tool_context=tool_context):
        yield event


async def _generate(
    prompt: str, *, messages=None, llm=None, tool_context=None, system_context: str = '', tools_enabled: bool = True,
    knowledge_sink: list[str] | None = None,
) -> AsyncIterator[tuple[str, dict[str, Any]]]:
    """Retry/fallback over the model chain using an explicit LLM contract."""
    provider = llm or default_llm
    chain = get_model_chain()
    attempts_per_model = 3
    total_attempts = len(chain) * attempts_per_model
    environment = get_app_env()
    configs = (
        [chain[index % len(chain)] for index in range(total_attempts)]
        if environment is AppEnv.TEST
        else [config for config in chain for _ in range(attempts_per_model)]
    )

    history = context_window(messages)
    knowledge, used_rag, retrieval_topic, retrieval_hits = await _retrieve_knowledge(prompt, history)
    if knowledge_sink is not None:
        knowledge_sink[:] = [str(item.get('content') or '') for item in knowledge if item.get('content')]
    if system_context:
        knowledge = [{'role': 'system', 'content': system_context}, *knowledge]

    attempt = 0
    permanent_failures: set[Provider] = set()
    use_tools = provider.capabilities.supports_tools and tools_enabled
    while attempt < len(configs):
        config = configs[attempt]
        attempt += 1
        if config.provider in permanent_failures:
            continue
        emitted_tokens = False
        answer_parts: list[str] = []
        try:
            if use_tools:
                conversation: list[Message] = [*knowledge, *history, {"role": "user", "content": prompt}]
                used_tools = False
                for _ in range(MAX_TOOL_ROUNDS):
                    tool_calls: list[dict[str, str]] = []
                    round_parts: list[str] = []
                    async for kind, payload in provider.stream(
                        config,
                        prompt,
                        messages=conversation,
                        tools=CANONICAL_TOOLS,
                    ):
                        if kind == "token":
                            text = str(payload["text"])
                            round_parts.append(text)
                        elif kind == "tool_calls":
                            tool_calls = list(payload.get("calls") or [])
                    if not tool_calls:
                        # Tool-planning speech is not the final answer. Never join
                        # intermediate "one moment" prose to the post-tool response.
                        answer_parts = round_parts
                        for text in round_parts:
                            emitted_tokens = True
                            yield 'token', {'text': text}
                        break
                    used_tools = True
                    assistant_calls = []
                    tool_messages: list[Message] = []
                    for index, call in enumerate(tool_calls):
                        call_id = call.get("id") or f"call_{index + 1}"
                        name = call.get("name") or "tool"
                        raw_arguments = call.get("arguments") or "{}"
                        arguments = normalize_relative_booking_date(
                            name, parse_arguments(raw_arguments), prompt, history
                        )
                        title, status = describe_tool_start(name, arguments)
                        inputs = present_tool_inputs(name, arguments)
                        yield "tool.started", {"tool": name, "tool_call_id": call_id, "title": title, "status": status, "inputs": inputs}
                        tool_result = await TOOL_REGISTRY.execute(
                            name,
                            arguments,
                            tool_context or ToolContext(request_id=f"agent-{config.provider.value}-{attempt}"),
                        )
                        if tool_result.ok:
                            result = tool_result.data if isinstance(tool_result.data, str) else __import__("json").dumps(tool_result.data, ensure_ascii=False)
                        else:
                            result = __import__("json").dumps({"error_code": tool_result.error_code, "message": tool_result.message}, ensure_ascii=False)
                        done_title, done_status = describe_tool_done(name, arguments, result)
                        yield "tool.completed", {
                            "tool": name,
                            "tool_call_id": call_id,
                            "arguments": arguments,
                            "ok": tool_result.ok,
                            "error_code": tool_result.error_code,
                            "title": done_title,
                            "status": done_status if tool_result.ok else "Pendiente de revisión",
                            "result": result,
                            "inputs": inputs,
                            "outputs": present_tool_outputs(name, result),
                        }
                        assistant_calls.append(
                            {
                                "id": call_id,
                                "type": "function",
                                "function": {"name": name, "arguments": raw_arguments},
                            }
                        )
                        tool_messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": call_id,
                                "name": name,
                                "content": result,
                            }
                        )
                    conversation.append(
                        {"role": "assistant", "content": None, "tool_calls": assistant_calls}
                    )
                    conversation.extend(tool_messages)
                if not emitted_tokens:
                    raise ProviderError("Provider returned an empty stream")
                rag_event = _build_rag_event(
                    "".join(answer_parts),
                    used_rag,
                    retrieval_topic,
                    retrieval_hits,
                    used_tools=used_tools,
                )
                if rag_event:
                    yield "rag.started", rag_event
                    yield "rag.completed", rag_event
                yield "done", {"provider": config.provider.value, "model": config.model}
                return

            conversation: list[Message] | None = [*knowledge, *history, {"role": "user", "content": prompt}]
            async for kind, payload in provider.stream(
                config,
                prompt,
                messages=conversation,
                tools=None,
            ):
                if kind != "token":
                    continue
                emitted_tokens = True
                text = str(payload["text"])
                answer_parts.append(text)
                yield "token", {"text": text}
            if not emitted_tokens:
                raise ProviderError("Provider returned an empty stream")
            rag_event = _build_rag_event("".join(answer_parts), used_rag, retrieval_topic, retrieval_hits)
            if rag_event:
                yield "rag.started", rag_event
                yield "rag.completed", rag_event
            yield "done", {"provider": config.provider.value, "model": config.model}
            return
        except ProviderError as exc:
            if emitted_tokens:
                yield "error", {"message": "La respuesta del proveedor se interrumpió"}
                return
            if not exc.retryable:
                permanent_failures.add(config.provider)
            if attempt >= total_attempts:
                yield "error", {"message": "No hay proveedores disponibles"}
                return

    yield "error", {"message": "No hay proveedores disponibles"}


async def _knowledge_message(prompt: str, messages: Sequence[Message] | None) -> list[Message]:
    knowledge, _used_rag, _retrieval_topic, _hits = await _retrieve_knowledge(prompt, messages)
    return knowledge


def _contextual_query(prompt: str, recent: Sequence[Message]) -> str:
    """Merge recent turns into a standalone query so short follow-ups retrieve.

    E.g. a follow-up "reservas y sumas" after "¿tienes acceso a ...?" must search
    for both ideas, not the fragment in isolation.
    """
    previous = " ".join(
        str(message.get("content") or "").strip()
        for message in recent
        if message.get("role") == "user"
    ).strip()
    if not previous:
        return prompt
    return f"{previous[-400:]} {prompt}".strip()


async def _retrieve_knowledge(
    prompt: str,
    messages: Sequence[Message] | None,
) -> tuple[list[Message], bool, str | None, list[RetrievalHit]]:
    recent = list(messages or [])[-4:]
    query = _contextual_query(prompt, recent)
    try:
        result = await rag_retriever.search(query, conversation=recent)
    except Exception:
        # Preserve the provider-facing history when knowledge infrastructure is
        # down; the runtime remains usable and must not fabricate context.
        return [], False, None, []

    if result.evidence_state == "INSUFFICIENT" or not result.hits:
        return [{"role": "system", "content": "knowledge_status=insufficient. Do not claim the document supports an answer."}], False, None, []
    context, source_map = build_knowledge_context(result)
    used_rag = result.evidence_state in {"SUFFICIENT", "AMBIGUOUS"} and bool(context)
    if not used_rag:
        return [{"role": "system", "content": "knowledge_status=insufficient. Do not claim the document supports an answer."}], False, None, []
    included = {citation.chunk_id for citation in source_map.values()}
    hits = [hit for hit in result.hits if hit.chunk_id in included]
    return [{"role": "system", "content": "knowledge_status=available\nIf supplied knowledge does not support the answer, do not claim that the document says it.\n" + context}], True, _topic_from_hits(hits), hits


def _meaningful_terms(text: str) -> set[str]:
    return {
        term
        for term in re.findall(r"[a-záéíóúüñ]{4,}", text.casefold())
        if term not in _RAG_STOP_WORDS
    }


def _terms_related(left: str, right: str) -> bool:
    if left == right:
        return True
    return len(left) >= 6 and len(right) >= 6 and left[:6] == right[:6]


def _overlap_count(answer: str, text: str) -> int:
    answer_terms = _meaningful_terms(answer)
    content_terms = _meaningful_terms(text)
    return sum(1 for term in answer_terms if any(_terms_related(term, other) for other in content_terms))


def _answer_uses_knowledge(answer: str, hits: Sequence[RetrievalHit]) -> bool:
    return _overlap_count(answer, "\n".join(hit.content for hit in hits)) >= _MIN_KNOWLEDGE_TERMS


def _is_clarification_answer(answer: str) -> bool:
    normalized = " ".join(answer.casefold().split())
    return any(marker in normalized for marker in _CLARIFICATION_MARKERS)


def _is_capability_answer(answer: str) -> bool:
    normalized = " ".join(answer.casefold().split())
    return any(marker in normalized for marker in _CAPABILITY_MARKERS)


def _clean_topic(value: object, *, strip_extension: bool = False) -> str | None:
    if not isinstance(value, str):
        return None
    topic = " ".join(value.replace("_", " ").split()).strip(" -—")
    if strip_extension:
        topic = re.sub(r"\.[a-z0-9]+$", "", topic, flags=re.IGNORECASE).strip()
    if not topic or topic.casefold() in {"general", "unknown", "none"}:
        return None
    return topic[:1].upper() + topic[1:]


def _hit_topic(hit: RetrievalHit) -> str | None:
    heading = chunk_heading(hit.content)
    if heading:
        return _clean_topic(heading)
    metadata = hit.metadata if isinstance(hit.metadata, dict) else {}
    for key in ("section", "heading_path", "document_title"):
        topic = _clean_topic(metadata.get(key))
        if topic:
            return topic
    return _clean_topic(metadata.get("source_filename"), strip_extension=True)


def _topic_from_hits(hits: Sequence[RetrievalHit]) -> str | None:
    for hit in hits:
        topic = _hit_topic(hit)
        if topic:
            return topic
    return None


def _supporting_hits(answer: str, hits: Sequence[RetrievalHit]) -> list[RetrievalHit]:
    ranked: list[tuple[int, RetrievalHit]] = []
    for hit in hits:
        overlap = _overlap_count(answer, hit.content)
        if overlap >= _MIN_KNOWLEDGE_TERMS:
            ranked.append((overlap, hit))
    ranked.sort(key=lambda item: -item[0])
    return [hit for _overlap, hit in ranked]


def _document_title(hits: Sequence[RetrievalHit], fallback: str) -> str:
    for hit in hits:
        metadata = hit.metadata if isinstance(hit.metadata, dict) else {}
        title = _clean_topic(metadata.get("document_title"))
        if title:
            return title
        title = _clean_topic(metadata.get("source_filename"), strip_extension=True)
        if title:
            return title
    return fallback


def _build_rag_event(
    answer: str,
    used_rag: bool,
    retrieval_topic: str | None,
    hits: Sequence[RetrievalHit],
    *,
    used_tools: bool = False,
) -> dict[str, object] | None:
    """Expose the document topic only when the answer actually used retrieved text."""
    if used_tools or not used_rag or _is_clarification_answer(answer) or _is_capability_answer(answer):
        return None
    supporting = _supporting_hits(answer, hits)
    if not supporting or not _answer_uses_knowledge(answer, supporting):
        return None
    message = _topic_from_hits(supporting) or retrieval_topic or "Contexto relevante"
    return {
        "used_rag": True,
        "message": message,
        "title": _document_title(supporting, message),
        "content": "\n\n".join(hit.content.strip() for hit in supporting if hit.content.strip()),
    }
