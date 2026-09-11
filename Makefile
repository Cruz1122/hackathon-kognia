.PHONY: help setup smoke \
	dev-api dev-web \
	build build-api build-web \
	check-venv

PYTHON := backend/.venv/bin/python
HOST := 127.0.0.1
WEB_PORT := 18473
API_PORT := 18474

help:
	@echo "make setup      instala dependencias"
	@echo "make dev-api    arranca la API (FastAPI)"
	@echo "make dev-web    arranca la web (Astro)"
	@echo "make build      compila API + web"
	@echo "make build-api  compileall de Python"
	@echo "make build-web  build de Astro"
	@echo "make smoke      health check de la API"

setup:
	./scripts/setup.sh

check-venv:
	@test -x "$(PYTHON)" || { echo "Falta backend/.venv. Ejecuta make setup" >&2; exit 1; }

dev-api: check-venv
	cd backend && .venv/bin/python -m uvicorn app.main:app --reload --reload-dir app --host $(HOST) --port $(API_PORT)

dev-web:
	cd frontend && pnpm run dev

build: build-api build-web
	@echo "Build OK"

build-api: check-venv
	@echo "[api] Compilando módulos Python"
	$(PYTHON) -m compileall -q backend/app

build-web:
	@echo "[web] Generando build de Astro/Vite"
	cd frontend && pnpm run build

smoke:
	./scripts/smoke.sh
