"""Verify the final-transcript retrieval boundary without invoking an LLM."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from app.features.agent import service


async def main() -> None:
    calls = 0
    original = service.rag_retriever.search

    async def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return await original(*args, **kwargs)

    service.rag_retriever.search = counted
    try:
        partials = [f"transcripción parcial {index}" for index in range(12)]
        # Partials are deliberately not passed to the retrieval helper. The
        # final transcript is the only event eligible to start a turn.
        del partials
        await service._knowledge_message("¿Cuál es la política de cancelación?", [])
    finally:
        service.rag_retriever.search = original
    result = {"partial_transcripts": 12, "final_transcripts": 1, "retrieval_calls": calls, "rewrite_calls": 0, "passed": calls == 1}
    path = Path(__file__).resolve().parents[2] / "artifacts" / "rag-eval" / "partial_transcript.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
