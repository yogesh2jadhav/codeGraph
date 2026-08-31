"""High-level read queries over an in-memory :class:`CodeGraph`.

An early, backend-agnostic version of the Graph Query API (PLAN.md section 37).
When Neo4j lands (Phase 7) these same function names move behind
``GraphRepository`` so callers never write Cypher.
"""

from __future__ import annotations

from code_memory.models.graph import CodeGraph, Edge


def _out(graph: CodeGraph, node_id: str, etype: str) -> list[Edge]:
    return [e for e in graph.edges if e.src == node_id and e.type == etype]


def _in(graph: CodeGraph, node_id: str, etype: str) -> list[Edge]:
    return [e for e in graph.edges if e.dst == node_id and e.type == etype]


def find_callees(graph: CodeGraph, method_id: str) -> list[dict]:
    """Methods that ``method_id`` calls directly."""
    return [{"id": e.dst, "confidence": e.confidence.value,
             "line": e.evidence.get("line_start"), "call": e.evidence.get("call")}
            for e in _out(graph, method_id, "CALLS")]


def find_callers(graph: CodeGraph, method_id: str) -> list[dict]:
    """Methods that call ``method_id`` directly."""
    return [{"id": e.src, "confidence": e.confidence.value,
             "line": e.evidence.get("line_start")}
            for e in _in(graph, method_id, "CALLS")]


def find_implementations(graph: CodeGraph, type_id: str) -> list[str]:
    """Types that extend or implement ``type_id``."""
    return sorted({e.src for e in graph.edges
                   if e.dst == type_id and e.type in ("EXTENDS", "IMPLEMENTS")})


def find_overrides(graph: CodeGraph, method_id: str) -> list[str]:
    return sorted({e.src for e in graph.edges
                   if e.dst == method_id and e.type == "OVERRIDES"})


def call_paths(graph: CodeGraph, src_id: str, dst_id: str,
               max_depth: int = 6) -> list[list[str]]:
    """All simple CALLS paths from src to dst up to ``max_depth`` hops."""
    adjacency: dict[str, list[str]] = {}
    for e in graph.edges:
        if e.type == "CALLS":
            adjacency.setdefault(e.src, []).append(e.dst)

    paths: list[list[str]] = []

    def dfs(node: str, path: list[str]) -> None:
        if node == dst_id and len(path) > 1:
            paths.append(list(path))
            return
        if len(path) > max_depth:
            return
        for nxt in adjacency.get(node, []):
            if nxt not in path:            # simple path - no cycles
                path.append(nxt)
                dfs(nxt, path)
                path.pop()

    dfs(src_id, [src_id])
    return paths


def transitive_callers(graph: CodeGraph, method_id: str,
                       max_depth: int = 5) -> set[str]:
    """Every method that can reach ``method_id`` through CALLS edges."""
    reverse: dict[str, list[str]] = {}
    for e in graph.edges:
        if e.type == "CALLS":
            reverse.setdefault(e.dst, []).append(e.src)

    seen: set[str] = set()
    frontier = [(method_id, 0)]
    while frontier:
        node, depth = frontier.pop()
        if depth >= max_depth:
            continue
        for caller in reverse.get(node, []):
            if caller not in seen:
                seen.add(caller)
                frontier.append((caller, depth + 1))
    return seen
