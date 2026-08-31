"""Vector index: chunking, storage and semantic search (PLAN.md section 4.5)."""

from code_memory.vector.chunker import Chunk, build_chunks
from code_memory.vector.store import (
    InMemoryVectorStore,
    VectorHit,
    VectorStore,
    get_vector_store,
)
from code_memory.vector.index import build_vector_index

__all__ = [
    "Chunk",
    "build_chunks",
    "VectorStore",
    "InMemoryVectorStore",
    "VectorHit",
    "get_vector_store",
    "build_vector_index",
]
