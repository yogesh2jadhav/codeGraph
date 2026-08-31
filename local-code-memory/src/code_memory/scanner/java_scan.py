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
from code_memory.analyzers.spring import SpringModel, analyze_spring

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
    spring: SpringModel | None = None

    def stats(self) -> dict:
        s = {
            "java_files_parsed": len(self.parsed_files),
            "parse_status": self.status_counts,
            "skipped": len(self.skipped),
            **self.graph.counts(),
            "duration_ms": self.duration_ms,
        }
        if self.spring is not None:
            s["spring"] = self.spring.counts()
        return s


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

        try:
            spring = analyze_spring(parsed, graph)
        except Exception as exc:  # never let an analyzer abort the scan
            log.error("spring analyzer failed", extra={"error": str(exc)})
            spring = None

        result = JavaScanResult(
            graph=graph, parsed_files=parsed, status_counts=counts,
            skipped=skipped, spring=spring,
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
        summary = {**graph.counts(), "parse_status": result.status_counts}
        if result.spring is not None:
            summary["spring"] = result.spring.counts()
        summary_path.write_text(json.dumps(summary, indent=2) + "\n",
                                encoding="utf-8")

        unresolved_path = reports_dir / "unresolved_symbols.md"
        unresolved_path.write_text(_render_unresolved(graph), encoding="utf-8")

        parse_path = reports_dir / "parse_report.md"
        parse_path.write_text(_render_parse_report(result), encoding="utf-8")

        context_dir = out / "context"
        context_dir.mkdir(parents=True, exist_ok=True)
        callgraph_path = context_dir / "07_call_graph.md"
        callgraph_path.write_text(_render_call_graph(graph), encoding="utf-8")

        written = [nodes_path, edges_path, summary_path, unresolved_path,
                   parse_path, callgraph_path]

        if result.spring is not None and result.spring.is_spring():
            api_path = context_dir / "06_api_endpoints.md"
            api_path.write_text(_render_api_endpoints(graph, result.spring),
                                encoding="utf-8")
            written.append(api_path)

        return written


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


def _render_api_endpoints(graph: CodeGraph, spring) -> str:
    """Endpoint table + per-endpoint call flow (HTTP -> controller -> service ->
    repository), reconstructed from CALLS edges. Only in-scan methods appear in
    the flow; external calls are omitted for brevity."""
    from code_memory.graph import queries

    lines = ["# 06 - API endpoints", "",
             "> Generated (Phase 4, Spring analyzer). Annotation-based; the call "
             "flow uses the syntactic call graph - trust the confidence tags.",
             ""]

    c = spring.counts()
    lines.append(f"- Components: **{c['components']}** "
                 f"{c['components_by_stereotype']}")
    lines.append(f"- Endpoints: **{c['endpoints']}**  "
                 f"Beans: {c['beans']}  Exception handlers: "
                 f"{c['exception_handlers']}  Injections: {c['injections']}")
    lines.append("")

    if not spring.endpoints:
        lines.append("_No HTTP endpoints detected._")
        return "\n".join(lines) + "\n"

    lines += ["| HTTP | Path | Handler | Location |", "| --- | --- | --- | --- |"]
    for ep in sorted(spring.endpoints, key=lambda e: (e["path"], e["http_method"])):
        lines.append(f"| {ep['http_method']} | `{ep['path']}` | "
                     f"`{ep['handler'].split('#')[-1]}` | {ep['location']} |")
    lines.append("")

    def stereo_of(method_id: str) -> str:
        owner = graph.get(method_id)
        if owner is None:
            return ""
        owner_fqn = owner.properties.get("owner", "")
        t = graph.get(f"type:{owner_fqn}")
        return t.properties.get("spring_stereotype", "") if t else ""

    lines += ["## Call flow per endpoint", ""]
    for ep in sorted(spring.endpoints, key=lambda e: e["path"]):
        handler_id = f"method:{ep['handler']}"
        lines.append(f"### `{ep['http_method']} {ep['path']}`")
        lines.append(f"handler `{ep['handler']}` - {ep['location']}")
        seen = {handler_id}
        frontier = [(handler_id, 0)]
        flow: list[str] = []
        while frontier:
            node_id, depth = frontier.pop(0)
            if depth >= 4:
                continue
            for e in queries.find_callees(graph, node_id):
                if not e["id"].startswith("method:") or e["id"] in seen:
                    continue
                seen.add(e["id"])
                tag = stereo_of(e["id"]) or "?"
                flow.append(f"{'  ' * depth}-> `{e['id'][len('method:'):]}` "
                            f"[{tag}] ({e['confidence']})")
                frontier.append((e["id"], depth + 1))
        lines += flow if flow else ["_no resolved downstream calls_"]
        lines.append("")
    return "\n".join(lines) + "\n"


def _render_call_graph(graph: CodeGraph) -> str:
    """Per-method 'calls / called-by' listing for methods declared in this scan.

    Bounded output: external call targets are collapsed; only in-scan methods get
    a section. Confidence is shown so the reader knows how much to trust a link.
    """
    from code_memory.graph import queries

    methods = sorted(
        (n for n in graph.nodes
         if n.kind in ("Method", "Constructor")
         and not n.properties.get("external")
         and not n.properties.get("placeholder")),
        key=lambda n: n.properties.get("fqn", n.id),
    )
    c = graph.counts()
    lines = [
        "# 07 - Call graph", "",
        "> Generated (Phase 3). Syntactic call graph - resolved with local "
        "heuristics, not a full type system. Trust the confidence tags.",
        "",
        f"- CALLS edges: **{c['call_edges']}**  "
        f"(resolution rate {c['call_resolution_rate']})",
        f"- by confidence: {c['calls_by_confidence']}",
        "",
    ]
    if not methods:
        lines.append("_No in-scan methods._")
        return "\n".join(lines) + "\n"

    def short(node_id: str) -> str:
        if node_id.startswith("method:"):
            return node_id[len("method:"):]
        if node_id.startswith("extmethod:"):
            return node_id[len("extmethod:"):] + " _(external)_"
        return node_id

    for n in methods:
        loc = n.properties.get("location", {})
        where = f"{loc.get('relative_path', '?')}:{loc.get('line_start', '?')}"
        callees = queries.find_callees(graph, n.id)
        callers = queries.find_callers(graph, n.id)
        lines.append(f"## `{n.properties.get('fqn', n.name)}`")
        lines.append(f"_{where}_")
        lines.append("")
        if callers:
            lines.append("**called by:**")
            for e in callers:
                lines.append(f"- `{short(e['id'])}` ({e['confidence']})")
        if callees:
            lines.append("**calls:**")
            for e in callees:
                lines.append(f"- `{short(e['id'])}` ({e['confidence']})"
                             + (f" - line {e['line']}" if e.get('line') else ""))
        if not callers and not callees:
            lines.append("_no resolved calls_")
        lines.append("")
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
