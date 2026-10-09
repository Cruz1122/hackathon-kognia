# Inteligencia: RAG, analytics, Sales Space

**Estado:** candidato (equipo). No hay Chroma, embeddings ni dashboard comercial en el código.

## RAG (horizontal)

Tools = acciones estructuradas. RAG = lo que no es transacción (políticas, docs). Complementario, no sustituto (AP-07).

```text
request → agent → ¿tool? sí → tool
                         no → knowledge retrieval → RAG
```

## Chroma

100% local. No vector DB SaaS. HTTP propio; volumen persistente. Collections separadas conceptualmente, p. ej. `rag_documents` y `conversation_embeddings`.

Backend y worker hablan Chroma por HTTP. Chroma **no** es verdad transaccional (AP-08 / AP-09).

## Dashboard comercial

No KPIs genéricos. North-star: por qué se pierden ventas, objeciones, diferencial de agentes, productos que interesan y no cierran, competidores, punto de deterioro, segmentos, **qué cambiar mañana**.

Métricas derivadas solo si sirven a `behavior → outcome`. Talk/listen ratio no es calidad universal.

Outcome positivo principal: **`won`**. Extra como mucho `lost` / `pending`. No veinte estados.

## Sales Space (experimental ML/DL)

Cada punto ≈ una **SALE**. Al seleccionar: venta, customer, agent, call, transcript, products, outcome, metadata → evidencia original.

Vector **híbrido**: embeddings de texto (transcript, summary, objections) **más** features estructuradas (duration, agent, product, campaign, segment, discount, order value, métricas, tiempo). **No** solo embeddings semánticos.

Pipeline offline (no al abrir el dashboard):

```text
Postgres → extract → features → embeddings → hybrid vector
  → normalize → Autoencoder → latent → 3D coords → store → API → Three.js
```

`won` es **label/metadata** (color, filtro, evaluación), **no** feature de entrenamiento (AP-12).

Autoencoder: decisión deliberada (latente + demostrar DL). Internamente se puede comparar con PCA/UMAP; la demo usa AE propio salvo bloqueo técnico.

Un único espacio + filtros temporales (`today`, 7d, month, quarter, year, custom), no un espacio por periodo.

## Jobs analíticos

Manual / daily / monthly / yearly + **Rebuild Analytics** (`POST /internal/analytics/rebuild` como ejemplo). Sin orquestador enterprise. En el worker (AP-10).

## Datos sintéticos

~50k ventas, `seed = 42`, reproducible, **relaciones deliberadas** (segmento+agente+producto → conversión; objeción de precio sin descuento → peor close). Dominio sustituible por factories (pizza, healthcare, finance, …).

Metadatos mínimos: seed, generated_at, dataset hash, model config. No MLOps.

Hackatón: telefonía **real**; histórico **puede ser sintético** (AP-16).
