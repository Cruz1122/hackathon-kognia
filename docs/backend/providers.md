# Providers

**Estado:** verificado contra `backend/app/providers/` y `backend/app/config.py`. Tests: `test_providers.py`, `test_llm.py`, `test_synthesis.py`, `test_pcm_wave.py`.

## Contratos

`contracts.py`:

- `LLMProvider.stream` → eventos `token` | `tool_calls`
- `SpeechToTextProvider` → preload, stream PCM, transcribe blob
- `TextToSpeechProvider` → preload, `sample_rate`, `stream_audio`, `synthesize_wav`
- `CanonicalTool` — JSON Schema; adapters traducen a OpenAI/Gemini

Singletons de producción (`providers/__init__.py`):

- `RoutedLLM()`
- `SherpaSpeechToText()`
- `PiperTextToSpeech()`

## Cadena LLM

`APP_ENV=test` (default): 4 modelos × 3 intentos, orden circular.

| Orden | Provider | Modelo |
| --- | --- | --- |
| 1 | OpenAI | `gpt-4o-mini` |
| 2 | Gemini | `gemini-3.5-flash-lite` |
| 3 | OpenRouter | `minimax/minimax-m2.7` |
| 4 | Groq | `llama-3.3-70b-versatile` |

`APP_ENV=production`: 2 modelos × 3 intentos **secuenciales**.

| Orden | Provider | Modelo |
| --- | --- | --- |
| 1 | OpenAI | `gpt-5.6-luna` |
| 2 | Gemini | `gemini-3.5-flash-lite` |

Keys: `OPENAI_API_KEY`, `GEMINI_API_KEY`, `OPENROUTER_API_KEY`, `GROQ_API_KEY`. Placeholders vacíos o `tu_clave_de_*` se tratan como ausentes.

Si ningún modelo de la cadena tiene key, `/ask` responde 503.

## STT

Sherpa-ONNX Nemotron 3.5 streaming español (560 ms). Modelo en `backend/models/sherpa-nemotron-35-560/` (gitignored). `SHERPA_MODEL_DIR`, `SHERPA_THREADS` (Compose default 2). `language=es`; sin hot-frame.

## TTS

Piper voz `es_MX-claude-high` en `backend/models/piper-es/` (override `PIPER_MODEL_DIR`, `PIPER_TTS_VOICE`). Un worker residente; la API serializa generación para no mezclar utterances.
