# Arquitectura objetivo

**Estado:** candidato. El runtime verificado está en [overview.md](overview.md).

## Capas

```text
                 External providers
                        │
                 Provider adapters
                        │
                 Internal contracts
                        │
             ┌──────────┴──────────┐
             │                     │
        Platform Core         Domain Modules
             │                     │
   Data · Realtime · Jobs     Telephony · Sales · Agent · WhatsApp
             │
         Frontend (contratos internos)
```

## Monolito modular + procesos especializados

No microservicios por moda. Separar proceso solo por CPU/GPU/memoria, job largo, failure domain, red, runtime o scaling distintos.

Compose **objetivo**:

```text
frontend → backend → postgres
                  → chroma
worker  → postgres + chroma

Externos: Telnyx, Meta WhatsApp, LLM
```

Hoy Compose tiene `frontend`, `backend`, `postgres`, `migrate`. Faltan `chroma` y `worker`.

Worker = **misma imagen** que el backend, otro comando (`python -m app.worker`): recordings, summaries, embeddings, RAG ingest, features, autoencoder, rebuild Sales Space, dataset sintético.

Scheduler: al principio **dentro del worker** (event / scheduled / manual). Cola distribuida solo si hay razón clara.

## Realtime reutilizable

Hoy `/ws/call` está acoplado al agente de voz. Dirección: `RealtimeHub` para call events, transcripts, agent events, progreso de analytics, notificaciones. Sigue in-process; no un bus distribuido.

## Conversación vs canal

```text
Conversation
 ├── VoiceSession
 ├── WhatsAppSession
 └── future channels
```

No modelar `call == conversation`.

## PostgreSQL previsto (no implementar CRM completo)

Entidades **posibles** al adaptar el reto: organizations, users, agents, customers, calls, call_participants, call_events, transcript_segments, recordings, products, offers, orders, sales, conversations, messages, analytics, jobs.

El modelo concreto se decide **con el reto**. Evitar CRM anticipado.

## STT / TTS

Mantener interfaces actuales:

```text
SpeechToTextProvider → Sherpa
TextToSpeechProvider → Piper
```

Whisper ya se descartó para este realtime. Alternativas TTS solo si mejoran materialmente.

## Despliegue

Azure como entorno. Preferir OSS/self-hosted/low-cost. No introducir PaaS Azure porque “está ahí”.
