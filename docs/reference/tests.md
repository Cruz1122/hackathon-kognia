# Tests

## Backend (`backend/tests/`)

| Archivo | Cubre |
| --- | --- |
| `test_health.py` | live/ready |
| `test_auth.py` | login, JWT, roles, bootstrap paths |
| `test_conversations.py` | tenant isolation, persistencia |
| `test_models.py` | constraints ORM |
| `test_llm.py` | cadena, fallback, stream |
| `test_providers.py` | contratos / fakes |
| `test_tools.py` | lorem + sum |
| `test_synthesis.py` | Piper paths (según entorno) |
| `test_pcm_wave.py` | features de PCM |
| `test_realtime.py` | hub JSON, fan-out, dead sockets |

## Frontend

| Archivo | Cubre |
| --- | --- |
| `backend-error.test.ts` | mensajes de error de la API |
| `detail-panel.test.ts` | panel de detalle de la llamada |
| `retrieval-card.test.ts` | tarjeta de retrieval |
| `session-guard.test.ts` | sesión y redirección |
| `phone-display.test.ts` | formato de teléfono |
| `app-shell.test.ts` | shell de la app |
| `model.test.ts` | señales del agente |

No hay e2e de WebSocket en el árbol actual.
