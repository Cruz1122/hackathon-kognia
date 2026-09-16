# Principios arquitectónicos

**Estado:** aceptado (equipo). Aplicarlos al decidir; no reescribir historia del código.

| ID | Nombre | Implicación |
| --- | --- | --- |
| AP-01 | Pivotability first | Ningún dominio provisional controla toda la arquitectura. |
| AP-02 | Provider isolation | Externos vía adapter: Telnyx, Sherpa, Piper, LLM, Meta, Chroma. |
| AP-03 | Modular monolith by default | No microservicios sin razón operativa. |
| AP-04 | Specialized processes | Worker, Chroma, jobs pesados pueden ser procesos aparte. |
| AP-05 | Internal contracts | El frontend no consume payloads de vendor (p. ej. Telnyx). |
| AP-06 | Conversation above channel | `Conversation` ⊃ call, WhatsApp, futuros canales. |
| AP-07 | Tools execute, RAG explains | Tools = acciones/estado; RAG = políticas/conocimiento. |
| AP-08 | PostgreSQL owns transactional truth | Chroma no sustituye el relacional. |
| AP-09 | Chroma owns vector retrieval | Embeddings de recuperación viven en Chroma. |
| AP-10 | Async work leaves the API path | Entrenar/embeber no bloquea FastAPI. |
| AP-11 | Analytics must answer decisions | Gráfica solo si cambia lo que haría un admin mañana. |
| AP-12 | Avoid outcome leakage | `won` es label/metadata, no feature del autoencoder. |
| AP-13 | Synthetic structure | Datos sintéticos con relaciones deliberadas, no ruido. |
| AP-14 | Reproducibility over MLOps | seed + config + hash; no MLflow/Kubeflow. |
| AP-15 | Local-first | OSS, self-hosted, barato; Azure es deploy, no menú de PaaS. |
| AP-16 | Demo reality where it matters | Telefonía real; histórico puede ser sintético (distinción explícita). |
| AP-17 | Six-hour rule | Sacrificar arquitectura si bloquea la entrega. |

## Contratos internos (ejemplo telefonía)

```text
Telnyx webhook → TelnyxAdapter → CallEvent → application → WebSocket → Frontend
```

Mañana el adapter cambia; `CallEvent` y la UI no.

## Agente vs tools

```text
agent runtime ≠ business tools
```

El runtime permanece. Las tools de dominio (pizza, healthcare, …) se sustituyen como plugins.
