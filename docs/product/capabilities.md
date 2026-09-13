# Capacidades: capas y gap

**Estado:** capas = aceptadas. Gap = verificado contra el repo (2026-09-13).

## PLATFORM CORE

Debe sobrevivir aunque desaparezca telefonía.

```text
auth · organizations · users · persistence · realtime
jobs · AI providers · RAG · embeddings · analytics infra
configuration · audit/events · frontend shell
```

## DOMAIN MODULES (retirables)

Hoy previstos: telephony, voice agent, sales, calls, customers, WhatsApp continuity.

Mañana: otro dominio sin reconstruir el core.

## INFRASTRUCTURE

```text
PostgreSQL · Chroma · workers · scheduler · storage
Docker · Azure deployment
```

## EXPERIMENTAL

Sales Space, autoencoder, 3D, datasets sintéticos, analytics avanzado.

## Gap vs código

| Capacidad | Código hoy | Visión |
| --- | --- | --- |
| Voice agent + Sherpa/Piper + tools | Sí | Core de voz; tools como plugins de dominio |
| Monitor visual + waveform | Sí (`/`, `/monitoring`) | 1 llamada / 1 espectador; `liveEdge` vs `current` |
| RealtimeHub JSON in-memory | Sí | Reutilizar para call/analytics/notificaciones; no event bus distribuido |
| LLM providers intercambiables | Sí | Mantener contratos internos |
| PostgreSQL + Alembic | Sí | Dueño transaccional |
| Auth JWT + SUPERADMIN/ADMIN | Sí | No roles extra sin necesidad |
| Conversations + messages | Sí | Conversación **por encima** del canal (AP-06); call ≠ conversation |
| Docker Compose (web, api, postgres, migrate) | Sí | Añadir chroma + worker cuando haga falta |
| Telnyx / PSTN / recordings | No | Demo de telefonía **real** |
| RecordingStorage | No | Local primero; no paths en dominio |
| Chroma / RAG / embeddings | No | Horizontal; collections separadas |
| Worker / scheduler / jobs table | No | Misma imagen, otro entrypoint |
| Analytics comercial / `won` | No | Preguntas north-star, no KPI genéricos |
| Sales Space / autoencoder / 3D | No | Offline; UI consume coordenadas |
| WhatsApp / omnicanal | No | Continuidad de conversación |
| Azure deploy | No | Entorno de despliegue, no PaaS extra |

## Prioridad provisional (antes del reto)

No implica que todo esté listo. Las piezas caras de reutilizar primero:

1. Voice agent core
2. Real-time monitor
3. Telnyx
4. Persistence — **hecho**
5. Auth + organization — **hecho**
6. Docker — **parcial** (falta chroma/worker)
7. RAG pipeline
8. Chroma
9. Business analytics foundation
10. Sales Space
11. Synthetic data generator
12. Omnichannel continuity
