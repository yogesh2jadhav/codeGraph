"""Local embedding providers (PLAN.md section 6).

The coding LLM is never used for embeddings. Providers:

  * :class:`HashingEmbeddingProvider` - default, deterministic, zero dependency.
    A hashed character/word n-gram bag projected to a fixed dimension and L2
    normalised. Not semantically strong, but makes retrieval work with no
    setup and keeps tests hermetic.
  * :class:`OllamaEmbeddingProvider` - optional, calls a local Ollama embed
    model (e.g. ``nomic-embed-text``). No Python ML dependency.
  * :class:`SentenceTransformerEmbeddingProvider` - optional, needs
    ``sentence-transformers``.

``get_embedding_provider(config)`` selects one, falling back to hashing.
"""

from code_memory.embeddings.provider import (
    EmbeddingProvider,
    HashingEmbeddingProvider,
    get_embedding_provider,
)

__all__ = [
    "EmbeddingProvider",
    "HashingEmbeddingProvider",
    "get_embedding_provider",
]
