"""Phase 2 - Java semantic scan.

Parses every Java source file found by the Phase 1 inventory, extracts code
entities (types / methods / fields / params / annotations / imports) with source
locations, and builds a normalized :class:`CodeGraph`.

Outputs (under ``<output_dir>/``):
  graph/nodes.json, graph/edges.json, graph/graph_summary.json
  reports/unresolved_symbols.md, reports/parse_report.md

Fault tolerance: a file that cannot be read or parsed is recorded and skipped;
files parsed with ERROR nodes are kept as ``partial``.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from code_memory.config import Config
from code_memory.logging_setup import get_logger
from code_memory.models.code import ParsedFile, ParseStatus
from code_memory.models.graph import CodeGraph
from code_memory.models.inventory import FileKind, ProjectInventory
from code_memory.parsers.java import java_available, parse_java_source
from code_memory.graph import build_graph

log = get_logger("scanner.java")

_JAVA_KINDS = {FileKind.JAVA_MAIN, FileKind.JAVA_TEST}


@dataclass
class JavaScanResult:
    graph: CodeGraph
    parsed_files: list[ParsedFile] = field(default_factory=list)
    status_counts: dict[str, int] = field(default_factory=dict)
    duration_ms: int = 0
    artifacts: list[Path] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    def stats(self) -> dict:
        return {
            "java_files_parsed": len(self.parsed_files),
            "parse_status": self.status_counts,
            "skipped": len(self.skipped),
            **self.graph.counts(),
            "duration_ms": self.duration_ms,
        }


class JavaSemanticScanner:
    def __init__(self, config: Config):
        self.config = config
        self.root = config.project_root

    def scan(self, inventory: ProjectInventory, *,
             write_artifacts: bool = True) -> JavaScanResult:
        started = time.perf_counter()

        if not java_available():
            log.warning("tree-sitter-java not installed - skipping Phase 2")
            return JavaScanResult(graph=CodeGraph(),
                                  status_counts={"unavailable": 1})

        parsed: list[ParsedFile] = []
        skipped: list[str] = []
        counts = {s.value: 0 for s in ParseStatus}

        java_entries = [e for e in inventory.files if e.kind in _JAVA_KINDS]
        for entry in java_entries:
            path = self.root / entry.relative_path
            try:
                source = path.read_bytes()
            except OSError as exc:
                skipped.append(f"{entry.relative_path}: {exc}")
                continue
            pf = parse_java_source(entry.relative_path, source)
            counts[pf.status.value] = counts.get(pf.status.value, 0) + 1
            parsed.append(pf)

        graph = build_graph(parsed)

        result = JavaScanResult(
            graph=graph, parsed_files=parsed, status_counts=counts,
            skipped=skipped,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
        if write_artifacts:
            result.artifacts = self._write(graph, result)

        log.info("java semantic scan done", extra={
            "files": len(parsed), "nodes": graph.counts()["node_count"],
            "edges": graph.counts()["edge_count"],
            "unresolved": graph.counts()["unresolved_count"],
            "duration_ms": result.duration_ms})
        return result

    # -- artifacts ---------------------------------------------------
    def _write(self, graph: CodeGraph, result: JavaScanResult) -> list[Path]:
        out = self.config.output_dir
        graph_dir = out / "graph"
        reports_dir = out / "reports"
        graph_dir.mkdir(parents=True, exist_ok=True)
        reports_dir.mkdir(parents=True, exist_ok=True)

        nodes_path = graph_dir / "nodes.json"
        edges_path = graph_dir / "edges.json"
        summary_path = graph_dir / "graph_summary.json"
        nodes_path.write_text(json.dumps(graph.nodes_json(), indent=2) + "\n",
                              encoding="utf-8")
        edges_path.write_text(json.dumps(graph.edges_json(), indent=2) + "\n",
                              encoding="utf-8")
        summary_path.write_text(
            json.dumps({**graph.counts(),
                        "parse_status": result.status_counts},
                       indent=2) + "\n", encoding="utf-8")

        unresolved_path = reports_dir / "unresolved_symbols.md"
        unresolved_path.write_text(_render_unresolved(graph), encoding="utf-8")

        parse_path = reports_dir / "parse_report.md"
        parse_path.write_text(_render_parse_report(result), encoding="utf-8")

        return [nodes_path, edges_path, summary_path, unresolved_path, parse_path]


def _render_unresolved(graph: CodeGraph) -> str:
    rows = sorted(graph.unresolved(), key=lambda n: (n.kind, n.id))
    lines = ["# Unresolved symbols", "",
             "Referenced by the code but not declared within this scan "
             "(external libraries, JDK, or names the resolver could not match).",
             ""]
    if not rows:
        lines.append("_None - every reference resolved._")
        return "\n".join(lines) + "\n"
    lines += ["| Kind | Symbol |", "| --- | --- |"]
    for n in rows:
        lines.append(f"| {n.kind} | `{n.name}` |")
    return "\n".join(lines) + "\n"


def _render_parse_report(result: JavaScanResult) -> str:
    c = result.status_counts
    lines = [
        "# Java parse report", "",
        f"- Files parsed: **{len(result.parsed_files)}**",
        f"- success: {c.get('success', 0)}  partial: {c.get('partial', 0)}  "
        f"failed: {c.get('failed', 0)}",
        f"- Skipped (unreadable): {len(result.skipped)}",
        "",
    ]
    partial = [pf for pf in result.parsed_files
               if pf.status != ParseStatus.SUCCESS]
    if partial:
        lines += ["## Files with parse issues", ""]
        for pf in partial:
            lines.append(f"- `{pf.relative_path}` - {pf.status.value}"
                         + (f" ({'; '.join(pf.errors)})" if pf.errors else ""))
        lines.append("")
    if result.skipped:
        lines += ["## Skipped files", ""] + [f"- {s}" for s in result.skipped]
        lines.append("")
    return "\n".join(lines) + "\n"
