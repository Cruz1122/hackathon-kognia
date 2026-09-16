# Arquitectura

Índice de límites y flujos.

**Hoy (verificado):** un proceso FastAPI + Astro + PostgreSQL. Hub in-memory. No hay cola, Chroma ni worker.

**Objetivo (candidato):** monolito modular + procesos especializados (worker, Chroma). Dominio de telefonía extraíble.

| Documento | Contenido |
| --- | --- |
| [overview.md](overview.md) | Componentes actuales, flujo feliz, no-objetivos |
| [data-flows.md](data-flows.md) | Turno de voz, persistencia, fan-out de eventos |
| [target.md](target.md) | Capas, Compose objetivo, conversación vs canal |

**Relacionado:** [Producto](../product/README.md) · [API](../api/README.md) · [Datos](../data/README.md) · [Backend](../backend/README.md)
