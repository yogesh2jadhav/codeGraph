"""Neo4j-backed :class:`GraphRepository` (optional).

Needs ``pip install neo4j`` and a running server (``docker compose up -d``).
The driver is imported lazily so the package works without it. Node ids from
the :class:`CodeGraph` are stored verbatim on a ``:CodeNode`` label with the
node ``kind`` as a second label; edges keep their ``type`` as the relationship
type plus ``confidence`` / ``evidence`` properties.
"""

from __future__ import annotations

import json
from typing import Any

from code_memory.logging_setup import get_logger
from code_memory.graph.repository import GraphRepository
from code_memory.models.graph import CodeGraph

log = get_logger("graph.neo4j")

_SAFE_LABEL = str.isidentifier


class Neo4jGraphRepository(GraphRepository):
    def __init__(self, *, uri: str, user: str, password: str, database: str):
        from neo4j import GraphDatabase  # lazy - optional dependency

        self._driver = GraphDatabase.driver(uri, auth=(user, password))
        self._database = database

    # -- lifecycle ---------------------------------------------------
    def ping(self) -> None:
        with self._driver.session(database=self._database) as s:
            s.run("RETURN 1").consume()

    def close(self) -> None:
        self._driver.close()

    def clear(self) -> None:
        with self._driver.session(database=self._database) as s:
            s.run("MATCH (n:CodeNode) DETACH DELETE n").consume()

    def replace_graph(self, graph: CodeGraph) -> None:
        self.clear()
        nodes = graph.nodes_json()
        edges = graph.edges_json()
        with self._driver.session(database=self._database) as s:
            s.run("CREATE CONSTRAINT code_node_id IF NOT EXISTS "
                  "FOR (n:CodeNode) REQUIRE n.id IS UNIQUE").consume()
            # nodes in batches
            for batch in _chunks(nodes, 500):
                s.run(
                    "UNWIND $rows AS row MERGE (n:CodeNode {id: row.id}) "
                    "SET n += row.props, n.kind = row.kind, n.name = row.name",
                    rows=[{"id": n["id"], "kind": n["kind"],
                           "name": n.get("name", ""),
                           "props": _flatten(n)} for n in batch],
                ).consume()
            for batch in _chunks(edges, 500):
                s.run(
                    "UNWIND $rows AS row "
                    "MATCH (a:CodeNode {id: row.src}) "
                    "MATCH (b:CodeNode {id: row.dst}) "
                    "CALL apoc.merge.relationship(a, row.type, "
                    "  {}, {confidence: row.confidence, evidence: row.evidence}, b) "
                    "YIELD rel RETURN count(rel)",
                    rows=[{"src": e["src"], "dst": e["dst"], "type": e["type"],
                           "confidence": e.get("confidence"),
                           "evidence": json.dumps(e.get("evidence", {}))}
                          for e in batch],
                ).consume() if _apoc_available(s) else _fallback_edges(s, batch)

    def stats(self) -> dict[str, Any]:
        with self._driver.session(database=self._database) as s:
            n = s.run("MATCH (n:CodeNode) RETURN count(n) AS c").single()["c"]
            r = s.run("MATCH ()-[r]->() RETURN count(r) AS c").single()["c"]
        return {"backend": "neo4j", "nodes": n, "edges": r}

    # -- reads ------------------------------------------------------
    def _run(self, cypher: str, **params) -> list[dict[str, Any]]:
        with self._driver.session(database=self._database) as s:
            return [r.data() for r in s.run(cypher, **params)]

    def get_node(self, node_id):
        rows = self._run("MATCH (n:CodeNode {id: $id}) RETURN n", id=node_id)
        return dict(rows[0]["n"]) if rows else None

    def find_nodes(self, *, kind=None, name_contains=None, fqn=None):
        rows = self._run(
            "MATCH (n:CodeNode) "
            "WHERE ($kind IS NULL OR n.kind = $kind) "
            "AND ($fqn IS NULL OR n.fqn = $fqn) "
            "AND ($needle IS NULL OR toLower(n.id) CONTAINS $needle "
            "     OR toLower(coalesce(n.name,'')) CONTAINS $needle) "
            "RETURN n LIMIT 200",
            kind=kind, fqn=fqn,
            needle=name_contains.lower() if name_contains else None)
        return [dict(r["n"]) for r in rows]

    def neighbors(self, node_id, *, edge_types=(), direction="out"):
        arrow = {"out": "-[r]->", "in": "<-[r]-", "both": "-[r]-"}[direction]
        rows = self._run(
            f"MATCH (n:CodeNode {{id: $id}}){arrow}(m:CodeNode) "
            "WHERE size($types) = 0 OR type(r) IN $types "
            "RETURN type(r) AS edge, m.id AS id, r.confidence AS confidence, m AS node",
            id=node_id, types=list(edge_types))
        return [{"edge": r["edge"], "id": r["id"],
                 "confidence": r["confidence"], "node": dict(r["node"])}
                for r in rows]

    def find_callers(self, method_id):
        return self._run(
            "MATCH (c:CodeNode)-[r:CALLS]->(m:CodeNode {id: $id}) "
            "RETURN c.id AS id, r.confidence AS confidence", id=method_id)

    def find_callees(self, method_id):
        return self._run(
            "MATCH (m:CodeNode {id: $id})-[r:CALLS]->(c:CodeNode) "
            "RETURN c.id AS id, r.confidence AS confidence", id=method_id)

    def find_implementations(self, type_id):
        rows = self._run(
            "MATCH (t:CodeNode)-[:EXTENDS|IMPLEMENTS]->(:CodeNode {id: $id}) "
            "RETURN t.id AS id", id=type_id)
        return sorted(r["id"] for r in rows)

    def find_impact(self, node_id, max_depth=4):
        callers = self._run(
            f"MATCH (c:CodeNode)-[:CALLS*1..{max_depth}]->(:CodeNode {{id: $id}}) "
            "RETURN DISTINCT c.id AS id", id=node_id)
        callees = self._run(
            f"MATCH (:CodeNode {{id: $id}})-[:CALLS*1..{max_depth}]->(c:CodeNode) "
            "RETURN DISTINCT c.id AS id", id=node_id)
        return {"target": node_id,
                "transitive_callers": sorted(r["id"] for r in callers),
                "callees": sorted(r["id"] for r in callees),
                "tests": sorted(r["id"] for r in callers
                                if "Test" in r["id"]),
                "direct_callers": [r["id"] for r in self.find_callers(node_id)],
                "related": {}}

    def find_endpoint_flow(self, endpoint_id):
        rows = self._run(
            "MATCH (:CodeNode {id: $id})-[:MAPPED_TO]->(h:CodeNode) "
            "OPTIONAL MATCH p = (h)-[:CALLS*1..5]->(x:CodeNode) "
            "RETURN h.id AS handler, [n IN nodes(p) | n.id] AS path", id=endpoint_id)
        handler = rows[0]["handler"] if rows else None
        flow = sorted({tuple(r["path"]) for r in rows if r["path"]})
        return {"endpoint": endpoint_id, "handler": handler,
                "flow": [list(p) for p in flow]}

    def find_call_flow(self, method_id, max_depth=8):
        rows = self._run(
            f"MATCH p = (:CodeNode {{id: $id}})-[:CALLS*1..{int(max_depth)}]->"
            "(x:CodeNode) "
            "WITH x, min(length(p)) AS depth "
            "RETURN x.id AS id, depth ORDER BY depth", id=method_id)
        return [{"id": r["id"], "depth": r["depth"], "confidence": None}
                for r in rows]

    def find_entrypoints(self):
        rows = self._run(
            "MATCH (m:CodeNode) WHERE m.kind IN ['Method', 'Constructor'] "
            "AND NOT coalesce(m.placeholder, false) "
            "AND NOT coalesce(m.spark_job, false) "
            "AND NOT ( ()-[:CALLS]->(m) ) "
            "AND NOT ( (:CodeNode)-[:MAPPED_TO]->(m) ) "
            "AND EXISTS( (m)-[:CALLS]->(:CodeNode) ) "
            "RETURN m.id AS id, m.fqn AS fqn, m.name AS name, m.owner AS owner, "
            "size([(m)-[:CALLS]->(:CodeNode) | 1]) AS call_count"
        )
        out = [
            {"id": r["id"], "fqn": r["fqn"] or r["id"],
             "name_hint": r["name"] in _ENTRY_NAME_HINTS,
             "call_count": r["call_count"]}
            for r in rows
            if not ("Test" in (r["owner"] or "") or "test" in (r["owner"] or "").lower())
        ]
        out.sort(key=lambda e: (not e["name_hint"], -e["call_count"]))
        return out

    def find_database_usage(self, table):
        table_id = table if table.startswith("table:") else f"table:{table.lower()}"
        read = self._run(
            "MATCH (m:CodeNode)-[:EXECUTES_SQL]->(:CodeNode)-[:READS_TABLE]->"
            "(:CodeNode {id: $id}) RETURN DISTINCT m.id AS id", id=table_id)
        write = self._run(
            "MATCH (m:CodeNode)-[:EXECUTES_SQL]->(:CodeNode)-[:WRITES_TABLE]->"
            "(:CodeNode {id: $id}) RETURN DISTINCT m.id AS id", id=table_id)
        return {"table": table_id, "read_by": [r["id"] for r in read],
                "written_by": [r["id"] for r in write]}


# -- helpers -----------------------------------------------------------------
def _chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def _flatten(node: dict[str, Any]) -> dict[str, Any]:
    """Neo4j only stores primitives / arrays of primitives - JSON-encode the rest."""
    out: dict[str, Any] = {}
    for k, v in node.items():
        if k in ("id", "kind", "name"):
            continue
        if isinstance(v, (str, int, float, bool)) or (
                isinstance(v, list) and all(isinstance(x, (str, int, float, bool))
                                            for x in v)):
            out[k] = v
        else:
            out[k] = json.dumps(v)
    return out


def _apoc_available(session) -> bool:
    try:
        session.run("RETURN apoc.version()").consume()
        return True
    except Exception:
        return False


def _fallback_edges(session, batch) -> None:
    for e in batch:
        rtype = e["type"] if _SAFE_LABEL(e["type"]) else "REL"
        session.run(
            f"MATCH (a:CodeNode {{id: $src}}), (b:CodeNode {{id: $dst}}) "
            f"MERGE (a)-[r:{rtype}]->(b) "
            "SET r.confidence = $confidence, r.evidence = $evidence",
            src=e["src"], dst=e["dst"], confidence=e.get("confidence"),
            evidence=json.dumps(e.get("evidence", {}))).consume()
