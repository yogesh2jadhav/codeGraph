"""Build (or refresh) the vector index for a scanned repository."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from code_memory.embeddings import EmbeddingProvider
from code_memory.logging_setup import get_logger
from code_memory.models.code import ParsedFile
from code_memory.models.graph import CodeGraph
from code_memory.vector.chunker import build_chunks
from code_memory.vector.store import InMemoryVectorStore, VectorStore

log = get_logger("vector.index")


def build_vector_index(graph: CodeGraph, parsed: list[ParsedFile], root: Path,
                       embedder: EmbeddingProvider, store: VectorStore,
                       *, batch: int = 128) -> dict[str, Any]:
    chunks = build_chunks(graph, parsed, root)
    if not chunks:
        return {"chunks": 0, "embedding": embedder.name}

    for i in range(0, len(chunks), batch):
        group = chunks[i:i + batch]
        vectors = embedder.embed([c.text for c in group])
        store.upsert(group, vectors)

    if isinstance(store, InMemoryVectorStore):
        store.embedding_name = embedder.name
        store.save()

    log.info("vector index built", extra={"chunks": len(chunks),
                                          "embedding": embedder.name})
    return {"chunks": len(chunks), "embedding": embedder.name,
            "dim": embedder.dim}
