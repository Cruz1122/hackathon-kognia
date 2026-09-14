"""Reproducible RAG quality/latency evaluation against the real pipeline."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import statistics
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from app.platform.rag.chroma_store import ChromaVectorStore
from app.platform.rag.embeddings import E5EmbeddingProvider
from app.platform.rag.ingestion import RagIngestionService
from app.platform.rag.retrieval import ProgressiveRetriever
from app.platform.rag.citations import build_knowledge_context

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "rag"
ARTIFACTS = ROOT / "artifacts" / "rag-eval"


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    index = min(len(values) - 1, max(0, round((len(values) - 1) * p)))
    return values[index]


def stats(values: list[float]) -> dict[str, float]:
    return {"min": min(values, default=0.0), "mean": statistics.mean(values) if values else 0.0, "p50": percentile(values, .50), "p95": percentile(values, .95), "p99": percentile(values, .99), "max": max(values, default=0.0)}


def hit_matches(hit: Any, sections: list[str]) -> bool:
    if not sections:
        return False
    haystack = f"{hit.metadata.get('section', '')}\n{hit.metadata.get('heading_path', '')}\n{hit.content}".casefold()
    return any(section.casefold() in haystack for section in sections)


def metrics(results: list[dict[str, Any]]) -> dict[str, Any]:
    quality: dict[str, dict[str, float]] = {}
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in results:
        groups[item["category"]].append(item)
    for category, values in {"overall": results, **groups}.items():
        if not values:
            continue
        recall1 = sum(bool(item["top1_match"]) for item in values) / len(values)
        recall3 = sum(bool(item["top3_match"]) for item in values) / len(values)
        reciprocal = sum((1 / item["first_match_rank"] if item["first_match_rank"] else 0) for item in values) / len(values)
        no_evidence = [item for item in values if not item["should_have_evidence"]]
        no_evidence_accuracy = sum(item["evidence_state"] == "INSUFFICIENT" for item in no_evidence) / len(no_evidence) if no_evidence else 0.0
        false_supported = sum(item["evidence_state"] != "INSUFFICIENT" for item in no_evidence) / len(no_evidence) if no_evidence else 0.0
        supported = [item for item in values if item["should_have_evidence"]]
        quality[category] = {"n": len(values), "recall_at_1": recall1, "recall_at_3": recall3, "mrr_at_3": reciprocal, "evidence_n": len(supported), "evidence_recall_at_3": sum(item["top3_match"] for item in supported) / len(supported) if supported else 0.0, "no_evidence_accuracy": no_evidence_accuracy, "false_supported_answer_rate": false_supported}
    return quality


def markdown_report(report: dict[str, Any], failures: list[dict[str, Any]]) -> str:
    quality_lines = ["| Category | N | Recall@1 | Recall@3 | Evidence R@3 | MRR@3 | No-evidence accuracy |", "|---|---:|---:|---:|---:|---:|---:|"]
    for category, value in report["quality"].items():
        quality_lines.append(f"| {category} | {value['n']} | {value['recall_at_1']:.3f} | {value['recall_at_3']:.3f} | {value['evidence_recall_at_3']:.3f} | {value['mrr_at_3']:.3f} | {value['no_evidence_accuracy']:.3f} |")
    verdict = "PASS" if report["quality"]["overall"]["recall_at_3"] >= .95 and report["quality"]["overall"]["mrr_at_3"] >= .85 and report["quality"]["overall"]["no_evidence_accuracy"] >= .95 and not report["version_check"]["old_version_leakage"] else "FAIL"
    failures_text = "\n".join(f"- `{item['id']}` ({item['category']}): {item['query']} — expected {item['gold_sections']}, ranked {item['ranked_chunks']}" for item in failures[:30]) or "- None"
    return f"""# RAG evaluation report

## Corpus

