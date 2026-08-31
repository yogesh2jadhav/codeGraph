"""Spring / Spring Boot analyzer.

Runs *after* the Phase 2/3 code graph is built. It reads the parsed files for
annotation arguments (which the graph doesn't keep) and augments the graph with
Spring-specific facts:

  * a ``spring_stereotype`` property on component types
    (RestController / Controller / Service / Repository / Component /
     Configuration / ControllerAdvice / SpringBootApplication)
  * ``Endpoint`` nodes for @RequestMapping / @GetMapping / ... handler methods,
    with ``EXPOSES`` (controller -> endpoint) and ``MAPPED_TO``
    (endpoint -> handler method) edges
  * ``INJECTS`` edges between components (constructor params + @Autowired fields)
  * ``bean`` / ``exception_handler`` flags on methods, and ``HANDLES`` edges
    from @ExceptionHandler methods to the exception type

All detection is annotation-based and best-effort; nothing here aborts a scan.
Meta-annotations are matched by their common simple names only (no transitive
annotation resolution).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from code_memory.graph.builder import _Resolver, _bare
from code_memory.models.code import EntityKind, MethodDecl, ParsedFile, TypeDecl
from code_memory.models.graph import CodeGraph, Confidence, Edge, Node

# stereotype simple-name -> canonical label
_STEREOTYPES = {
    "SpringBootApplication": "SpringBootApplication",
    "RestController": "RestController",
    "Controller": "Controller",
    "Service": "Service",
    "Repository": "Repository",
    "Component": "Component",
    "Configuration": "Configuration",
    "RestControllerAdvice": "ControllerAdvice",
    "ControllerAdvice": "ControllerAdvice",
}
_MAPPING_HTTP = {
    "GetMapping": "GET", "PostMapping": "POST", "PutMapping": "PUT",
    "DeleteMapping": "DELETE", "PatchMapping": "PATCH",
    "RequestMapping": "ANY",
}
_FIRST_STRING_RE = re.compile(r'"([^"]*)"')
_REQUEST_METHOD_RE = re.compile(r"RequestMethod\.(\w+)")
_DOT_CLASS_RE = re.compile(r"([A-Za-z_][\w.]*)\.class")


@dataclass
class SpringModel:
    stereotypes: dict[str, str] = field(default_factory=dict)      # fqn -> label
    endpoints: list[dict[str, Any]] = field(default_factory=list)
    beans: list[str] = field(default_factory=list)                 # method fqns
    exception_handlers: list[str] = field(default_factory=list)
    injects: list[tuple[str, str]] = field(default_factory=list)   # (src fqn, dst fqn)

    def is_spring(self) -> bool:
        return bool(self.stereotypes or self.endpoints)

    def counts(self) -> dict[str, Any]:
        by_label: dict[str, int] = {}
        for label in self.stereotypes.values():
            by_label[label] = by_label.get(label, 0) + 1
        return {
            "spring_detected": self.is_spring(),
            "components": len(self.stereotypes),
            "components_by_stereotype": dict(sorted(by_label.items())),
            "endpoints": len(self.endpoints),
            "beans": len(self.beans),
            "exception_handlers": len(self.exception_handlers),
            "injections": len(self.injects),
        }


def _anno_simple_names(annotations) -> set[str]:
    return {a.name.rsplit(".", 1)[-1] for a in annotations}


def _first_path(arguments_text: str | None) -> str:
    if not arguments_text:
        return ""
    m = _FIRST_STRING_RE.search(arguments_text)
    return m.group(1) if m else ""


def _join_path(base: str, sub: str) -> str:
    parts = [p.strip("/") for p in (base, sub) if p and p.strip("/")]
    return "/" + "/".join(parts) if parts else "/"


def analyze_spring(parsed: list[ParsedFile], graph: CodeGraph) -> SpringModel:
    model = SpringModel()
    resolver = _Resolver(parsed)

    file_of_type: dict[str, ParsedFile] = {}
    all_types: list[tuple[ParsedFile, TypeDecl]] = []
    for pf in parsed:
        for td in pf.all_types():
            file_of_type[td.fqn] = pf
            all_types.append((pf, td))

    # Pass 1: classify every component first, so INJECTS confidence can tell
    # whether an injected type is itself a Spring bean.
    for pf, td in all_types:
        names = _anno_simple_names(td.annotations)
        stereo = next((_STEREOTYPES[n] for n in names if n in _STEREOTYPES), None)
        if stereo is None:
            continue
        model.stereotypes[td.fqn] = stereo
        node = _node(graph, f"type:{td.fqn}")
        if node is not None:
            node.properties["spring_stereotype"] = stereo

    # Pass 2: endpoints, beans, exception handlers, injections.
    for pf, td in all_types:
        stereo = model.stereotypes.get(td.fqn)
        if stereo is None:
            continue

        is_controller = stereo in ("RestController", "Controller", "ControllerAdvice")
        class_path = _class_request_path(td)

        for m in td.methods:
            m_names = _anno_simple_names(m.annotations)

            if is_controller:
                _emit_endpoints(graph, model, td, m, m_names, class_path)

            if "Bean" in m_names:
                model.beans.append(m.fqn)
                mn = _node(graph, f"method:{m.fqn}")
                if mn is not None:
                    mn.properties["bean"] = True

            if "ExceptionHandler" in m_names:
                model.exception_handlers.append(m.fqn)
                mn = _node(graph, f"method:{m.fqn}")
                if mn is not None:
                    mn.properties["exception_handler"] = True
                _emit_exception_handled(graph, resolver, pf, m, m_names)

        _emit_injections(graph, resolver, pf, td, model)

    return model


# -- endpoints -------------------------------------------------------------
def _class_request_path(td: TypeDecl) -> str:
    for a in td.annotations:
        if a.name.rsplit(".", 1)[-1] == "RequestMapping":
            return _first_path(a.arguments_text)
    return ""


def _emit_endpoints(graph, model, td, m: MethodDecl, m_names, class_path):
    mapping = next((n for n in m_names if n in _MAPPING_HTTP), None)
    if mapping is None:
        return
    anno = next(a for a in m.annotations
                if a.name.rsplit(".", 1)[-1] == mapping)
    http = _MAPPING_HTTP[mapping]
    if mapping == "RequestMapping":
        rm = _REQUEST_METHOD_RE.search(anno.arguments_text or "")
        http = rm.group(1) if rm else "ANY"
    path = _join_path(class_path, _first_path(anno.arguments_text))

    endpoint_id = f"endpoint:{http} {path}"
    graph.add_node(Node(endpoint_id, "Endpoint", f"{http} {path}", {
        "http_method": http, "path": path,
        "controller": td.fqn, "handler": m.fqn,
        "location": m.location.to_dict(), "resolved": True,
    }))
    graph.add_edge(Edge("EXPOSES", f"type:{td.fqn}", endpoint_id, Confidence.HIGH,
                        {"file": m.location.relative_path,
                         "line_start": m.location.line_start}))
    graph.add_edge(Edge("MAPPED_TO", endpoint_id, f"method:{m.fqn}",
                        Confidence.HIGH,
                        {"file": m.location.relative_path,
                         "line_start": m.location.line_start}))
    model.endpoints.append({
        "http_method": http, "path": path, "handler": m.fqn,
        "controller": td.fqn,
        "location": f"{m.location.relative_path}:{m.location.line_start}",
    })


# -- dependency injection -----------------------------------------------
def _emit_injections(graph, resolver, pf, td: TypeDecl, model: SpringModel):
    targets: list[tuple[str, int]] = []          # (raw type text, line)

    for ctor in (m for m in td.methods if m.kind == EntityKind.CONSTRUCTOR):
        for p in ctor.parameters:
            targets.append((p.type_text, ctor.location.line_start))
    for fld in td.fields:
        if {"Autowired", "Inject", "Resource"} & _anno_simple_names(fld.annotations):
            targets.append((fld.type_text or "", fld.location.line_start))

    seen: set[str] = set()
    for raw, line in targets:
        dep = resolver.resolve_decl(raw, pf)
        if dep is None or dep.fqn == td.fqn or dep.fqn in seen:
            continue
        seen.add(dep.fqn)
        conf = Confidence.HIGH if dep.fqn in model.stereotypes else Confidence.MEDIUM
        graph.add_edge(Edge("INJECTS", f"type:{td.fqn}", f"type:{dep.fqn}", conf,
                            {"file": pf.relative_path, "line_start": line,
                             "raw": raw}))
        model.injects.append((td.fqn, dep.fqn))


# -- exception handlers ------------------------------------------------
def _emit_exception_handled(graph, resolver, pf, m: MethodDecl, m_names):
    anno = next(a for a in m.annotations
                if a.name.rsplit(".", 1)[-1] == "ExceptionHandler")
    raw_types: list[str] = []
    if anno.arguments_text:
        raw_types = [x for x in _DOT_CLASS_RE.findall(anno.arguments_text)]
    if not raw_types:
        raw_types = [p.type_text for p in m.parameters
                     if _bare(p.type_text).endswith(("Exception", "Error", "Throwable"))]
    for raw in raw_types:
        tgt, conf, _ = resolver.resolve(raw, pf)
        graph.ensure_placeholder(tgt, "Type", _bare(raw))
        graph.add_edge(Edge("HANDLES", f"method:{m.fqn}", tgt, conf,
                            {"file": pf.relative_path,
                             "line_start": m.location.line_start, "raw": raw}))


def _node(graph: CodeGraph, node_id: str):
    return graph.get(node_id)
