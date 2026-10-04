# Stateful omnichannel backend

The backend keeps operational memory in `AgentSnapshot` per tenant-scoped conversation. `AgentState` is a versioned application contract; chat messages remain the transcript, while facts carry provenance, key actions describe meaningful changes, and tool history records bounded results. `GET /conversations/{id}/agent-state` is an authenticated tenant-scoped view. The authenticated `PUT /conversations/{id}/whatsapp` endpoint requires an operator assertion of verified identity and an existing matching customer phone before it links the WhatsApp identity. `POST /conversations/{id}/agent-resume` clears a human handoff and pending consent after operator action.

Sentiment evaluators `satisfaction` and `frustration` use a 5-level ordinal scale (`very_low`, `low`, `neutral`, `high`, `very_high`) plus `unknown`, so a UI can place them on a 5-level ruler. Decision-critical signals (`confirmation`, `integrity`, `human`) and `intent` keep their discrete labels because the runtime policy and safety checks depend on their exact values.

The conversation runtime selects bounded operational context and recent messages. It buffers the LLM draft, evaluates integrity with official TypeSafe Jev, and only then yields the existing token/done events consumed by `/ask` and `/ws/call`. Jev timeout, missing credentials, or invalid classification is unknown; it never grants consent. The write policy requires the same proposal to be presented, an exact deterministic consent phrase (`confirmo`, `sí, confirmo`, ...), Jev's `confirmation=explicit` label, and an unchanged operation fingerprint. The numeric confidence is deliberately not used as a threshold: on the pinned model a real "confirmo" returns explicit at ~0.44, so consent is anchored on the deterministic phrase and Jev only has to agree. Integrity gating is likewise label-based (`supported` vs `unsupported`/`uncertain`); honest failure and clarification drafts are supported, invented success claims are not. Voice proposals become presentable only after Telnyx playback acknowledgement. WhatsApp proposals become presentable after Meta reports delivered/read. A requested human stops autonomous actions until an authenticated operator resumes the conversation.

`AgentOperation` durably records write intent before effects. Restaurant availability is simulated; the demonstration booking result is deterministic and lives in this ledger rather than a live restaurant backend. Replays return the recorded result. A started/uncertain external operation is not repeated blindly. For Telnyx callbacks, the idempotent command identifier and base64 server-owned `client_state` correlate signed provider events with the original conversation; the first verified event reconciles a lost dial response. Events whose signed correlation cannot be mapped are ignored. Existing Telnyx event deduplication is process-local, while business write intents are PostgreSQL-backed.

## WhatsApp

`GET/POST /webhooks/whatsapp` implement Meta challenge verification, raw-body HMAC-SHA256 validation, a 1 MiB webhook envelope limit, event-batch parsing, durable deduplication, and ACK only after database commit. PostgreSQL inbox/outbox rows are authoritative; Redis is only a wake-up. The worker also scans pending/stale rows to recover from lost queue pushes or worker restarts. Each event is serialized with a PostgreSQL advisory lock. Outgoing uncertain sends are not retried automatically because provider acceptance may have occurred despite a client timeout. Delivery statuses are monotonic and are stored separately from user messages.

Text, button/list replies and incoming audio voice notes are normalized into the same conversation. Audio metadata/download uses the authenticated Meta Graph endpoint, HTTPS media-host allowlisting, a 16 MiB bound, SHA-256 verification and the existing Sherpa `ffmpeg` decoder with a 120-second bound. The transcript is capped at 8,000 characters. Other message types receive a safe textual failure; outbound audio and general image/document understanding are not supported.

WhatsApp text is sent only within the last-user-message 24-hour service window. Outside that window an explicit opt-in and an approved template are required; no arbitrary user text is injected into the template. Voice calls do not reset that window. Operator binding is intended for a demo number and requires an already stored customer phone. The endpoint trusts the authenticated operator's `identity_verified` assertion; it does not itself perform OTP verification.

## Minimal configuration

Existing `DATABASE_URL`, `REDIS_URL`, LLM and Telnyx settings are reused. New secrets, only in `backend/.env` (not source control):

```dotenv
TYPESAFE_API_KEY=
WHATSAPP_ACCESS_TOKEN=
WHATSAPP_APP_SECRET=
WHATSAPP_VERIFY_TOKEN=
WHATSAPP_PHONE_NUMBER_ID=
```

The Graph API version (`v23.0`) and Jev model (`jev-1.13.0`) are deliberately pinned in code. The Meta business account must subscribe to the `messages` webhook field, grant messaging permissions, and have approved templates. Telnyx must have an outbound-enabled Voice API application and verified origin number. `TELNYX_PHONE_NUMBER`, `TELNYX_CONNECTION_ID`, and existing webhook/signature settings supply those values. WhatsApp callbacks/notifications are not a replacement for a genuine human operator queue; human handoff is recorded and automation is paused.

## Migration, operation, and rollback

Apply Alembic revision `0007_stateful_agent` after taking the project's normal database backup. It is additive and does not backfill or rewrite existing conversation messages. Revert by stopping the new app/worker paths and deploying the previous app; retain new tables while diagnosing. Do **not** downgrade while snapshots, bindings or operation history must be retained. External reservations/calls/messages cannot be rolled back by a database downgrade.

Run `python -m app.worker` alongside the API. Pending WhatsApp rows are recovered from PostgreSQL; stale externally-sending rows become `uncertain` and need provider/operator reconciliation, never an automatic resend. Keep webhook secrets out of logs and never log message/media bodies. Configure access controls and retention in Meta/TypeSafe accounts before sending customer data.

## Verification boundaries

Unit tests cover exact confirmation binding, signature bytes, batch parsing, window/template rules and media validation. The disposable PostgreSQL integration suite exercises migration, cross-tenant isolation, persistence/replay, turn serialization, integrity gating, inbox/outbox, callback and cancellation. Build and HTTP health smoke do not prove real Meta, Jev or Telnyx account configuration; a sandbox message, voice note, delivered callback and outbound test call remain required before production use. `ffmpeg` already exists in the backend container image.
