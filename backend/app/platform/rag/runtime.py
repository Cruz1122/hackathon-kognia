from __future__ import annotations

from .chroma_store import ChromaVectorStore
from .embeddings import E5EmbeddingProvider
from .retrieval import ProgressiveRetriever

store = ChromaVectorStore()
embeddings = E5EmbeddingProvider()
retriever = ProgressiveRetriever(store, embedding_provider=embeddings)
