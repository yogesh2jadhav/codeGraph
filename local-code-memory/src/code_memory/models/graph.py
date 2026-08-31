"""A backend-agnostic in-memory code graph.

Phase 2 emits this as ``graph/nodes.json`` + ``graph/edges.json``. Phase 7 will
load the same structure into Neo4j behind ``GraphRepository``. Keeping it plain
here means the graph can be inspected and tested without any database.

Every edge records *evidence* (source file + line span) and a *confidence*
bucket so consumers never mistake an inferred relationship for a proven one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable


class Confidence(str, Enum):
    HIGH = "HIGH"          # taken verbatim from the AST
    MEDIUM = "MEDIUM"      # resolved with a local heuristic
    LOW = "LOW"            # guessed
    UNKNOWN = "UNKNOWN"


@dataclass
class Node:
    id: str                       # stable, e.g. "type:com.example.Foo"
    kind: str                     # Class | Interface | Method | Field | Package ...
    name: str
    properties: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "kind": self.kind, "name": self.name,
                **self.properties}


@dataclass
class Edge:
    type: str                     # CONTAINS | DECLARES | EXTENDS | IMPLEMENTS ...
    src: str
    dst: str
    confidence: Confidence = Confidence.HIGH
    evidence: dict[str, Any] = field(default_factory=dict)

    def key(self) -> tuple[str, str, str]:
        return (self.type, self.src, self.dst)

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, "src": self.src, "dst": self.dst,
                "confidence": self.confidence.value, "evidence": self.evidence}


class CodeGraph:
    def __init__(self) -> None:
        self._nodes: dict[str, Node] = {}
        self._edges: dict[tuple[str, str, str], Edge] = {}

    # -- mutation -----------------------------------------------------
    def add_node(self, node: Node) -> Node:
        existing = self._nodes.get(node.id)
        if existing is None:
            self._nodes[node.id] = node
            return node

        incoming_is_real = not node.properties.get("placeholder")
        existing_is_placeholder = existing.properties.get("placeholder")
        if incoming_is_real and existing_is_placeholder:
            # A real declaration arrived after something referenced it by id
            # (e.g. an import seen in another file first). Upgrade in place so
            # edge endpoints stay valid.
            existing.kind = node.kind
            existing.name = node.name
            existing.properties = dict(node.properties)
            return existing

        # Otherwise merge: first non-empty wins for scalars.
        for key, value in node.properties.items():
            existing.properties.setdefault(key, value)
        return existing

    def add_edge(self, edge: Edge) -> None:
        self._edges.setdefault(edge.key(), edge)

    def ensure_placeholder(self, node_id: str, kind: str, name: str) -> None:
        """Register a referenced-but-not-yet-declared node (e.g. a superclass
        in another module). Marked so reports can list unresolved symbols."""
        if node_id not in self._nodes:
            self._nodes[node_id] = Node(node_id, kind, name,
                                        {"resolved": False, "placeholder": True})

    # -- access -----------------------------------------------------
    @property
    def nodes(self) -> list[Node]:
        return list(self._nodes.values())

    @property
    def edges(self) -> list[Edge]:
        return list(self._edges.values())

    def unresolved(self) -> list[Node]:
        return [n for n in self._nodes.values()
                if n.properties.get("placeholder")]

    def counts(self) -> dict[str, Any]:
        by_node_kind: dict[str, int] = {}
        by_edge_type: dict[str, int] = {}
        for n in self._nodes.values():
            by_node_kind[n.kind] = by_node_kind.get(n.kind, 0) + 1
        for e in self._edges.values():
            by_edge_type[e.type] = by_edge_type.get(e.type, 0) + 1
        return {
            "node_count": len(self._nodes),
            "edge_count": len(self._edges),
            "unresolved_count": len(self.unresolved()),
            "nodes_by_kind": dict(sorted(by_node_kind.items())),
            "edges_by_type": dict(sorted(by_edge_type.items())),
        }

    def extend(self, nodes: Iterable[Node], edges: Iterable[Edge]) -> None:
        for n in nodes:
            self.add_node(n)
        for e in edges:
            self.add_edge(e)

    # -- serialisation --------------------------------------------
    def nodes_json(self) -> list[dict[str, Any]]:
        return [n.to_dict() for n in sorted(self._nodes.values(), key=lambda n: n.id)]

    def edges_json(self) -> list[dict[str, Any]]:
        return [e.to_dict() for e in sorted(self._edges.values(), key=lambda e: e.key())]
