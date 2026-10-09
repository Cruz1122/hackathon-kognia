# Golden Benchmark IPS

El benchmark vive en `backend/evals/ips_golden/` y no se importa desde el camino de producción. Construye exactamente 60 casos a partir de un snapshot IPS ya normalizado en PostgreSQL:

| Categoría | Casos |
|---|---:|
| Búsquedas estructuradas | 10 |
| Información de sedes | 8 |
| Capacidades y comparaciones | 8 |
| Búsquedas semánticas/STT | 8 |
| Conversaciones multitur​no | 10 |
| Adaptación JEV | 6 |
| Seguridad y alcance | 6 |
| Fallos y recuperación | 4 |

La semilla es `20261009`. Los valores oficiales no están hardcodeados: el plan selecciona nombres, `site_code`, teléfonos, direcciones, cantidades, nulos y homónimos desde el `snapshot_id` y `source_hash` elegido. Si el snapshot no tiene las propiedades necesarias, el runner falla explícitamente en lugar de inventar fixtures oficiales.

## Smoke y modo offline

El modo por defecto simula LLM y JEV, pero ejecuta el coordinador stateful, el registro de tools y PostgreSQL. No realiza llamadas de pago. Después de construir la imagen actual:

```bash
export GIT_COMMIT="$(git rev-parse HEAD)"
mkdir -p var/evals
chmod a+rwX var/evals
docker compose run --rm --no-deps \
  -w /app/backend \
  -v "$PWD/var/evals:/app/backend/var/evals" \
  backend python -m evals.ips_golden run \
  --mode offline \
  --limit 1 \
  --output var/evals/ips-golden-smoke
```

El smoke escribe `manifest.json`, `results.jsonl` y `report.md`. `--limit 1` comprueba el camino crítico; la suite completa usa los 60 casos:

```bash
docker compose run --rm --no-deps \
  -w /app/backend \
  -v "$PWD/var/evals:/app/backend/var/evals" \
  backend python -m evals.ips_golden run \
  --mode offline \
  --output var/evals/ips-golden-offline
```

Para reproducir exactamente una ejecución, pasar ambos identificadores guardados en el manifest:

```bash
docker compose run --rm --no-deps \
  -w /app/backend \
  -v "$PWD/var/evals:/app/backend/var/evals" \
  backend python -m evals.ips_golden run \
  --mode offline \
  --snapshot-id "<snapshot_id>" \
  --source-hash "<source_hash>" \
  --output var/evals/ips-golden-replay
```

El runtime de producto solo consulta el snapshot activo; por eso el runner exige que el snapshot seleccionado esté activo. `snapshot_id` y `source_hash` se validan y se registran en cada resultado.

## Juez posterior

El juez es independiente del agente y corre después, fuera del camino crítico. No genera Ground Truth ni convierte un fallo determinista en aprobado. Para validar contratos sin red:

```bash
docker compose run --rm --no-deps \
  -w /app/backend \
  -v "$PWD/var/evals:/app/backend/var/evals" \
  backend python -m evals.ips_golden judge \
  --input var/evals/ips-golden-offline/results.jsonl \
  --output var/evals/ips-golden-offline/judge.jsonl \
  --provider fake

docker compose run --rm --no-deps \
  -w /app/backend \
  -v "$PWD/var/evals:/app/backend/var/evals" \
  backend python -m evals.ips_golden report \
  --input var/evals/ips-golden-offline/results.jsonl \
  --judge-input var/evals/ips-golden-offline/judge.jsonl \
  --output var/evals/ips-golden-offline/report-with-judge.md
```

El modo remoto requiere una activación separada y explícita. Es un endpoint OpenAI-compatible independiente; no se reutilizan las credenciales del agente:

```bash
export GOLDEN_JUDGE_API_KEY="..."
docker compose run --rm --no-deps \
  -w /app/backend \
  -v "$PWD/var/evals:/app/backend/var/evals" \
  -e GOLDEN_JUDGE_API_KEY \
  backend python -m evals.ips_golden judge \
  --input var/evals/ips-golden-offline/results.jsonl \
  --output var/evals/ips-golden-offline/judge-live.jsonl \
  --provider openai_compatible \
  --base-url "https://<judge-host>/v1" \
  --model "<judge-model>"
```

