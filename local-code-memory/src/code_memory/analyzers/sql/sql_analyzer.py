"""Phase 6 - SQL analyzer.

Collects SQL from Java source (@Query, string literals, text blocks) and from
``.sql`` files, parses each statement, and adds first-class SQL entities to the
code graph:

  nodes:  SQLStatement, Table
  edges:  EXECUTES_SQL  (method | source-file -> SQLStatement)
          READS_TABLE   (SQLStatement -> Table)
          WRITES_TABLE  (SQLStatement -> Table)

Identical statements are deduplicated onto one SQLStatement node that records
every call site. Nothing here aborts a scan.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from code_memory.analyzers.sql.extract import (
    SqlHit,
    extract_sql_from_java,
    split_sql_file,
)
from code_memory.analyzers.sql.parse import parse_sql
from code_memory.logging_setup import get_logger
from code_memory.models.code import ParsedFile
from code_memory.models.graph import CodeGraph, Confidence, Edge, Node

log = get_logger("analyzers.sql")

_WS = re.compile(r"\s+")


@dataclass
class SqlModel:
    statements: dict[str, dict[str, Any]] = field(default_factory=dict)  # id -> info
    tables: set[str] = field(default_factory=set)

    def counts(self) -> dict[str, Any]:
        by_type: dict[str, int] = {}
        parsed_ok = 0
        for s in self.statements.values():
            by_type[s["statement_type"]] = by_type.get(s["statement_type"], 0) + 1
            parsed_ok += 1 if s["parsed_ok"] else 0
        return {
            "sql_statements": len(self.statements),
            "sql_by_type": dict(sorted(by_type.items())),
            "sql_parsed_ok": parsed_ok,
            "tables": len(self.tables),
        }

    def is_present(self) -> bool:
        return bool(self.statements)


def _norm(sql: str) -> str:
    return _WS.sub(" ", sql).strip().rstrip(";")


def _sql_id(sql: str) -> str:
    return "sql:" + hashlib.sha1(_norm(sql).lower().encode()).hexdigest()[:12]


def _owner_for_line(pf: ParsedFile, line: int) -> str | None:
    """FQN of the method whose body span contains ``line`` (innermost wins)."""
    best: str | None = None
    best_span = None
    for td in pf.all_types():
        for m in td.methods:
            lo, hi = m.location.line_start, m.location.line_end
            if lo <= line <= hi and (best_span is None or (hi - lo) < best_span):
                best, best_span = m.fqn, hi - lo
    return best


def analyze_sql(root: Path, parsed: list[ParsedFile], inventory,
                graph: CodeGraph) -> SqlModel:
    model = SqlModel()

    # -- SQL embedded in Java --------------------------------------------
    for pf in parsed:
        path = root / pf.relative_path
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for hit in extract_sql_from_java(source):
            owner = _owner_for_line(pf, hit.line)
            src_id = f"method:{owner}" if owner else f"file:{pf.relative_path}"
            _record(model, graph, hit.sql, src_id, pf.relative_path, hit.line,
                    hit.kind)

    # -- standalone .sql files ------------------------------------------
    from code_memory.models.inventory import FileKind

    for entry in getattr(inventory, "files", []):
        if entry.kind != FileKind.SQL:
            continue
        path = root / entry.relative_path
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        file_id = f"file:{entry.relative_path}"
        graph.add_node(Node(file_id, "SourceFile", entry.relative_path,
                            {"kind": "sql"}))
        for stmt, line in split_sql_file(text):
            _record(model, graph, stmt, file_id, entry.relative_path, line,
                    "sql-file")

    return model


def _record(model: SqlModel, graph: CodeGraph, sql: str, src_id: str,
            rel_path: str, line: int, kind: str) -> None:
    sid = _sql_id(sql)
    parsed = parse_sql(sql)

    info = model.statements.get(sid)
    if info is None:
        info = {
            "id": sid,
            "statement_type": parsed.statement_type,
            "parsed_ok": parsed.parsed_ok,
            "text": _norm(sql)[:500],
            "tables_read": parsed.tables_read,
            "tables_written": parsed.tables_written,
            "sources": [],
            "origin_kinds": set(),
        }
        model.statements[sid] = info
    info["sources"].append({"src": src_id, "file": rel_path, "line": line})
    info["origin_kinds"].add(kind)

    graph.add_node(Node(sid, "SQLStatement", parsed.statement_type, {
        "statement_type": parsed.statement_type,
        "parsed_ok": parsed.parsed_ok,
        "text": info["text"],
        "tables_read": parsed.tables_read,
        "tables_written": parsed.tables_written,
        "origin": sorted(info["origin_kinds"]),
        "resolved": True,
    }))
    # jpa-query (JPQL, not native) references entity names, not tables - lower
    # confidence for its table edges.
    conf = Confidence.MEDIUM if kind == "jpa-query" or not parsed.parsed_ok \
        else Confidence.HIGH
    graph.add_edge(Edge("EXECUTES_SQL", src_id, sid, Confidence.HIGH,
                        {"file": rel_path, "line_start": line, "origin": kind}))

    for tbl in parsed.tables_read:
        model.tables.add(tbl)
        graph.add_node(Node(f"table:{tbl}", "Table", tbl, {"resolved": True}))
        graph.add_edge(Edge("READS_TABLE", sid, f"table:{tbl}", conf,
                            {"file": rel_path, "line_start": line}))
    for tbl in parsed.tables_written:
        model.tables.add(tbl)
        graph.add_node(Node(f"table:{tbl}", "Table", tbl, {"resolved": True}))
        graph.add_edge(Edge("WRITES_TABLE", sid, f"table:{tbl}", conf,
                            {"file": rel_path, "line_start": line}))
