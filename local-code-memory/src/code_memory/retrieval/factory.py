"""Assemble a :class:`HybridRetriever` from config + persisted scan artifacts."""

from __future__ import annotations

import json

from code_memory.config import Config
from code_memory.embeddings import get_embedding_provider
from code_memory.graph.repository import get_graph_repository
from code_memory.reranking import get_reranker
from code_memory.retrieval.hybrid import HybridRetriever
from code_memory.vector.chunker import Chunk
from code_memory.vector.store import InMemoryVectorStore, get_vector_store


def build_retriever(config: Config) -> HybridRetriever:
    graph = get_graph_repository(config)
    embedder = get_embedding_provider(config)
    store = get_vector_store(config, dim=embedder.dim, embedding_name=embedder.name)

    chunks: list[Chunk] = []
    index_path = config.output_dir / "vector" / "index.json"
    if isinstance(store, InMemoryVectorStore) and index_path.is_file():
        data = json.loads(index_path.read_text(encoding="utf-8"))
        chunks = [Chunk.from_dict(item["chunk"]) for item in data.get("items", [])]

    return HybridRetriever(graph=graph, store=store, embedder=embedder,
                           reranker=get_reranker(config), chunks=chunks)
