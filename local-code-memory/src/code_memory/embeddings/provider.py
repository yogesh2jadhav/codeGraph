"""Embedding provider implementations."""

from __future__ import annotations

import abc
import hashlib
import math
import re
import struct
from typing import Sequence

from code_memory.config import Config
from code_memory.logging_setup import get_logger

log = get_logger("embeddings")

_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")


class EmbeddingProvider(abc.ABC):
    name: str = "abstract"
    dim: int = 0

    @abc.abstractmethod
    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        ...

    def embed_one(self, text: str) -> list[float]:
        return self.embed([text])[0]


# ---------------------------------------------------------------------------
class HashingEmbeddingProvider(EmbeddingProvider):
    """Feature-hashing embedding: split into identifier tokens (also camelCase /
    snake_case sub-tokens) + char trigrams, hash each into ``dim`` buckets with a
    signed hash, then L2-normalise. Deterministic and dependency-free."""

    def __init__(self, dim: int = 512):
        self.dim = dim
        self.name = f"hashing-{dim}"

    def _features(self, text: str) -> list[str]:
        text = text.lower()
        feats: list[str] = []
        for tok in _TOKEN_RE.findall(text):
            feats.append(tok)
            for part in re.split(r"[_\d]+", _camel_split(tok)):
                if len(part) > 2:
                    feats.append(part)
        compact = re.sub(r"\s+", " ", text)
        feats += [compact[i:i + 3] for i in range(0, max(0, len(compact) - 2), 2)]
        return feats

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        out = []
        for text in texts:
            vec = [0.0] * self.dim
            for feat in self._features(text or ""):
                h = hashlib.blake2b(feat.encode(), digest_size=8).digest()
                idx = struct.unpack("<Q", h)[0]
                sign = 1.0 if (idx & 1) else -1.0
                vec[(idx >> 1) % self.dim] += sign
            norm = math.sqrt(sum(v * v for v in vec)) or 1.0
            out.append([v / norm for v in vec])
        return out


def _camel_split(tok: str) -> str:
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", tok)


# ---------------------------------------------------------------------------
class OllamaEmbeddingProvider(EmbeddingProvider):
    def __init__(self, base_url: str, model: str, dim_hint: int = 0):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.name = f"ollama:{model}"
        self.dim = dim_hint

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        import json
        import urllib.request

        vectors: list[list[float]] = []
        for text in texts:
            req = urllib.request.Request(
                f"{self.base_url}/api/embeddings",
                data=json.dumps({"model": self.model, "prompt": text}).encode(),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read())
            vec = data["embedding"]
            self.dim = len(vec)
            vectors.append(_l2(vec))
        return vectors


class SentenceTransformerEmbeddingProvider(EmbeddingProvider):
    def __init__(self, model: str):
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(model)
        self.name = f"st:{model}"
        self.dim = self._model.get_sentence_embedding_dimension()

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        vecs = self._model.encode(list(texts), normalize_embeddings=True)
        return [list(map(float, v)) for v in vecs]


def _l2(vec: Sequence[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


# ---------------------------------------------------------------------------
def get_embedding_provider(config: Config) -> EmbeddingProvider:
    provider = str(config.get("embedding.provider", "local")).lower()
    model = config.get("embedding.model", "configurable")

    try:
        if provider in ("ollama", "ollama-embed"):
            p = OllamaEmbeddingProvider(
                config.get("llm.base_url", "http://localhost:11434"),
                model if model != "configurable" else "nomic-embed-text",
            )
            p.embed(["ping"])  # fail fast if unreachable
            return p
        if provider in ("sentence-transformers", "st"):
            return SentenceTransformerEmbeddingProvider(
                model if model != "configurable" else "all-MiniLM-L6-v2")
    except Exception as exc:
        log.warning("embedding provider unavailable, using hashing fallback",
                    extra={"provider": provider, "error": str(exc)})

    return HashingEmbeddingProvider(int(config.get("embedding.dim", 512)))
