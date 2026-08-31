"""Vector store abstraction + a dependency-free in-memory implementation.

``InMemoryVectorStore`` persists to a single JSON file (vectors as float lists)
so ``code-memory search`` can reload it without re-embedding. ``QdrantVectorStore``
is optional and selected with ``vector.provider: qdrant``.
"""

from __future__ import annotations

import abc
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from code_memory.config import Config
from code_memory.logging_setup import get_logger
from code_memory.vector.chunker import Chunk

log = get_logger("vector.store")


@dataclass
class VectorHit:
    chunk: Chunk
    score: float


class VectorStore(abc.ABC):
    @abc.abstractmethod
    def upsert(self, chunks: Sequence[Chunk], vectors: Sequence[Sequence[float]]) -> None: ...

    @abc.abstractmethod
    def search(self, vector: Sequence[float], top_k: int = 20,
               kind: str | None = None) -> list[VectorHit]: ...

    @abc.abstractmethod
    def count(self) -> int: ...


class InMemoryVectorStore(VectorStore):
    def __init__(self, path: Path | None = None, embedding_name: str = ""):
        self.path = path
        self.embedding_name = embedding_name
        self._chunks: list[Chunk] = []
        self._vectors: list[list[float]] = []
        if path and path.is_file():
            self._load()

    def upsert(self, chunks, vectors):
        by_id = {c.id: i for i, c in enumerate(self._chunks)}
        for chunk, vec in zip(chunks, vectors):
            vec = list(map(float, vec))
            if chunk.id in by_id:
                idx = by_id[chunk.id]
                self._chunks[idx] = chunk
                self._vectors[idx] = vec
            else:
                self._chunks.append(chunk)
                self._vectors.append(vec)

    def search(self, vector, top_k=20, kind=None):
        q = list(map(float, vector))
        qn = math.sqrt(sum(v * v for v in q)) or 1.0
        scored: list[VectorHit] = []
        for chunk, vec in zip(self._chunks, self._vectors):
            if kind and chunk.kind != kind:
                continue
            dot = sum(a * b for a, b in zip(q, vec))
            vn = math.sqrt(sum(v * v for v in vec)) or 1.0
            scored.append(VectorHit(chunk, dot / (qn * vn)))
        scored.sort(key=lambda h: h.score, reverse=True)
        return scored[:top_k]

    def count(self) -> int:
        return len(self._chunks)

    # -- persistence ---------------------------------------------
    def save(self) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({
            "embedding": self.embedding_name,
            "dim": len(self._vectors[0]) if self._vectors else 0,
            "items": [{"chunk": c.to_dict(), "vector": v}
                      for c, v in zip(self._chunks, self._vectors)],
        }), encoding="utf-8")

    def _load(self) -> None:
        data = json.loads(self.path.read_text())
        self.embedding_name = data.get("embedding", "")
        for item in data.get("items", []):
            self._chunks.append(Chunk.from_dict(item["chunk"]))
            self._vectors.append([float(x) for x in item["vector"]])


class QdrantVectorStore(VectorStore):  # pragma: no cover - optional path
    def __init__(self, url: str, collection: str, dim: int,
                 embedding_name: str = ""):
        from qdrant_client import QdrantClient
        from qdrant_client.models import Distance, VectorParams

        self._client = QdrantClient(url=url)
        self._collection = collection
        self.embedding_name = embedding_name
        if not self._client.collection_exists(collection):
            self._client.create_collection(
                collection,
                vectors_config=VectorParams(size=dim, distance=Distance.COSINE))

    def upsert(self, chunks, vectors):
        from qdrant_client.models import PointStruct

        points = [
            PointStruct(id=abs(hash(c.id)) % (10 ** 18), vector=list(map(float, v)),
                        payload=c.to_dict())
            for c, v in zip(chunks, vectors)
        ]
        self._client.upsert(self._collection, points)

    def search(self, vector, top_k=20, kind=None):
        flt = None
        if kind:
            from qdrant_client.models import (FieldCondition, Filter,
                                              MatchValue)
            flt = Filter(must=[FieldCondition(key="kind",
                                              match=MatchValue(value=kind))])
        res = self._client.search(self._collection, list(map(float, vector)),
                                  limit=top_k, query_filter=flt)
        return [VectorHit(Chunk.from_dict(p.payload), p.score) for p in res]

    def count(self):
        return self._client.count(self._collection).count


def get_vector_store(config: Config, *, dim: int = 0,
                     embedding_name: str = "") -> VectorStore:
    provider = str(config.get("vector.provider", "memory")).lower()
    index_path = config.output_dir / "vector" / "index.json"

    if provider == "qdrant":
        try:
            return QdrantVectorStore(
                config.get("vector.url", "http://localhost:6333"),
                config.get("vector.collection", "code_memory"),
                dim or 512, embedding_name)
        except Exception as exc:
            log.warning("qdrant unavailable, using in-memory vector store",
                        extra={"error": str(exc)})

    return InMemoryVectorStore(index_path, embedding_name)
