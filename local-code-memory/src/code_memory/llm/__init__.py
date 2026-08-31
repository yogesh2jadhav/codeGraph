"""Phase 12 - local LLM integration (PLAN.md §5, §39, §40).

The coding LLM is reached only through :class:`LLMProvider`. Default is
:class:`EchoProvider` (offline, deterministic) so the advisor and its tests work
with no server; :class:`OllamaProvider` talks to a local Ollama instance.
"""

from code_memory.llm.provider import (
    EchoProvider,
    LLMProvider,
    OllamaProvider,
    get_llm_provider,
)
from code_memory.llm.advisor import CodingAdvisor, Advice

__all__ = [
    "LLMProvider",
    "EchoProvider",
    "OllamaProvider",
    "get_llm_provider",
    "CodingAdvisor",
    "Advice",
]
