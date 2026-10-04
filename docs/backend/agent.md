# Agente

**Estado:** verificado contra `backend/app/features/agent/service.py` y `tools.py`. Tests: `backend/tests/test_tools.py`, `test_llm.py`.

`stream_agent(prompt, messages=, llm=)` recorre la cadena de `get_model_chain()` y emite:

- `token` `{ text }`
- `tool.started` / `tool.completed`
- `rag.started` / `rag.completed` con `{ used_rag: true, message }` solo cuando la respuesta útil coincide con el contexto RAG; `message` es el tópico normalizado del documento o de la sección recuperada
- `done` `{ provider, model }`
- `error` si el stream se corta **después** de haber emitido tokens

Los adaptadores de proveedor emiten además `usage` `{ prompt_tokens, completion_tokens, total_tokens }` (OpenAI con `stream_options.include_usage`, Gemini con `usageMetadata`). El `TraceRecorder` los acumula y `to_dict()` expone `usage`; el modo dev (`/dev/calls`, `/dev/conversations/{id}/traces`) agrega el total por llamada.

`app/platform/pricing.py` resuelve modelo → tarifa USD por millón: primero el catálogo público de OpenRouter (`GET /api/v1/models`, sin key), cacheado en Redis (`pricing:openrouter:models`, TTL `PRICING_CACHE_TTL_SECONDS`, por defecto 6 h) y con espejo en memoria; si OpenRouter/Redis fallan, usa tarifas de lista fijas. El modo dev calcula `cost_usd` por turno (sumando spans `llm.request` con el modelo de cada uno) y por llamada, e informa `pricing_source` (`openrouter` o `fallback`). Es una estimación: no modela caché ni el tier de contexto largo, y modelos sin tarifa quedan en `null`. `PRICING_SOURCE_URL` permite apuntar a otro catálogo.

Máximo **4** rondas de tools (`MAX_TOOL_ROUNDS`). System prompt: agente de voz breve en español, siempre en texto plano (sin Markdown ni listas); debe ejecutar tools en vez de inventar resultados.

## Tools de demo

| Nombre | Args | Efecto |
| --- | --- | --- |
| `generate_lorem_ipsum` | `characters` 1–5000 | Recorta Lorem |
| `sum_numbers` | `numbers[]` | Suma; error si lista vacía |

Definición canónica en `CANONICAL_TOOLS`; `to_openai_tools` / `to_gemini_tools` para cada vendor.

**Producto:** `agent runtime ≠ business tools`. Las tools actuales son demo (`lorem`, `sum`). En el reto se sustituyen por plugins de dominio (p. ej. pedidos de pizzería) sin reescribir el runtime. RAG cubre políticas cuando no hay tool ([intelligence](../product/intelligence.md)).

## Chunking semántico (servidor)

`_take_semantic_chunk` en `main.py`: corta en `.!?` cuando hay al menos 2 palabras. Cada chunk dispara Piper en la llamada y en `/voice`. El frontend tiene un `SemanticChunker` equivalente para caminos HTTP.
