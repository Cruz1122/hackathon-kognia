# SPEC — Telnyx + Patter Call Platform

Estado: `READY FOR IMPLEMENTATION`  
Rama principal: `feat/telnyx-call-platform`  
Riesgo: `COMPLEX`  
Estrategia: implementar **una fase por vez** y no avanzar hasta validar su gate.

## 0. Protocolo obligatorio para el agente

Trabaja iterativamente.

Para cada fase:

1. Inspecciona primero los archivos afectados.
2. Resume qué vas a cambiar.
3. Implementa únicamente esa fase.
4. Ejecuta sus pruebas.
5. Ejecuta regresión relevante.
6. Completa el checklist.
7. Reporta evidencia.
8. DETENTE.

No avances automáticamente a la siguiente fase.

Formato obligatorio al terminar cada fase:

```text
PHASE N — RESULT

Status: PASS | PARTIAL | FAIL

Implemented:
- ...

Files changed:
- ...

Validation:
[x] ...
[x] ...
[ ] ...

Tests:
- command → PASS/FAIL
- command → PASS/FAIL

External validation:
- ...

Known issues:
- ...

Gate: PASS | FAIL

Next phase may start: YES | NO
```

Una fase solo puede declararse `PASS` si todos los requisitos del gate fueron comprobados.

Mocks no sustituyen verificaciones PSTN reales cuando el gate exige Telnyx real.

---

# 1. Objetivo final

Integrar:

```text
PSTN
 ↓
Telnyx
 ↓
Patter
 ↓
Sherpa
 ↓
stream_agent
 ├─ LLM
 ├─ Tools
 └─ RAG
 ↓
Piper
 ↓
Patter
 ↓
Telnyx
```

Y paralelamente:

```text
Telnyx / Patter / Agent
          ↓
   CallTimelineService
      ↙          ↘
RealtimeHub    PostgreSQL
```

El producto final debe soportar:

- llamadas PSTN entrantes;
- agente actual atendiendo;
- monitor administrativo live;
- transcript live;
- tools/RAG live;
- estado del agente;
- audio live de ambos lados;
- grabación dual;
- timeline persistente;
- replay con seek sincronizado.

Patter solo maneja infraestructura telefónica.

No reemplazar:

- `stream_agent`;
- tools;
- RAG;
- Sherpa;
- Piper;
- PostgreSQL;
- `Conversation`;
- `Message`;
- auth;
- analytics.

---

# 2. Contratos que no deben romperse

Debe seguir funcionando:

```text
/ws/call
/ws/events
/ask
/transcribe
/synthesize
stream_agent
tools
RAG
enrichment
analytics
```

Formato interno canónico de entrada:

```text
PCM signed 16-bit
little-endian
mono
16000 Hz
```

Todo payload externo Telnyx/Patter debe adaptarse en el boundary.

Nunca enviar payloads Telnyx crudos al frontend.

---

# 3. Decisiones previas obligatorias

Antes de Fase 1 resolver:

- [ ] `TELNYX_ORGANIZATION_ID`
- [ ] `TELNYX_SYSTEM_USER_ID`
- [ ] `TELNYX_PHONE_NUMBER`
- [ ] almacenamiento compartido backend/worker
- [ ] política de stream token: TTL reconnectable o one-shot
- [ ] una sola réplica backend para esta versión
- [ ] fork de Patter creado y fijado por commit

Para hackatón:

```text
1 número Telnyx
→ 1 organización
→ 1 system user
```

Multi-organización por número queda fuera de alcance.

---

# PHASE 1 — Telnyx transport

## Objetivo

Conseguir una llamada PSTN real que llegue al backend y produzca audio válido.

## Implementar

- [ ] Crear fork mínimo de Patter.
- [ ] Fijar commit exacto.
- [ ] Hacer Patter embebible en nuestro FastAPI.
- [ ] Añadir:

```text
POST /webhooks/telnyx/voice
WS   /ws/telnyx/stream/{call_id}
```

