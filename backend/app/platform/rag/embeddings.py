from __future__ import annotations

import hashlib
import math
import re
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

    def _encode(self, texts: list[str], prefix: str) -> list[list[float]]:
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
                self._model = SentenceTransformer(self.model_name)
            except ImportError:
                self._model = False
        if self._model not in (None, False):
            values = self._model.encode([f"{prefix}: {text}" for text in texts], normalize_embeddings=True)
            return [list(map(float, value)) for value in values]
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
        return self._encode(texts, "query")

    def embed_passages(self, texts: list[str]) -> list[list[float]]:
        return self._encode(texts, "passage")
