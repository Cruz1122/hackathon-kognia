# Frontend

Astro 7.3.1, pnpm 11.3.0, Node ≥ 22.12. Build estático; `PUBLIC_API_URL` se inyecta en build Docker.

| Documento | Contenido |
| --- | --- |
| [voice-call.md](voice-call.md) | Páginas, módulos de llamada y monitoreo |

## Páginas

| Ruta | Archivo | Rol |
| --- | --- | --- |
| `/` | `frontend/src/pages/index.astro` | Login ADMIN, llamada PCM, toasts |
| `/monitoring` | `frontend/src/pages/monitoring.astro` | Timeline local o hub `/ws/events` |

## UI

Skill obligatorio: `.agents/skills/gooey-ui-system/SKILL.md`.

- Paleta: `#414141`, `#f7c974`, `#faeccf`, `#f8f8f8`
- Tipografía: Urbanist
- Feedback inmediato: `frontend/src/features/voice-call/infrastructure/toast.ts` + `src/styles/toast.css`

## Tests frontend

```bash
cd frontend && pnpm test
```

Cubre los `*.test.ts` del frontend (`node --test` + strip-types), entre ellos errores de API, panel de detalle y tarjeta de retrieval.
