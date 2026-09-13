# Llamada y monitoreo

**Estado:** verificado contra `frontend/src/features/voice-call/` y las páginas Astro.

## Módulos

```text
features/voice-call/
  domain/types.ts                 CallState, VoiceSnapshot
  application/voice-call-controller.ts
  application/live-monitor.ts     WS /ws/call (demo principal)
  application/events-monitor.ts   WS /ws/events
  infrastructure/
    agent-stream-adapter.ts       fetch /ask y /voice
    audio-capture-adapter.ts      mic + /transcribe fallback
    pcm-audio-queue.ts            playback PCM
    sse-parser.ts
    speech-queue.ts / browser-speech-synthesizer.ts
    cancellation-controller.ts
    toast.ts
  services/semantic-chunker.ts
```

## Demo principal (`/`)

1. Login `POST /auth/login`.
2. `POST /conversations` con Bearer.
3. WebSocket `/ws/call`: `auth` → `conversation.attach`.
4. Captura PCM 16 kHz (ver `audio-capture-adapter.ts` / `live-monitor.ts`).
5. Toasts para conexión, tools, TTS y errores.

`VoiceCallController` orquesta el camino HTTP (`/ask`, `/voice`, `/transcribe`, `/synthesize`) usado como respaldo o métricas; la isla de `index.astro` gobierna la sesión autenticada y el live monitor.

## Monitoreo (`/monitoring`)

`bootEventsMonitor(apiUrl)`:

- Filtra envelopes por `kognia.auth.conversation-id` si está en `sessionStorage`.
- Abre WS `/ws/events` y manda `{ type: "auth", token }` como primer mensaje.
- Pinta parciales del cliente, tokens del agente y tools. No reproduce audio.
- Cierre `4401` → toast de sesión inválida. Sin auth válida el chip permanece en demo local.

## Estados de llamada (`CallState`)

`idle` → `connecting` → `listening` → `processing` → `speaking` → `interrupted` / `ended` / `error`.

**Producto:** el monitor de dominio (PSTN) usaría `STARTING|RINGING|ACTIVE|ENDED|ERROR` y `liveEdge` vs `current` para replay detrás del presente. Ver [Telefonía](../product/telephony.md).