- Seed: `42`
- v1 chunks: `{report['corpus']['v1']['chunks']}`
- v2 chunks: `{report['corpus']['v2']['chunks']}`
- Embedding model: `{report['corpus']['embedding_model']}`
- Dimensions: `{report['corpus']['dimensions']}`
- Active v2 leakage: `{report['version_check']['old_version_leakage']}`

## Quality

{chr(10).join(quality_lines)}

## Retrieval levels

```json
{json.dumps(report['levels'], indent=2)}
```

## Latency (retrieval-only, milliseconds)

```json
{json.dumps(report['latency_ms'], indent=2)}
```

Cold ingestion: `{report['cold_ingestion_ms']:.2f} ms`; replacement: `{report['replacement_ms']:.2f} ms`.

## Failures

{failures_text}

## Limitations

- This run evaluates the real E5 + Chroma pipeline serially with a `{report['corpus']['v1']['chunks']}`-chunk source corpus.
- Scaling and concurrency campaigns require the separate benchmark command and are not inferred from this run.
- No LLM generation or tool side effect is included in retrieval-only latency.

## Scaling and concurrency

```json
{json.dumps(report.get('scaling_and_concurrency', []), indent=2)}
```

Cache: `{report.get('cache', {})}`.

## Final verdict

**{verdict}**
"""


async def evaluate(store: ChromaVectorStore, retriever: ProgressiveRetriever, queries: list[dict[str, Any]], *, document_id: str, repeats: int = 1) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for query in queries:
        for repeat in range(repeats):
            started = time.perf_counter()
            context_fingerprint = hashlib.sha256(json.dumps(query.get("context", [])[-2:], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            cache_key = (hashlib.sha256(f"{query['query'].strip().casefold()}|{context_fingerprint}".encode()).hexdigest(), document_id)
            cache_hit = cache_key in retriever._cache
            result = await retriever.search(query["query"], document_id=document_id, conversation=query.get("context", []), debug=True)
            elapsed = (time.perf_counter() - started) * 1000
            first_rank = next((index for index, hit in enumerate(result.hits, 1) if hit_matches(hit, query["gold_sections"])), None)
            context, source_map = build_knowledge_context(result)
            provenance_ok = all(source_map[label].chunk_id == result.hits[index - 1].chunk_id and source_map[label].metadata.get("content_hash") == hashlib.sha256(result.hits[index - 1].content.encode()).hexdigest() and source_map[label].metadata.get("document_id") == document_id for label, index in ((f"S{i}", i) for i in range(1, len(result.hits) + 1)))
            timings = result.debug.get("timings", {})
            results.append({"id": query["id"], "category": query["category"], "query": query["query"], "repeat": repeat, "cache_hit": cache_hit, "gold_sections": query["gold_sections"], "should_have_evidence": query["should_have_evidence"], "level_reached": result.level_reached, "rewrite_used": result.rewrite_used, "evidence_state": result.evidence_state, "top1_match": bool(first_rank == 1), "top3_match": bool(first_rank and first_rank <= 3), "first_match_rank": first_rank, "citation_provenance": provenance_ok, "source_map": {key: value.__dict__ for key, value in source_map.items()}, "ranked_chunks": [hit.chunk_id for hit in result.hits], "lexical_ranked_chunks": [hit.chunk_id for hit in result.debug.get("lexical", [])], "semantic_ranked_chunks": [hit.chunk_id for hit in result.debug.get("semantic", [])], "raw_semantic_scores": [hit.score for hit in result.debug.get("raw_semantic", [])], "semantic_scores": [hit.score for hit in result.debug.get("semantic", [])], "timings": {**timings, "total_retrieval_ms": elapsed}})
    return results


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--concurrency", type=int, default=1)
    args = parser.parse_args()
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    queries = json.loads((FIXTURES / "golden_queries.json").read_text())
    os.environ.setdefault("CHROMA_RAG_COLLECTION", f"rag_eval_{os.getpid()}")
    embeddings = E5EmbeddingProvider("intfloat/multilingual-e5-small")
    store = ChromaVectorStore()
    retriever = ProgressiveRetriever(store, embedding_provider=embeddings)
    ingestion = RagIngestionService(store, embeddings, retriever)
    cold_started = time.perf_counter()
    v1 = await ingestion.replace((FIXTURES / "corpus_v1.md").read_bytes(), "corpus_v1.md", document_version=1)
    cold_ms = (time.perf_counter() - cold_started) * 1000
    v1_results = await evaluate(store, retriever, queries, document_id=v1["document_id"], repeats=args.repeats)
    warm_started = time.perf_counter()
    v2 = await ingestion.replace((FIXTURES / "corpus_v2.md").read_bytes(), "corpus_v2.md", document_version=2)
    replacement_ms = (time.perf_counter() - warm_started) * 1000
    version_query = "si cancelo diez horas antes cuanto pierdo"
    v2_result = await retriever.search(version_query, document_id=v2["document_id"], debug=True)
    v2_leakage = any("30%" in hit.content for hit in v2_result.hits)
    quality_results = [item for item in v1_results if item["repeat"] == 0]
    by_level = Counter("NO_EVIDENCE" if item["evidence_state"] == "INSUFFICIENT" else f"L{item['level_reached']}" for item in quality_results)
    latency = stats([item["timings"]["total_retrieval_ms"] for item in v1_results])
    identifier_results = [item for item in quality_results if re.search(r"\b[A-Z]{2,}[\w-]*\d[\w-]*\b", item["query"].upper())]
    provenance_accuracy = sum(item["citation_provenance"] for item in quality_results) / max(1, len(quality_results))
    report = {"seed": 42, "corpus": {"v1": v1, "v2": v2, "embedding_model": embeddings.model_name, "dimensions": embeddings.dimensions}, "cold_ingestion_ms": cold_ms, "replacement_ms": replacement_ms, "version_check": {"query": version_query, "v2_document_id": v2["document_id"], "old_version_leakage": v2_leakage, "active_document_id": await store.get_active_document(), "result_chunks": [hit.chunk_id for hit in v2_result.hits]}, "quality": metrics(quality_results), "exact_identifier_recall_at_1": sum(item["top1_match"] for item in identifier_results) / max(1, len(identifier_results)), "exact_identifier_queries": len(identifier_results), "citation_provenance_accuracy": provenance_accuracy, "quality_supported_only": {"recall_at_3": sum(item["top3_match"] for item in quality_results if item["should_have_evidence"]) / max(1, sum(item["should_have_evidence"] for item in quality_results))}, "levels": dict(by_level), "latency_ms": latency, "results_count": len(v1_results), "unique_query_count": len(quality_results), "concurrency_requested": args.concurrency, "concurrency_note": "Quality metrics use first-pass results; latency includes all repeats for cache and warm measurements."}
    scaling_path = ARTIFACTS / "scaling.json"
    if scaling_path.exists():
        report["scaling_and_concurrency"] = json.loads(scaling_path.read_text())
    report["cache"] = {"total": len(v1_results), "hits": sum(item["cache_hit"] for item in v1_results), "hit_rate": sum(item["cache_hit"] for item in v1_results) / max(1, len(v1_results))}
    (ARTIFACTS / "results.json").write_text(json.dumps({"report": report, "results": v1_results}, ensure_ascii=False, indent=2))
    (ARTIFACTS / "latency.json").write_text(json.dumps({"cold_ingestion_ms": cold_ms, "replacement_ms": replacement_ms, "retrieval": latency}, indent=2))
    failures = [item for item in quality_results if not item["top3_match"] and item["should_have_evidence"]]
    (ARTIFACTS / "failures.json").write_text(json.dumps(failures, ensure_ascii=False, indent=2))
    (ARTIFACTS / "report.md").write_text(markdown_report(report, failures))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
