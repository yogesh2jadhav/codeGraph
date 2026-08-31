"""Build (or incrementally refresh) the vector index for a scanned repository."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from code_memory.embeddings import EmbeddingProvider
from code_memory.logging_setup import get_logger
from code_memory.models.code import ParsedFile
from code_memory.models.graph import CodeGraph
from code_memory.vector.chunker import build_chunks
from code_memory.vector.store import InMemoryVectorStore, VectorStore

log = get_logger("vector.index")


def _text_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def build_vector_index(graph: CodeGraph, parsed: list[ParsedFile], root: Path,
                       embedder: EmbeddingProvider, store: VectorStore,
                       *, batch: int = 128, incremental: bool = True) -> dict[str, Any]:
    chunks = build_chunks(graph, parsed, root)
    if not chunks:
        return {"chunks": 0, "embedding": embedder.name, "embedded": 0,
                "reused": 0, "pruned": 0}

    # Phase 15: reuse vectors for chunks whose text is unchanged.
    reuse: dict[str, list[float]] = {}
    pruned = 0
    if (incremental and isinstance(store, InMemoryVectorStore)
            and store.embedding_name in ("", embedder.name)):
        reuse = store.vectors_by_text_hash()
        pruned = store.prune_to({c.id for c in chunks})

    embedded = reused = 0
    pending: list = []
    pending_texts: list[str] = []

    def flush():
        nonlocal embedded
        if not pending:
            return
        vectors = embedder.embed(pending_texts)
        store.upsert(pending, vectors)
        embedded += len(pending)
        pending.clear()
        pending_texts.clear()

    for c in chunks:
        cached = reuse.get(_text_hash(c.text))
        if cached is not None:
            store.upsert([c], [cached])
            reused += 1
        else:
            pending.append(c)
            pending_texts.append(c.text)
            if len(pending) >= batch:
                flush()
    flush()

    if isinstance(store, InMemoryVectorStore):
        store.embedding_name = embedder.name
        store.save()

    stats = {"chunks": len(chunks), "embedding": embedder.name,
             "embedded": embedded, "reused": reused, "pruned": pruned,
             "dim": embedder.dim}
    log.info("vector index built", extra=stats)
    return stats