- [ ] Preservar `/ws/call`.
- [ ] Firma Ed25519 sobre body crudo.
- [ ] Anti-replay por timestamp.
- [ ] Deduplicación mediante `data.id`.
- [ ] Stream token asociado a llamada.
- [ ] Negociar inicialmente `L16 / 16000 Hz`.
- [ ] Leer `start.media_format`.
- [ ] No asumir byte order.
- [ ] Convertir wire format → PCM16LE 16 kHz.
- [ ] Backend limitado a una réplica.

## Validación

- [ ] Webhook válido devuelve éxito.
- [ ] Firma inválida es rechazada.
- [ ] Webhook duplicado no duplica efectos.
- [ ] Media WS sin token es rechazado.
- [ ] Media WS válido conecta.
- [ ] Llamada PSTN real es contestada.
- [ ] Captura real confirma codec.
- [ ] Captura real confirma sample rate.
- [ ] Captura real confirma byte order.
- [ ] Audio extraído es inteligible.
- [ ] `/ws/call` sigue funcionando.

## Gate

```text
Teléfono real
→ Telnyx
→ webhook
→ answer
→ Media WS
→ PCM16LE válido
```

No avanzar sin una llamada PSTN real.

---

# PHASE 2 — Voice pipeline

## Objetivo

Hacer que la llamada PSTN converse con el agente actual.

## Sherpa adapter

Implementar `SherpaSTTProvider`.

- [ ] recognizer compartido;
- [ ] stream separado por llamada;
- [ ] queue separada por llamada;
- [ ] `clone()` correctamente aislado;
- [ ] `feed_pcm` fuera del event loop;
- [ ] partial transcripts;
- [ ] final transcripts;
- [ ] cleanup.

## Piper adapter

Implementar `PiperTTSProvider`.

- [ ] envolver `stream_tts_audio`;
- [ ] declarar sample rate real;
- [ ] declarar formato;
- [ ] conservar lock actual inicialmente;
- [ ] streaming incremental;
- [ ] cleanup.

## Agent bridge

Implementar `PatterAgentBridge`.

- [ ] transcript final → `stream_agent`;
- [ ] conservar `tool_context`;
- [ ] respuesta como async generator;
- [ ] tools siguen funcionando;
- [ ] RAG sigue funcionando;
- [ ] propagar cancelación;
- [ ] integrar barge-in.

## Validación

Llamada real en español:

- [ ] usuario habla;
- [ ] aparecen parciales;
- [ ] aparece transcript final;
- [ ] agente responde;
- [ ] TTS es audible;
- [ ] ejecutar una tool real;
- [ ] ejecutar un caso RAG;
- [ ] barge-in corta reproducción anterior;
- [ ] no hay bloqueo evidente del event loop.

## Gate

Una llamada telefónica completa debe funcionar de extremo a extremo.

---

# PHASE 3 — Timeline

## Objetivo

Convertir la llamada en una secuencia reproducible de eventos.

## Implementar

Crear `CallSessionContext` por llamada:

```text
call_id
organization_id
conversation_id
telnyx_call_control_id
call_leg_id
call_session_id
started_at
monotonic_zero
next_sequence
recording_offset_ms
lifecycle_state
```

Cada evento debe tener:

```text
seq
occurred_at
offset_ms
provider_occurred_at
```

Añadir mediante Alembic:

```text
calls
call_events
transcript_segments
recordings
```

No editar migraciones antiguas.

Implementar:

- [ ] `CallTimelineService`;
- [ ] tipos de evento centrales;
- [ ] queue de persistencia acotada;
- [ ] sesiones SQL cortas;
- [ ] writer independiente del Media WS;
- [ ] final transcripts;
- [ ] tools;
- [ ] RAG;
- [ ] lifecycle;
- [ ] estado de agente.

## Validación

- [ ] `seq` crece estrictamente por llamada.
- [ ] Dos llamadas tienen secuencias independientes.
- [ ] `offset_ms` nunca retrocede.
- [ ] timeline se reconstruye desde PostgreSQL.
- [ ] DB lenta no bloquea audio.
- [ ] upgrade Alembic funciona.
- [ ] downgrade Alembic funciona.
- [ ] llamadas browser siguen funcionando.

## Gate

Una llamada finalizada debe producir un timeline persistente ordenado.

---

