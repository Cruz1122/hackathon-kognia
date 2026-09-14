import re
from collections.abc import AsyncIterator, Sequence
from typing import Any

from ...config import AppEnv, Provider, get_app_env, get_model_chain
from ...providers import ProviderError, llm_provider as default_llm
from ...providers.contracts import LLMProvider
from ...agent.tools.contracts import ToolContext
from ...agent.tools.loader import load_tool_registry
from ...platform.rag.runtime import retriever as rag_retriever
from ...platform.rag.citations import build_knowledge_context
from .tools import CANONICAL_TOOLS, describe_tool_done, describe_tool_start, execute_tool, parse_arguments

Message = dict[str, Any]
MAX_TOOL_ROUNDS = 4
TOOL_REGISTRY = load_tool_registry()
_RAG_STOP_WORDS = frozenset(
    "a al algo con como cuando de del donde el en es esta este hay la las lo los más me "
    "para por que qué se su sus te tu tus un una y".split()
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


async def stream_agent(
    prompt: str,
    *,
    messages: Sequence[Message] | None = None,
    llm: LLMProvider | None = None,
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

    knowledge, used_rag, retrieval_topic = await _retrieve_knowledge(prompt, messages)

    attempt = 0
    permanent_failures: set[Provider] = set()
    use_tools = provider.capabilities.supports_tools
    while attempt < len(configs):
        config = configs[attempt]
        attempt += 1
        if config.provider in permanent_failures:
            continue
        emitted_tokens = False
        answer_parts: list[str] = []
        try:
            if use_tools:
                conversation: list[Message] = [*(messages or []), *knowledge, {"role": "user", "content": prompt}]
                for _ in range(MAX_TOOL_ROUNDS):
                    tool_calls: list[dict[str, str]] = []
                    async for kind, payload in provider.stream(
                        config,
                        prompt,
                        messages=conversation,
                        tools=CANONICAL_TOOLS,
                    ):
                        if kind == "token":
                            emitted_tokens = True
                            text = str(payload["text"])
                            answer_parts.append(text)
                            yield "token", {"text": text}
                        elif kind == "tool_calls":
                            tool_calls = list(payload.get("calls") or [])
                    if not tool_calls:
                        break
                    assistant_calls = []
                    tool_messages: list[Message] = []
                    for index, call in enumerate(tool_calls):
                        call_id = call.get("id") or f"call_{index + 1}"
                        name = call.get("name") or "tool"
                        raw_arguments = call.get("arguments") or "{}"
                        arguments = parse_arguments(raw_arguments)
                        title, status = describe_tool_start(name, arguments)
                        yield "tool.started", {"tool": name, "title": title, "status": status}
                        tool_result = await TOOL_REGISTRY.execute(
                            name,
                            arguments,
                            ToolContext(request_id=f"agent-{config.provider.value}-{attempt}"),
                        )
                        if tool_result.ok:
                            result = tool_result.data if isinstance(tool_result.data, str) else __import__("json").dumps(tool_result.data, ensure_ascii=False)
                        else:
                            result = __import__("json").dumps({"error_code": tool_result.error_code, "message": tool_result.message}, ensure_ascii=False)
                        done_title, done_status = describe_tool_done(name, arguments, result)
                        yield "tool.completed", {
                            "tool": name,
                            "title": done_title,
                            "status": done_status,
                            "result": result,
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
                rag_event = _build_rag_event("".join(answer_parts), knowledge, used_rag, retrieval_topic)
                if rag_event:
                    yield "rag.started", rag_event
                    yield "rag.completed", rag_event
                yield "done", {"provider": config.provider.value, "model": config.model}
                return

            conversation: list[Message] | None = [*(messages or []), *knowledge, {"role": "user", "content": prompt}]
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
            rag_event = _build_rag_event("".join(answer_parts), knowledge, used_rag, retrieval_topic)
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
    knowledge, _used_rag, _retrieval_topic = await _retrieve_knowledge(prompt, messages)
    return knowledge


async def _retrieve_knowledge(
    prompt: str,
    messages: Sequence[Message] | None,
) -> tuple[list[Message], bool, str | None]:
    try:
        result = await rag_retriever.search(prompt, conversation=list(messages or [])[-2:])
    except Exception:
        # Preserve the provider-facing history when knowledge infrastructure is
        # down; the runtime remains usable and must not fabricate context.
        return [], False, None

    if result.evidence_state == "INSUFFICIENT" or not result.hits:
        return [{"role": "system", "content": "knowledge_status=insufficient. Do not claim the document supports an answer."}], False, None
    context, source_map = build_knowledge_context(result)
    used_rag = result.evidence_state in {"SUFFICIENT", "AMBIGUOUS"} and bool(context)
    if not used_rag:
        return [{"role": "system", "content": "knowledge_status=insufficient. Do not claim the document supports an answer."}], False, None
    return [{"role": "system", "content": "knowledge_status=available\nIf supplied knowledge does not support the answer, do not claim that the document says it.\n" + context}], True, _retrieval_topic(source_map)


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


def _answer_uses_knowledge(answer: str, knowledge: Sequence[Message]) -> bool:
    answer_terms = _meaningful_terms(answer)
    context_text = "\n".join(str(message.get("content", "")) for message in knowledge)
    context_terms = _meaningful_terms(context_text)
    return any(_terms_related(answer_term, context_term) for answer_term in answer_terms for context_term in context_terms)


def _is_clarification_answer(answer: str) -> bool:
    normalized = " ".join(answer.casefold().split())
    return any(marker in normalized for marker in _CLARIFICATION_MARKERS)


def _clean_topic(value: object, *, strip_extension: bool = False) -> str | None:
    if not isinstance(value, str):
        return None
    topic = " ".join(value.replace("_", " ").split()).strip(" -—")
    if strip_extension:
        topic = re.sub(r"\.[a-z0-9]+$", "", topic, flags=re.IGNORECASE).strip()
    if not topic or topic.casefold() in {"general", "unknown", "none"}:
        return None
    return topic[:1].upper() + topic[1:]


def _retrieval_topic(source_map: dict[str, Any]) -> str | None:
    for citation in source_map.values():
        metadata = getattr(citation, "metadata", {})
        if not isinstance(metadata, dict):
            continue
        for key in ("section", "heading_path", "document_title"):
            topic = _clean_topic(metadata.get(key))
            if topic:
                return topic
        topic = _clean_topic(metadata.get("source_filename"), strip_extension=True)
        if topic:
            return topic
    return None


def _build_rag_event(
    answer: str,
    knowledge: Sequence[Message],
    used_rag: bool,
    retrieval_topic: str | None,
) -> dict[str, object] | None:
    """Expose only a useful, context-supported document topic to the UI."""
    if not used_rag or _is_clarification_answer(answer) or not _answer_uses_knowledge(answer, knowledge):
        return None
    return {"used_rag": True, "message": retrieval_topic or "Contexto relevante"}
