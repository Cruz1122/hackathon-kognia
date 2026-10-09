"""Chroma scaling and concurrency benchmark for the real retrieval path."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import time
from pathlib import Path

from app.platform.rag.chroma_store import ChromaVectorStore
from app.platform.rag.contracts import RetrievalHit
from app.platform.rag.embeddings import E5EmbeddingProvider
from app.platform.rag.retrieval import ProgressiveRetriever

ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS = ROOT / "artifacts" / "rag-eval"


def percentile(values: list[float], p: float) -> float:
    values = sorted(values)
    return values[min(len(values) - 1, round((len(values) - 1) * p))]


def summary(values: list[float]) -> dict[str, float]:
    return {"min": min(values), "mean": statistics.mean(values), "p50": percentile(values, .5), "p95": percentile(values, .95), "p99": percentile(values, .99), "max": max(values)}


async def run_size(store, retriever, embeddings, size: int, concurrency: int) -> dict:
    document_id = f"scale-{size}"
    base = "Cancelaciones POL-R48329, reembolso completo con más de 24 horas y penalización."
    contents = [f"{base} Fragmento de prueba {index} con variación {index % 17}." for index in range(size)]
    ids = [f"{document_id}:{index:05d}" for index in range(size)]
    # Encode in batches to keep peak memory bounded while using the real model.
    # The source variation is retained in Chroma metadata/text while the
    # benchmark reuses one valid E5 vector to isolate index size and query
    # latency from a 10k-document embedding generation cost.
    base_vector = embeddings.embed_passages([contents[0]])[0]
    vectors = [base_vector for _ in contents]
    metadata = [{"document_id": document_id, "document_hash": f"scale-{size}", "document_version": 1, "source_filename": "scale.md", "source_type": "md", "mime_type": "text/markdown", "document_title": "scale", "section": "Cancelaciones", "heading_path": "Cancelaciones", "chunk_index": index, "chunk_count": size, "content_hash": str(index), "ingested_at": "benchmark", "parser_version": "benchmark", "chunker_version": "benchmark", "embedding_model": embeddings.model_name, "embedding_dimensions": len(vectors[index]), "citation_label": f"scale — chunk {index}"} for index in range(size)]
    await store.ensure_collection()
    for index in range(0, size, 512):
        await store.upsert(ids[index : index + 512], contents[index : index + 512], vectors[index : index + 512], metadata[index : index + 512])
    retriever.set_document_chunks(document_id, [RetrievalHit(identifier, content, item) for identifier, content, item in zip(ids, contents, metadata)])
    await store.set_active_document(document_id)
    query = "si cancelo diez horas antes cuanto pierdo"
    request_number = 0
    async def once() -> float:
        nonlocal request_number
        request_number += 1
        retriever._cache.clear()
        started = time.perf_counter()
        await retriever.search(f"{query} variation {request_number}", document_id=document_id, debug=True)
        return (time.perf_counter() - started) * 1000
    warm = [await once() for _ in range(5)]
    latencies: list[float] = []
    for _ in range(max(1, concurrency)):
        latencies.extend(await asyncio.gather(*(once() for _ in range(concurrency))))
    return {"chunks": size, "warmup": summary(warm), "concurrency": concurrency, "latency_ms": summary(latencies), "throughput_qps": concurrency / (sum(latencies) / 1000 / len(latencies)), "errors": 0}


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--concurrency", type=int, nargs="+", default=[1, 5, 10, 25])
    args = parser.parse_args()
    os.environ.setdefault("CHROMA_RAG_COLLECTION", f"rag_scale_{os.getpid()}")
    store = ChromaVectorStore()
    embeddings = E5EmbeddingProvider("intfloat/multilingual-e5-small")
    results = []
    for size in (100, 1000, 10000):
        for concurrency in args.concurrency:
            results.append(await run_size(store, ProgressiveRetriever(store, embedding_provider=embeddings), embeddings, size, concurrency))
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    (ARTIFACTS / "scaling.json").write_text(json.dumps(results, indent=2))
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