# PHASE 4 — Monitor LIVE

## Objetivo

Permitir que un admin observe una llamada específica.

## Protocolo

```json
{"type":"auth","token":"..."}
{"type":"subscribe.call","call_id":"..."}
```

Backend:

- [ ] obtener organización desde JWT;
- [ ] cargar llamada;
- [ ] comprobar tenant;
- [ ] suscribir por `(organization_id, call_id)`;
- [ ] unsubscribe;
- [ ] cambio de llamada;
- [ ] rechazar llamadas ajenas.

Frontend:

- [ ] listar llamadas activas;
- [ ] seleccionar llamada;
- [ ] duración;
- [ ] transcript;
- [ ] tools;
- [ ] RAG;
- [ ] agent state;
- [ ] lifecycle;
- [ ] reconnect.

## Gate

Un admin solo puede ver llamadas de su organización.

Cerrar el monitor NO afecta la llamada.

---

# PHASE 5 — Live audio

## Objetivo

El administrador escucha CUSTOMER y AGENT en tiempo real.

## Implementar

Crear `LiveAudioHub`.

```text
WS /ws/calls/{call_id}/audio
```

Tap CUSTOMER:

```text
Telnyx
→ normalize PCM
→ tap
→ Sherpa
```

Tap AGENT:

```text
Piper
→ tap
→ Telnyx
```

Reglas:

- [ ] nunca bloquear caller;
- [ ] queue acotada por viewer;
- [ ] `drop-oldest` ante atraso;
- [ ] separar audio y JSON;
- [ ] auth admin;
- [ ] tenant isolation.

Frames deben contener:

```text
version
channel
flags
seq
offset_ms
PCM
```

Frontend:

- [ ] jitter buffer;
- [ ] `AudioWorklet`;
- [ ] CUSTOMER;
- [ ] AGENT;
- [ ] reconexión.

## Gate

- [ ] admin escucha CUSTOMER;
- [ ] admin escucha AGENT;
- [ ] monitor lento no ralentiza llamada;
- [ ] monitor congelado no aumenta memoria sin límite;
- [ ] dos llamadas no mezclan audio.

---

# PHASE 6 — Recording

## Objetivo

Generar grabación histórica canónica.

## Telnyx

Crear `TelnyxRecordingController`.

Usar:

```text
format = wav
channels = dual
recording_track = both
trim = disabled
```

Gestionar:

```text
call.recording.saved
call.recording.error
```

Estados:

```text
RECORDING
→ PROCESSING
→ DOWNLOADING
→ READY
```

## Worker

- [ ] nuevo job de recording;
- [ ] preservar `enrich_conversation`;
- [ ] descarga streaming;
- [ ] archivo temporal;
- [ ] rename atómico;
- [ ] SHA-256;
- [ ] tamaño;
- [ ] duración;
- [ ] canales;
- [ ] waveform peak/RMS;
- [ ] idempotencia.

Storage:

- [ ] backend y worker comparten filesystem;

o:

- [ ] usar Blob Storage.

Seguridad:

- [ ] filename derivado de UUID interno;
- [ ] no confiar en paths externos;
- [ ] no loggear recording URL;
- [ ] límite máximo de tamaño;
- [ ] timeout;
- [ ] validar WAV.

Patter `LocalCallRecorder` queda únicamente como fallback/debug.

## Gate

Después de colgar debe existir un WAV dual persistente y autorizado.

---

# PHASE 7 — Replay

## Objetivo

Reproducir cualquier llamada histórica sincronizada.

## API

```text
GET /calls/{id}
GET /calls/{id}/timeline
GET /calls/{id}/recording
```

Reglas:

- [ ] JWT obligatorio;
- [ ] tenant desde usuario;
- [ ] nunca aceptar `organization_id` del cliente;
- [ ] 404 para recursos ajenos;
- [ ] recording soporta range requests.

## Frontend

Reloj maestro:

```text
timelineMs =
  audio.currentTime * 1000
  + recordingOffsetMs
```

Sincronizar:

- [ ] waveform;
- [ ] transcript;
- [ ] agent state;
- [ ] tools;
- [ ] RAG;
- [ ] lifecycle.

