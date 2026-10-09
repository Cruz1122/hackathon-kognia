import math
from pathlib import Path

import pytest

from app.platform.rag.embeddings import E5EmbeddingProvider


def test_bundled_e5_model_produces_normalized_embeddings(monkeypatch: pytest.MonkeyPatch) -> None:
    model_dir = Path(__file__).resolve().parents[1] / "models" / "multilingual-e5-small"
    if not model_dir.is_dir():
        pytest.skip("run make setup to download the bundled E5 model")

    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")
    provider = E5EmbeddingProvider()
    vector = provider.embed_queries(["¿Qué cubre la póliza?"])[0]

    assert len(vector) == provider.dimensions == 384
    assert math.isclose(math.sqrt(sum(value * value for value in vector)), 1.0, rel_tol=1e-5)
