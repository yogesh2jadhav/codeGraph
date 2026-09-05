"""Backend-agnostic graph repository (PLAN.md sections 4.4 and 37).

The rest of the application talks to :class:`GraphRepository`, never to Neo4j or
to a raw :class:`CodeGraph`. Two implementations ship:

  * :class:`InMemoryGraphRepository` - default, zero dependencies, backed by the
    ``graph/nodes.json`` + ``graph/edges.json`` the scanner already writes.
  * :class:`Neo4jGraphRepository` - optional (needs the ``neo4j`` driver and a
    running server); selected with ``graph.provider: neo4j``.

``get_graph_repository(config)`` picks one, falling back to in-memory if Neo4j
is configured but unreachable / the driver is missing.
"""

from __future__ import annotations

import abc
from typing import Any

from code_memory.config import Config
from code_memory.logging_setup import get_logger
from code_memory.models.graph import CodeGraph

log = get_logger("graph.repository")

# Method names that strongly suggest "this is a standalone entrypoint" (a
# main(), a Runnable/Callable-style entry, a batch/ETL job driver) - used by
# find_entrypoints() implementations to rank results, not to filter them (a
# caller-less method with real callees is reported either way).
ENTRY_NAME_HINTS = {
    "main", "run", "execute", "process", "start", "call", "launch",
    "doWork", "doExecute", "runJob", "runPipeline",
}


class GraphRepository(abc.ABC):
    # -- lifecycle ----------------------------------------------------
    @abc.abstractmethod
    def replace_graph(self, graph: CodeGraph) -> None:
        """Persist ``graph`` as the current code memory (replacing any prior)."""

    @abc.abstractmethod
    def clear(self) -> None: ...

    @abc.abstractmethod
    def stats(self) -> dict[str, Any]: ...

    # -- reads ------------------------------------------------------
    @abc.abstractmethod
    def get_node(self, node_id: str) -> dict[str, Any] | None: ...

    @abc.abstractmethod
    def find_nodes(self, *, kind: str | None = None,
                   name_contains: str | None = None,
                   fqn: str | None = None) -> list[dict[str, Any]]: ...

    @abc.abstractmethod
    def neighbors(self, node_id: str, *, edge_types: tuple[str, ...] = (),
                  direction: str = "out") -> list[dict[str, Any]]: ...

    @abc.abstractmethod
    def find_callers(self, method_id: str) -> list[dict[str, Any]]: ...

    @abc.abstractmethod
    def find_callees(self, method_id: str) -> list[dict[str, Any]]: ...

    @abc.abstractmethod
    def find_implementations(self, type_id: str) -> list[str]: ...

    @abc.abstractmethod
    def find_impact(self, node_id: str, max_depth: int = 4) -> dict[str, Any]: ...

    @abc.abstractmethod
    def find_endpoint_flow(self, endpoint_id: str) -> dict[str, Any]: ...

    @abc.abstractmethod
    def find_call_flow(self, method_id: str, max_depth: int = 8) -> list[dict[str, Any]]:
        """Ordered, breadth-first CALLS chain starting at ``method_id`` - the
        generic form of ``find_endpoint_flow`` for any method, not just a
        Spring handler. Each item is {id, depth, confidence}."""

    @abc.abstractmethod
    def find_entrypoints(self) -> list[dict[str, Any]]:
        """Methods that look like a standalone execution entrypoint: nothing
        in the scan calls them, they call at least one other in-scan method,
        and they are not already reported as a Spring endpoint or Spark job
        (those get their own dedicated flow views)."""

    @abc.abstractmethod
    def find_database_usage(self, table: str) -> dict[str, Any]: ...


def get_graph_repository(config: Config) -> GraphRepository:
    provider = str(config.get("graph.provider", "memory")).lower()
    out_dir = config.output_dir / "graph"

    if provider == "neo4j":
        try:
            from code_memory.graph.neo4j_repository import Neo4jGraphRepository

            repo = Neo4jGraphRepository(
                uri=config.get("graph.uri", "bolt://localhost:7687"),
                user=config.get("graph.username", "neo4j"),
                password=config.get("graph.password", "neo4j"),
                database=config.get("graph.database", "neo4j"),
            )
            repo.ping()
            return repo
        except Exception as exc:
            log.warning("neo4j unavailable, falling back to in-memory graph",
                        extra={"error": str(exc)})

    from code_memory.graph.memory_repository import InMemoryGraphRepository

    return InMemoryGraphRepository(nodes_path=out_dir / "nodes.json",
                                   edges_path=out_dir / "edges.json")