Seek debe reconstruir estado desde eventos.

No guardar snapshots de UI.

## Gate

Validar seek:

- [ ] inicio;
- [ ] mitad;
- [ ] durante tool;
- [ ] durante barge-in;
- [ ] final.

El mismo timestamp debe producir siempre el mismo estado.

---

# PHASE 8 — Marks + hardening

## Objetivo

Eliminar inconsistencias restantes y validar concurrencia.

Implementar `send_mark()` real.

Distinguir:

```text
audio generated
audio sent
audio played
audio cancelled
```

- [ ] procesar mark ack;
- [ ] reconciliar marks tras `clear`;
- [ ] webhooks duplicados;
- [ ] webhooks fuera de orden;
- [ ] media reconnect;
- [ ] cleanup;
- [ ] recording failure;
- [ ] download failure;
- [ ] hangup abrupto;
- [ ] tool failure;
- [ ] cancelación async generator;
- [ ] benchmark PCMU vs L16;
- [ ] WSS Azure real.

## Gate final

Ejecutar DOS llamadas PSTN simultáneas durante varios minutos.

Validar:

- [ ] no cruza audio;
- [ ] no cruza transcript;
- [ ] no cruza contexto;
- [ ] no cruza tenant;
- [ ] tools independientes;
- [ ] barge-in independiente;
- [ ] timeline de ambas;
- [ ] recording de ambas;
- [ ] monitor aislado;
- [ ] replay de ambas;
- [ ] cleanup correcto;
- [ ] memoria vuelve a rango normal.

---

# Validación común después de cada fase

Ejecutar según corresponda:

```bash
cd backend && .venv/bin/pytest
cd frontend && pnpm test
./scripts/build.sh
./scripts/smoke.sh
```

Cuando existan migraciones:

```text
DB limpia → upgrade
DB actual → upgrade
downgrade
upgrade nuevamente
```

Las pruebas Telnyx deben dividirse en:

```text
fake Telnyx
+
PSTN real
```

No declarar `PASS` externo usando exclusivamente mocks.

---

# Reglas de calidad globales

## Seguridad

- [ ] firmas Ed25519;
- [ ] anti-replay;
- [ ] webhooks idempotentes;
- [ ] secretos fuera de logs;
- [ ] URLs recording fuera del frontend;
- [ ] tenant isolation;
- [ ] límites de payload;
- [ ] storage seguro.

## Concurrencia

- [ ] nada pesado bloquea event loop;
- [ ] ninguna `AsyncSession` dura todo el Media WS;
- [ ] queues acotadas;
- [ ] viewers desacoplados;
- [ ] recording desacoplado;
- [ ] estado por llamada.

## Compatibilidad

No romper:

```text
/ws/call
/ask
/transcribe
/synthesize
stream_agent
tools
RAG
worker
analytics
```

---

# Fuera de alcance

No implementar:

- WhatsApp;
- CRM nuevo;
- microservicio de telefonía;
- Kafka;
- Celery;
- RQ;
- Patter Dashboard;
- Patter MetricsStore;
- nuevo AgentRuntime;
- Deepgram;
- ElevenLabs;
- autoscaling telefónico;
- multi-region;
- múltiples réplicas;
- persistencia de frames;
- todos los partial transcripts;
- carga masiva.

---

# Rollback

Feature flag principal:

```text
TELNYX_ENABLED=false
```

Ante problema:

1. desactivar Telnyx;
2. preservar `/ws/call`;
3. desasociar el número Telnyx si es necesario;
4. volver al commit anterior del fork;
5. conservar nuevas tablas;
6. conservar grabaciones;
7. no borrar llamadas existentes;
8. si L16 falla, permitir temporalmente PCMU 8 kHz manteniendo el boundary de conversión.

---

# Regla final para el agente

Nunca avances porque “parece funcionar”.

Avanza solamente cuando:

```text
implementation complete
+
tests pass
+
checklist complete
+
gate verified
+
evidence reported
```

Si un gate falla:

```text
STOP
→ diagnosticar
→ corregir
→ repetir validación
```

No comenzar la siguiente fase hasta recibir instrucción explícita.