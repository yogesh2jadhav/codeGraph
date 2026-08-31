"""Phase 10/11 - Markdown context generation.

Phase 10: :func:`generate_context_pack` writes the full ``.code-memory/context/``
set (``00``..``14``) plus ``manifest.json`` and ``reports/quality_report.md``,
all derived from the graph + metadata (never hand-maintained - PLAN.md §53).

Phase 11: :func:`generate_task_context` builds a compact, token-budgeted task
pack under ``.code-memory/tasks/<id>/`` using hybrid retrieval.
"""

from code_memory.context.generator import generate_context_pack
from code_memory.context.task import TaskPack, generate_task_context

__all__ = ["generate_context_pack", "generate_task_context", "TaskPack"]
