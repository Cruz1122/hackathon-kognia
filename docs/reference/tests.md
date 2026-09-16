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
| `sse-parser.test.ts` | parseo SSE |
| `semantic-chunker.test.ts` | cortes de frase |
| `cancellation-controller.test.ts` | abort de turno |

No hay e2e de WebSocket en el árbol actual.
