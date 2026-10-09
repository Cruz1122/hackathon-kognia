# Llamada y monitoreo

**Estado:** verificado contra `frontend/src/features/voice-call/` y las páginas Astro.

## Módulos

```text
features/voice-call/
  application/live-monitor.ts     WS /ws/call (demo principal)
  application/live-call.ts        audio de una llamada guardada
  application/events-monitor.ts   WS /ws/events
  infrastructure/
    audio-capture-adapter.ts      mic PCM 16 kHz
    pcm-audio-queue.ts            playback PCM
    toast.ts
```

## Demo principal (`/calls/demo`)

1. Login `POST /auth/login`.
2. `POST /conversations` con Bearer.
3. WebSocket `/ws/call`: `auth` → `conversation.attach`.
4. Captura PCM 16 kHz (ver `audio-capture-adapter.ts` / `live-monitor.ts`).
5. Toasts para conexión, tools, TTS y errores.

La isla de `calls/demo.astro` gobierna la sesión autenticada y el live monitor.

## Monitoreo (`/monitoring`)

`bootEventsMonitor(apiUrl)`:

- Filtra envelopes por `kognia.auth.conversation-id` si está en `sessionStorage`.
- Abre WS `/ws/events` y manda `{ type: "auth", token }` como primer mensaje.
- Pinta parciales del cliente, tokens del agente, tools y una fila mínima de contexto con el tópico del documento o sección recuperada, solo cuando esa respuesta usa el contexto recuperado. No reproduce audio.
- Cierre `4401` → toast de sesión inválida. Sin auth válida el chip permanece en demo local.

**Producto:** el monitor de dominio (PSTN) usaría `STARTING|RINGING|ACTIVE|ENDED|ERROR` y `liveEdge` vs `current` para replay detrás del presente. Ver [Telefonía](../product/telephony.md).
