# Telefonía, monitor y audio

**Estado:** candidato (equipo). Código actual: micrófono de browser → `/ws/call`, no PSTN.

## Telnyx

Proveedor inicial previsto. Cuenta aún por preparar. Objetivo de demo: **telefonía real**.

```text
PSTN → Telnyx → Backend → Agent → Telnyx → PSTN
```

Observación:

```text
Telnyx (webhooks + media WS + recording)
  → TelnyxAdapter
  → Internal events (CallEvent)
  → RealtimeHub
  → Monitor UI
```

Telnyx separa `inbound_track` / `outbound_track` y expone legs/sesión en webhooks. El frontend nunca pinta esos payloads crudos (AP-05).

## Monitor

Feature diferenciadora: un ADMIN observa **una** llamada de **un** agente de su org. No optimizar para miles de espectadores.

Campos: customer, phone, agent, status, started_at, answered_at, duration, live edge, audio, waveform, transcripts, events.

Estados: `STARTING` | `RINGING` | `ACTIVE` | `ENDED` | `ERROR`.

En UI: `liveEdge` = presente de la llamada; `current` = posición del espectador (puede ir detrás mientras la llamada sigue).

Hoy el monitor es timeline + hub JSON; liveEdge/current y replay real **no** están.

## Grabaciones

Permanentes para el proyecto. Hackatón: disco local. Después object storage sin cambiar dominio.

```text
RecordingStorage → LocalRecordingStorage
```

No acoplar `calls` a un path de filesystem en lógica de negocio.

Capacidades: audio live, buffer, recording finalized, historical playback.
