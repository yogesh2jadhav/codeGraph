"""Hybrid retrieval: lexical + vector + symbol + graph expansion, then rerank.

    query
      -> lexical search      (token overlap over chunk text / node ids)
      -> vector search       (embedding cosine, top N)
      -> symbol lookup       (exact / substring FQN or name match)
      -> graph expansion     (callers + callees of matched methods, 1-2 hops)
      |
      v  reciprocal-rank fusion -> candidate set
      v  reranker
      v  RetrievedItem list (chunk, node, score, sources[], file:line)

Priority ordering follows PLAN.md section 38: exact target, direct callers,
direct callees, then broader semantic matches. That ordering is applied as a
score bonus per source so the reranker still has the final say.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from code_memory.graph.repository import GraphRepository
from code_memory.logging_setup import get_logger
from code_memory.reranking import Reranker
from code_memory.embeddings import EmbeddingProvider
from code_memory.vector.chunker import Chunk
from code_memory.vector.store import VectorStore

log = get_logger("retrieval")
_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")

_SOURCE_BONUS = {
    "symbol": 0.30, "vector": 0.0, "lexical": 0.05,
    "graph-caller": 0.12, "graph-callee": 0.10,
}


@dataclass
class RetrievedItem:
    node_id: str
    score: float
    sources: list[str] = field(default_factory=list)
    text: str = ""
    kind: str = ""
    file: str | None = None
    line: int | None = None
    fqn: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"node_id": self.node_id, "score": round(self.score, 4),
                "sources": self.sources, "kind": self.kind,
                "file": self.file, "line": self.line, "fqn": self.fqn,
                "preview": self.text[:200]}


def _tokens(text: str) -> set[str]:
    return {t.lower() for t in _TOKEN_RE.findall(text or "")}


class HybridRetriever:
    def __init__(self, *, graph: GraphRepository, store: VectorStore,
                 embedder: EmbeddingProvider, reranker: Reranker,
                 chunks: list[Chunk] | None = None):
        self.graph = graph
        self.store = store
        self.embedder = embedder
        self.reranker = reranker
        # chunk lookup by node id, for graph-expansion candidates
        self._chunk_by_node = {c.node_id: c for c in (chunks or [])}

    def retrieve(self, query: str, *, top_k: int = 10,
                 vector_k: int = 30, kind: str | None = None) -> list[RetrievedItem]:
        pools: dict[str, list[tuple[str, float]]] = {}

        # -- vector --------------------------------------------------
        try:
            qvec = self.embedder.embed_one(query)
            vhits = self.store.search(qvec, top_k=vector_k, kind=kind)
            pools["vector"] = [(h.chunk.node_id, h.score) for h in vhits]
            for h in vhits:
                self._chunk_by_node.setdefault(h.chunk.node_id, h.chunk)
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("vector search failed", extra={"error": str(exc)})
            pools["vector"] = []

        # -- lexical ------------------------------------------------
        qtok = _tokens(query)
        lex: list[tuple[str, float]] = []
        for node_id, chunk in self._chunk_by_node.items():
            ov = len(qtok & _tokens(chunk.text + " " + node_id))
            if ov:
                lex.append((node_id, ov / (len(qtok) or 1)))
        lex.sort(key=lambda x: x[1], reverse=True)
        pools["lexical"] = lex[:vector_k]

        # -- symbol -----------------------------------------------
        pools["symbol"] = [(n["id"], 1.0)
                           for term in _identifier_terms(query)
                           for n in self.graph.find_nodes(name_contains=term)][:20]

        # -- graph expansion -----------------------------------
        seed_methods = {nid for nid, _ in
                        pools["vector"][:5] + pools["symbol"][:5]
                        if nid.startswith("method:")}
        callers, callees = [], []
        for mid in seed_methods:
            callers += [(c["id"], 1.0) for c in self.graph.find_callers(mid)]
            callees += [(c["id"], 1.0) for c in self.graph.find_callees(mid)]
        pools["graph-caller"] = callers[:20]
        pools["graph-callee"] = callees[:20]

        fused = self._fuse(pools)
        items = [self._to_item(nid, score, srcs)
                 for nid, (score, srcs) in fused.items()]
        items = [it for it in items if it is not None]
        ranked = self.reranker.rerank(query, items, top_k=max(top_k, 1))
        return ranked[:top_k]

    # -- fusion ------------------------------------------------
    def _fuse(self, pools: dict[str, list[tuple[str, float]]]):
        scores: dict[str, tuple[float, list[str]]] = {}
        for source, ranked in pools.items():
            for rank, (node_id, _raw) in enumerate(ranked):
                rr = 1.0 / (rank + 10)               # reciprocal-rank fusion
                bonus = _SOURCE_BONUS.get(source, 0.0)
                prev_score, prev_src = scores.get(node_id, (0.0, []))
                scores[node_id] = (prev_score + rr + bonus,
                                   prev_src + [source])
        return scores

    def _to_item(self, node_id: str, score: float,
                 sources: list[str]) -> RetrievedItem | None:
        node = self.graph.get_node(node_id)
        chunk = self._chunk_by_node.get(node_id)
        if node is None and chunk is None:
            return None
        loc = (node or {}).get("location") or (chunk.metadata if chunk else {})
        return RetrievedItem(
            node_id=node_id, score=score, sources=sorted(set(sources)),
            text=(chunk.text if chunk else (node or {}).get("name", "")),
            kind=(node or {}).get("kind") or (chunk.kind if chunk else ""),
            file=loc.get("file") or loc.get("relative_path"),
            line=loc.get("line_start"),
            fqn=(node or {}).get("fqn") or (chunk.metadata.get("fqn")
                                            if chunk else None),
        )


def _identifier_terms(query: str) -> list[str]:
    """Pull likely code identifiers (>=4 chars, or CamelCase) out of a query."""
    terms = []
    for t in _TOKEN_RE.findall(query):
        if len(t) >= 4 or re.search(r"[a-z][A-Z]", t):
            terms.append(t)
    return terms[:6]