El prompt versionado es `backend/evals/ips_golden/prompts/judge_v1.md`; cada score guarda provider, modelo y versión.

## Comparación offline vs. live

El modo live del agente también requiere confirmación explícita. Para que los percentiles sean comparables, ejecuta exactamente los mismos `--case-id`, snapshot y repeticiones en ambos modos. Después puede reevaluarse un resultado existente sin repetir ni pagar providers; esto es útil cuando solo cambia una regla determinista:

```bash
docker compose run --rm --no-deps \
  -w /app/backend \
  -v "$PWD/var/evals:/app/backend/var/evals" \
  backend python -m evals.ips_golden reevaluate \
  --input var/evals/ips-golden-live/results.jsonl \
  --output var/evals/ips-golden-live/results-reevaluated.jsonl
```

Genera la tabla pareada de P50, P95, máximo y multiplicador con:

```bash
docker compose run --rm --no-deps \
  -w /app/backend \
  -v "$PWD/var/evals:/app/backend/var/evals" \
  backend python -m evals.ips_golden compare-latency \
  --offline-input var/evals/ips-golden-offline/results-reevaluated.jsonl \
  --live-input var/evals/ips-golden-live/results-reevaluated.jsonl \
  --output var/evals/ips-golden-latency-comparison.md
```

`compare-latency` rechaza conjuntos con casos, repeticiones, `snapshot_id` o `source_hash` diferentes. La ejecución live que genera los datos requiere `--mode live --confirm-live` y puede tener coste; reevaluar y comparar no hacen llamadas externas.

## Qué se captura

Cada turno conserva entrada, historial, respuesta final, eventos, tool calls y argumentos, resultado de cada tool, señales y comportamiento JEV, errores, fallbacks y `TraceRecorder`. Las spans incluyen generación/primer token, tools, `jev.observe`, `jev.integrity`, retrieval IPS, operaciones PostgreSQL, Chroma, duración total y estado de caché fría/caliente. El adaptador opcional `voice.measure_voice_turn` agrega spans separados para STT, TTS y primer audio; el modo offline de texto no afirma medir audio real.

### Backchannel de voz y control anti-spam

El camino de voz precarga seis frases cortas de Piper al calentar el pipeline. El backchannel solo se agenda cuando el runtime emite el guard estructural `agent.guard` y la conversación está en `buscando`, o cuando ya comenzó una operación de tool/RAG. No usa un clasificador nuevo de keywords ni modifica la lógica de negocio del agente.

Se espera 700 ms antes de reproducirlo, se permite como máximo una frase por turno, no se emite durante saludos, aclaraciones, cierre o emergencia, y el audio final cancela el backchannel pendiente y comparte el mismo coordinador de reproducción. El coordinador mantiene un único audio activo; la cancelación por barge-in existente sigue siendo la autoridad para interrumpir playback.

El `TraceRecorder` separa `time_to_first_audio_ms` (incluye backchannel si se reproduce) de `time_to_useful_answer_ms` (primer audio de la respuesta final). Esas métricas permiten ver si se reduce el silencio inicial sin confundir una frase de espera con la respuesta útil.

En evaluación, la comprobación determinista de alcance se limita a la superficie real de tools, snapshots y contratos. Claims de texto libre sobre disponibilidad o acciones no soportadas se revisan post-run mediante el juez, sin bloquear ni alterar la respuesta del agente.

## Verificación local

Las pruebas estructurales no requieren PostgreSQL:

```bash
cd backend
pytest -q tests/test_ips_golden.py
```

La suite relevante del dominio puede ejecutarse sin live providers:

```bash
cd backend
pytest -q tests/test_ips_ingestion.py tests/test_ips_memory.py tests/test_domain_tools_registry.py tests/test_tracing.py tests/test_ips_golden.py
```

La instalación, `pytest` y CI nunca llaman al juez remoto, a LLM live ni a APIs de pago. El comando live exige `--confirm-live` y el reporte marca sus limitaciones.
