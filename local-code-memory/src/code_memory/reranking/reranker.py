"""Reranker implementations.

  * :class:`NoopReranker`            - keep the fused order.
  * :class:`LexicalOverlapReranker` - cheap, dependency-free: blend the fused
    score with query/candidate token-overlap (Jaccard). Default.
  * :class:`CrossEncoderReranker`   - optional, needs ``sentence-transformers``.
"""

from __future__ import annotations

import abc
import re
from typing import Sequence

from code_memory.config import Config
from code_memory.logging_setup import get_logger

log = get_logger("reranking")
_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")


def _tokens(text: str) -> set[str]:
    out: set[str] = set()
    for t in _TOKEN_RE.findall(text.lower()):
        out.add(t)
        out.update(p for p in re.split(r"[_]+",
                                       re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", t))
                   if len(p) > 2)
    return out


class Reranker(abc.ABC):
    name = "abstract"

    @abc.abstractmethod
    def rerank(self, query: str, candidates: Sequence["object"],
               top_k: int) -> list["object"]:
        ...


class NoopReranker(Reranker):
    name = "noop"

    def rerank(self, query, candidates, top_k):
        return list(candidates)[:top_k]


class LexicalOverlapReranker(Reranker):
    name = "lexical-overlap"

    def __init__(self, weight: float = 0.35):
        self.weight = weight

    def rerank(self, query, candidates, top_k):
        q = _tokens(query)
        if not q:
            return list(candidates)[:top_k]
        rescored = []
        for c in candidates:
            ct = _tokens(getattr(c, "text", "") + " " + getattr(c, "node_id", ""))
            overlap = len(q & ct) / len(q | ct) if ct else 0.0
            base = getattr(c, "score", 0.0)
            c.score = (1 - self.weight) * base + self.weight * overlap
            rescored.append(c)
        rescored.sort(key=lambda x: x.score, reverse=True)
        return rescored[:top_k]


class CrossEncoderReranker(Reranker):  # pragma: no cover - optional
    name = "cross-encoder"

    def __init__(self, model: str):
        from sentence_transformers import CrossEncoder

        self._model = CrossEncoder(model)

    def rerank(self, query, candidates, top_k):
        if not candidates:
            return []
        scores = self._model.predict(
            [(query, getattr(c, "text", "")) for c in candidates])
        for c, s in zip(candidates, scores):
            c.score = float(s)
        ranked = sorted(candidates, key=lambda x: x.score, reverse=True)
        return ranked[:top_k]


def get_reranker(config: Config) -> Reranker:
    kind = str(config.get("retrieval.reranker", "lexical")).lower()
    if kind in ("none", "noop", "off"):
        return NoopReranker()
    if kind in ("cross-encoder", "cross_encoder"):
        try:
            return CrossEncoderReranker(
                config.get("retrieval.reranker_model",
                           "cross-encoder/ms-marco-MiniLM-L-6-v2"))
        except Exception as exc:
            log.warning("cross-encoder unavailable, using lexical reranker",
                        extra={"error": str(exc)})
    return LexicalOverlapReranker()
