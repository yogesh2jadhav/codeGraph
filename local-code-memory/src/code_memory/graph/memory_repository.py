"""In-memory :class:`GraphRepository` backed by the scanner's JSON graph.

No external services. It reconstructs a lightweight adjacency view from
``graph/nodes.json`` + ``graph/edges.json`` (or from a live :class:`CodeGraph`)
and answers the high-level queries used by ``code-memory impact`` / ``graph`` /
retrieval.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from code_memory.graph.repository import ENTRY_NAME_HINTS, GraphRepository
from code_memory.models.graph import CodeGraph


class InMemoryGraphRepository(GraphRepository):
    def __init__(self, *, nodes_path: Path | None = None,
                 edges_path: Path | None = None,
                 graph: CodeGraph | None = None):
        self._nodes: dict[str, dict[str, Any]] = {}
        self._edges: list[dict[str, Any]] = []
        self._out: dict[str, list[dict[str, Any]]] = {}
        self._in: dict[str, list[dict[str, Any]]] = {}
        self._nodes_path = nodes_path
        self._edges_path = edges_path
        if graph is not None:
            self._load_from_graph(graph)
        elif nodes_path and edges_path and nodes_path.is_file():
            self._load_from_json(nodes_path, edges_path)

    # -- loading ---------------------------------------------------
    def _index(self) -> None:
        self._out, self._in = {}, {}
        for e in self._edges:
            self._out.setdefault(e["src"], []).append(e)
            self._in.setdefault(e["dst"], []).append(e)

    def _load_from_graph(self, graph: CodeGraph) -> None:
        self._nodes = {n["id"]: n for n in graph.nodes_json()}
        self._edges = list(graph.edges_json())
        self._index()

    def _load_from_json(self, nodes_path: Path, edges_path: Path) -> None:
        self._nodes = {n["id"]: n for n in
                       json.loads(nodes_path.read_text(encoding="utf-8"))}
        self._edges = (json.loads(edges_path.read_text(encoding="utf-8"))
                       if edges_path.is_file() else [])
        self._index()

    # -- lifecycle -----------------------------------------------
    def replace_graph(self, graph: CodeGraph) -> None:
        self._load_from_graph(graph)
        if self._nodes_path and self._edges_path:
            self._nodes_path.parent.mkdir(parents=True, exist_ok=True)
            self._nodes_path.write_text(
                json.dumps(list(self._nodes.values()), indent=2) + "\n",
                encoding="utf-8")
            self._edges_path.write_text(
                json.dumps(self._edges, indent=2) + "\n", encoding="utf-8")

    def clear(self) -> None:
        self._nodes, self._edges = {}, []
        self._index()

    def stats(self) -> dict[str, Any]:
        by_kind: dict[str, int] = {}
        by_edge: dict[str, int] = {}
        for n in self._nodes.values():
            by_kind[n["kind"]] = by_kind.get(n["kind"], 0) + 1
        for e in self._edges:
            by_edge[e["type"]] = by_edge.get(e["type"], 0) + 1
        return {"backend": "memory", "nodes": len(self._nodes),
                "edges": len(self._edges),
                "nodes_by_kind": dict(sorted(by_kind.items())),
                "edges_by_type": dict(sorted(by_edge.items()))}

    # -- reads --------------------------------------------------
    def get_node(self, node_id: str) -> dict[str, Any] | None:
        return self._nodes.get(node_id)

    def find_nodes(self, *, kind=None, name_contains=None, fqn=None):
        out = []
        needle = name_contains.lower() if name_contains else None
        for n in self._nodes.values():
            if kind and n["kind"] != kind:
                continue
            if fqn and n.get("fqn") != fqn:
                continue
            if needle and needle not in n["id"].lower() \
                    and needle not in str(n.get("name", "")).lower() \
                    and needle not in str(n.get("fqn", "")).lower():
                continue
            out.append(n)
        return out

    def neighbors(self, node_id, *, edge_types=(), direction="out"):
        edges = []
        if direction in ("out", "both"):
            edges += self._out.get(node_id, [])
        if direction in ("in", "both"):
            edges += self._in.get(node_id, [])
        result = []
        for e in edges:
            if edge_types and e["type"] not in edge_types:
                continue
            other = e["dst"] if e["src"] == node_id else e["src"]
            result.append({"edge": e["type"], "id": other,
                           "confidence": e.get("confidence"),
                           "node": self._nodes.get(other),
                           "evidence": e.get("evidence", {})})
        return result

    def find_callers(self, method_id):
        return [{"id": e["src"], "confidence": e.get("confidence"),
                 "line": e.get("evidence", {}).get("line_start")}
                for e in self._in.get(method_id, []) if e["type"] == "CALLS"]

    def find_callees(self, method_id):
        return [{"id": e["dst"], "confidence": e.get("confidence"),
                 "line": e.get("evidence", {}).get("line_start"),
                 "call": e.get("evidence", {}).get("call")}
                for e in self._out.get(method_id, []) if e["type"] == "CALLS"]

    def find_implementations(self, type_id):
        return sorted({e["src"] for e in self._in.get(type_id, [])
                       if e["type"] in ("EXTENDS", "IMPLEMENTS")})

    def find_impact(self, node_id, max_depth=4):
        transitive_callers = self._bfs(node_id, ("CALLS",), "in", max_depth)
        callees = self._bfs(node_id, ("CALLS",), "out", max_depth)
        direct_callers = [c["id"] for c in self.find_callers(node_id)]

        owner = (self._nodes.get(node_id) or {}).get("owner")
        tests = [c for c in transitive_callers
                 if _looks_like_test(self._nodes.get(c, {}))]

        related: dict[str, list[str]] = {}
        for etype in ("USES_TYPE", "RETURNS_TYPE", "THROWS", "EXECUTES_SQL",
                      "READS_TABLE", "WRITES_TABLE", "OVERRIDES", "MAPPED_TO",
                      "EXPOSES", "INJECTS", "HANDLES"):
            hits = [n["id"] for n in self.neighbors(node_id, edge_types=(etype,),
                                                    direction="both")]
            if hits:
                related[etype] = sorted(set(hits))

        return {
            "target": node_id,
            "owner": owner,
            "direct_callers": sorted(set(direct_callers)),
            "transitive_callers": sorted(transitive_callers),
            "callees": sorted(callees),
            "tests": sorted(set(tests)),
            "related": related,
        }

    def find_endpoint_flow(self, endpoint_id):
        handler = next((n["id"] for n in self.neighbors(
            endpoint_id, edge_types=("MAPPED_TO",), direction="out")), None)
        chain = self.find_call_flow(handler) if handler else []
        return {"endpoint": endpoint_id, "handler": handler, "flow": chain}

    def find_call_flow(self, method_id, max_depth=8):
        return self._ordered_calls(method_id, max_depth=max_depth)

    def find_entrypoints(self):
        # node dicts here are the flattened JSON form (kind/id/name at the top
        # level, everything else from Node.properties flattened alongside) -
        # see CodeGraph.nodes_json()/Node.to_dict().
        mapped = {e["dst"] for e in self._edges if e["type"] == "MAPPED_TO"}
        out = []
        for n in self._nodes.values():
            if n["kind"] not in ("Method", "Constructor"):
                continue
            if n["id"] in mapped or n.get("spark_job") or n.get("placeholder"):
                continue
            owner = n.get("owner", "") or ""
            if "Test" in owner or "test" in owner.lower():
                continue
            has_caller = any(e["type"] == "CALLS" for e in self._in.get(n["id"], []))
            if has_caller:
                continue
            calls_out = [e for e in self._out.get(n["id"], [])
                        if e["type"] == "CALLS" and e["dst"].startswith("method:")]
            if not calls_out:
                continue
            out.append({"id": n["id"], "fqn": n.get("fqn", n["id"]),
                       "name_hint": n.get("name") in ENTRY_NAME_HINTS,
                       "call_count": len(calls_out)})
        # methods that look intentional (main/run/...) first, then by fan-out
        out.sort(key=lambda e: (not e["name_hint"], -e["call_count"]))
        return out

    def find_database_usage(self, table):
        table_id = table if table.startswith("table:") else f"table:{table.lower()}"
        readers, writers = [], []
        for e in self._in.get(table_id, []):
            (readers if e["type"] == "READS_TABLE" else
             writers if e["type"] == "WRITES_TABLE" else []).append(e["src"])
        # map SQL statements back to the methods that execute them
        def via_methods(sql_ids):
            out = set()
            for sid in sql_ids:
                if sid.startswith("sql:"):
                    out.update(e["src"] for e in self._in.get(sid, [])
                               if e["type"] == "EXECUTES_SQL")
                else:
                    out.add(sid)
            return sorted(out)
        return {"table": table_id,
                "read_by": via_methods(readers),
                "written_by": via_methods(writers)}

    # -- helpers -------------------------------------------------
    def _bfs(self, start, edge_types, direction, max_depth):
        adj = self._in if direction == "in" else self._out
        seen: set[str] = set()
        frontier = [(start, 0)]
        while frontier:
            node, depth = frontier.pop()
            if depth >= max_depth:
                continue
            for e in adj.get(node, []):
                if e["type"] not in edge_types:
                    continue
                other = e["src"] if direction == "in" else e["dst"]
                if other not in seen:
                    seen.add(other)
                    frontier.append((other, depth + 1))
        return seen

    def _ordered_calls(self, start, max_depth=5):
        out, seen = [], {start}
        frontier = [(start, 0)]
        while frontier:
            node, depth = frontier.pop(0)
            if depth >= max_depth:
                continue
            for e in self._out.get(node, []):
                if e["type"] != "CALLS" or not e["dst"].startswith("method:"):
                    continue
                if e["dst"] in seen:
                    continue
                seen.add(e["dst"])
                out.append({"id": e["dst"], "depth": depth,
                            "confidence": e.get("confidence")})
                frontier.append((e["dst"], depth + 1))
        return out


def _looks_like_test(node: dict[str, Any]) -> bool:
    fqn = (node.get("fqn") or node.get("owner") or node.get("id") or "")
    annos = node.get("annotations") or []
    return ("Test" in fqn or "test" in fqn.lower().split("#")[0]
            or "Test" in annos or "ParameterizedTest" in annos)
