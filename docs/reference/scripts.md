# Scripts y Makefile

**Estado:** verificado contra `Makefile` y `scripts/`.

| Target / script | Efecto |
| --- | --- |
| `make setup` | `scripts/setup.sh` |
| `make dev-api` | uvicorn `app.main:app` `:18474` reload |
| `make dev-web` | `pnpm run dev` en `frontend/` |
| `make build` | compileall + astro build |
| `make build-api` / `build-web` | por pieza |
| `make smoke` | `scripts/smoke.sh` |
| `scripts/dev-api.sh` / `dev-web.sh` | equivalentes a make |
| `scripts/download-sherpa-model.sh` | modelo STT |
| `scripts/download-piper-model.sh` | voz TTS |
| `scripts/build.sh` | compileall + `astro build` (equivalente a `make build`) |

`make check-venv` (interno): exige `backend/.venv/bin/python`.
