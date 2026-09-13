# Agente

**Estado:** verificado contra `backend/app/features/agent/service.py` y `tools.py`. Tests: `backend/tests/test_tools.py`, `test_llm.py`.

`stream_agent(prompt, messages=, llm=)` recorre la cadena de `get_model_chain()` y emite:

- `token` `{ text }`
- `tool.started` / `tool.completed`
- `done` `{ provider, model }`
- `error` si el stream se corta **después** de haber emitido tokens

Máximo **4** rondas de tools (`MAX_TOOL_ROUNDS`). System prompt: agente de voz breve en español; debe ejecutar tools en vez de inventar resultados.

## Tools de demo

| Nombre | Args | Efecto |
| --- | --- | --- |
| `generate_lorem_ipsum` | `characters` 1–5000 | Recorta Lorem |
| `sum_numbers` | `numbers[]` | Suma; error si lista vacía |

Definición canónica en `CANONICAL_TOOLS`; `to_openai_tools` / `to_gemini_tools` para cada vendor.

**Producto:** `agent runtime ≠ business tools`. Las tools actuales son demo (`lorem`, `sum`). En el reto se sustituyen por plugins de dominio (p. ej. pedidos de pizzería) sin reescribir el runtime. RAG cubre políticas cuando no hay tool ([intelligence](../product/intelligence.md)).

## Chunking semántico (servidor)

`_take_semantic_chunk` en `main.py`: corta en `.!?` cuando hay al menos 2 palabras. Cada chunk dispara Piper en la llamada y en `/voice`. El frontend tiene un `SemanticChunker` equivalente para caminos HTTP.
