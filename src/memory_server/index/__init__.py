"""색인 (SPEC 3.5): SQLite + sqlite-vec 캐시, 임베딩, 색인기."""

from .db import IndexDB, MemoryRow, open_db
from .embedder import Embedder, EmbeddingUnavailable, FakeEmbedder, SentenceTransformerEmbedder, make_embedder
from .indexer import IndexResult, Indexer, embedding_text

__all__ = [
    "Embedder",
    "EmbeddingUnavailable",
    "FakeEmbedder",
    "IndexDB",
    "IndexResult",
    "Indexer",
    "MemoryRow",
    "SentenceTransformerEmbedder",
    "embedding_text",
    "make_embedder",
    "open_db",
]
