# Verificación

Objetivo hackathon: build + smoke del camino crítico, no una suite exhaustiva.

## Build

```bash
make build
```

- API: `python -m compileall backend/app`
- Web: `pnpm run build` con `PUBLIC_API_URL`

## Smoke (API levantada)

```bash
make smoke
```

`scripts/smoke.sh`:

1. `GET /health/live` debe ser 2xx (`curl --fail`)
2. `GET /health/ready` acepta 200 o 503
3. Sin token: `POST /ask` debe ser **401**
4. Con `SMOKE_TOKEN` y `SMOKE_CONVERSATION_ID`: `/ask` 200/502/503; con `SMOKE_LLM=1` exige `event: done`

## Tests backend

```bash
cd backend && .venv/bin/pytest
```

Índice de archivos: [Referencia → tests](../reference/tests.md).

## Tests frontend

```bash
cd frontend && pnpm test
```

## Checklist demo

1. Backend arranca; `/health/live` ok.
2. `/health/ready` 200 (Sherpa, Piper, Postgres).
3. SUPERADMIN + org + ADMIN existen.
4. Login en `/`, llamada PCM, respuesta hablada.
5. `/monitoring` con la misma sesión ve eventos JSON (no audio).
