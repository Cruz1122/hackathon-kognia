from __future__ import annotations

import hashlib
import math
import os
import re
from pathlib import Path
from typing import Protocol


class EmbeddingProvider(Protocol):
    dimensions: int
    def embed_queries(self, texts: list[str]) -> list[list[float]]: ...
    def embed_passages(self, texts: list[str]) -> list[list[float]]: ...


class E5EmbeddingProvider:
    dimensions = 384

    def __init__(self, model_name: str = "intfloat/multilingual-e5-small") -> None:
        self.model_name = model_name
        self._model = None

    def preload(self) -> None:
        """Load torch + E5 weights before the first user turn hits RAG."""
        self.embed_queries(["ok"])

    def _encode(self, texts: list[str], prefix: str) -> list[list[float]]:
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            default_path = Path(__file__).resolve().parents[3] / "models" / "multilingual-e5-small"
            configured_path = os.getenv("E5_MODEL_DIR")
            model_source = Path(configured_path) if configured_path else default_path
            is_local_model = model_source.is_dir()
            self._model = SentenceTransformer(
                str(model_source) if is_local_model else self.model_name,
                device="cpu",
                local_files_only=is_local_model,
            )
        values = self._model.encode(
            [f"{prefix}: {text}" for text in texts],
            normalize_embeddings=True,
            convert_to_numpy=True,
        )
        return [list(map(float, value)) for value in values]

    def embed_queries(self, texts: list[str]) -> list[list[float]]:
        return self._encode(texts, "query")

    def embed_passages(self, texts: list[str]) -> list[list[float]]:
        return self._encode(texts, "passage")


class HashEmbeddingProvider:
    """Small deterministic fake for fast unit tests without model weights."""

    dimensions = 384

    def __init__(self, model_name: str = "test-hash-embedding") -> None:
        self.model_name = model_name

    def preload(self) -> None:
        return None

    def _encode(self, texts: list[str]) -> list[list[float]]:
        result = []
        for text in texts:
            vector = [0.0] * self.dimensions
            normalized = re.sub(r"\s+", " ", text.casefold().strip())
            features = re.findall(r"\w+", normalized)
            features.extend(normalized[index : index + 3] for index in range(max(0, len(normalized) - 2)))
            for token in features:
                index = int(hashlib.sha256(token.encode()).hexdigest(), 16) % self.dimensions
                vector[index] += 1.0
            norm = math.sqrt(sum(value * value for value in vector)) or 1.0
            result.append([value / norm for value in vector])
        return result

    def embed_queries(self, texts: list[str]) -> list[list[float]]:
        return self._encode([f"query: {text}" for text in texts])

    def embed_passages(self, texts: list[str]) -> list[list[float]]:
        return self._encode([f"passage: {text}" for text in texts])
