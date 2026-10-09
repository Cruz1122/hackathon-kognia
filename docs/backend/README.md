# Backend

Índice del proceso FastAPI.

| Documento | Contenido |
| --- | --- |
| [providers.md](providers.md) | Contratos LLM/STT/TTS y cadena de modelos |
| [agent.md](agent.md) | `stream_agent`, tools, retries |
| [ips-soda3.md](ips-soda3.md) | Adapter del catálogo IPS de datos.gov.co con caché Redis |

**Lifespan** (`main.py`): check DB → preload Sherpa → preload Piper. Cualquier fallo se registra y el proceso sigue vivo para que `/health/live` distinga proceso vs. ready.

**Sustitución de providers:** `main.py` asigna `llm_provider`, `stt_provider`, `tts_provider` desde `backend/app/providers/`. Tests inyectan fakes (`providers/fakes.py`).

**Locks:** `transcription_lock` serializa `/transcribe`. La llamada WS usa streams Sherpa por conexión.

**Config:** `backend/app/config.py`. Env se carga de raíz `.env` y `backend/.env` sin override de variables de shell (`load_repository_environment`).
