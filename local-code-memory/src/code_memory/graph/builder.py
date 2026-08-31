"""Turn parsed Java files into a normalized :class:`CodeGraph`.

Type-reference resolution (for EXTENDS / IMPLEMENTS / THROWS / ANNOTATED_WITH)
is best-effort and every edge is tagged with a confidence bucket:

  HIGH    - target is a type declared in this scan (exact FQN, or via an
            explicit import, or same-package)
  MEDIUM  - resolved to an external FQN via an explicit import, java.lang.*,
            or a unique simple-name match among declared types
  UNKNOWN - could not resolve; a placeholder node is created

Placeholder nodes are listed in ``reports/unresolved_symbols.md``.
"""

from __future__ import annotations

import re

from code_memory.models.code import EntityKind, ParsedFile, TypeDecl
from code_memory.models.graph import CodeGraph, Confidence, Edge, Node

_KIND_TO_LABEL = {
    EntityKind.CLASS: "Class",
    EntityKind.INTERFACE: "Interface",
    EntityKind.ENUM: "Enum",
    EntityKind.RECORD: "Record",
    EntityKind.ANNOTATION: "Annotation",
}
_JAVA_LANG = {
    "String", "Object", "Integer", "Long", "Double", "Float", "Boolean",
    "Byte", "Short", "Character", "Number", "Void", "Math", "System",
    "Thread", "Runnable", "Exception", "RuntimeException", "Error",
    "Throwable", "Iterable", "Comparable", "CharSequence", "Class",
    "Override", "Deprecated", "SuppressWarnings", "FunctionalInterface",
    "SafeVarargs",
}
_GENERIC_RE = re.compile(r"<.*>", re.DOTALL)


def _bare(type_text: str) -> str:
    """Strip generics, array brackets and annotations from a type reference."""
    t = _GENERIC_RE.sub("", type_text)
    t = t.replace("[]", "").replace("...", "").strip()
    t = t.split("@")[-1].strip()          # drop leading annotations
    return t.split()[-1] if t.split() else t


class _Resolver:
    def __init__(self, parsed: list[ParsedFile]) -> None:
        self.by_fqn: dict[str, TypeDecl] = {}
        self.by_simple: dict[str, set[str]] = {}
        for pf in parsed:
            for td in pf.all_types():
                self.by_fqn[td.fqn] = td
                self.by_simple.setdefault(td.name, set()).add(td.fqn)

    def resolve(self, ref: str, pf: ParsedFile) -> tuple[str, Confidence, bool]:
        """Return (node_id, confidence, resolved_to_declared)."""
        name = _bare(ref)
        if not name:
            return "type:<unknown>", Confidence.UNKNOWN, False

        if "." in name:
            if name in self.by_fqn:
                return f"type:{name}", Confidence.HIGH, True
            return f"type:{name}", Confidence.MEDIUM, False

        simple = name.rsplit(".", 1)[-1]

        # explicit import  ->  import ... .Simple ;
        for imp in pf.imports:
            if not imp.wildcard and imp.fqn.rsplit(".", 1)[-1] == simple:
                if imp.fqn in self.by_fqn:
                    return f"type:{imp.fqn}", Confidence.HIGH, True
                return f"type:{imp.fqn}", Confidence.MEDIUM, False

        # same package
        if pf.package:
            cand = f"{pf.package}.{simple}"
            if cand in self.by_fqn:
                return f"type:{cand}", Confidence.HIGH, True

        # unique simple-name match anywhere in the scan
        matches = self.by_simple.get(simple)
        if matches and len(matches) == 1:
            return f"type:{next(iter(matches))}", Confidence.MEDIUM, True

        if simple in _JAVA_LANG:
            return f"type:java.lang.{simple}", Confidence.MEDIUM, False

        return f"type:{simple}", Confidence.UNKNOWN, False


def build_graph(parsed: list[ParsedFile]) -> CodeGraph:
    graph = CodeGraph()
    resolver = _Resolver(parsed)

    for pf in parsed:
        file_id = f"file:{pf.relative_path}"
        graph.add_node(Node(file_id, "SourceFile", pf.relative_path,
                            {"parse_status": pf.status.value,
                             "package": pf.package,
                             "type_count": sum(1 for _ in pf.all_types())}))

        if pf.package:
            pkg_id = f"package:{pf.package}"
            graph.add_node(Node(pkg_id, "Package", pf.package))
            graph.add_edge(Edge("CONTAINS", pkg_id, file_id))

        ev_file = {"file": pf.relative_path}
        for imp in pf.imports:
            ref_id = f"type:{imp.fqn}" if not imp.wildcard else f"package:{imp.fqn}"
            graph.ensure_placeholder(
                ref_id, "Package" if imp.wildcard else "Type", imp.fqn)
            graph.add_edge(Edge("IMPORTS", file_id, ref_id,
                                Confidence.HIGH,
                                {**ev_file, "static": imp.static,
                                 "wildcard": imp.wildcard}))

        for top in pf.types:
            _emit_type(graph, resolver, pf, top, parent_id=file_id,
                       parent_rel="DECLARES")

    return graph


def _emit_type(graph: CodeGraph, resolver: _Resolver, pf: ParsedFile,
               td: TypeDecl, parent_id: str, parent_rel: str) -> None:
    type_id = f"type:{td.fqn}"
    loc = td.location.to_dict()
    graph.add_node(Node(type_id, _KIND_TO_LABEL[td.kind], td.name, {
        "fqn": td.fqn,
        "package": td.package,
        "modifiers": td.modifiers,
        "type_parameters": td.type_parameters,
        "annotations": [a.name for a in td.annotations],
        "location": loc,
        "resolved": True,
    }))
    graph.add_edge(Edge(parent_rel, parent_id, type_id, Confidence.HIGH,
                        {"file": pf.relative_path,
                         "line_start": loc["line_start"],
                         "line_end": loc["line_end"]}))

    ev = {"file": pf.relative_path, "line_start": loc["line_start"],
          "line_end": loc["line_end"]}

    for sup in td.extends:
        tgt, conf, declared = resolver.resolve(sup, pf)
        graph.ensure_placeholder(tgt, "Type", _bare(sup))
        graph.add_edge(Edge("EXTENDS", type_id, tgt, conf, {**ev, "raw": sup}))
    for iface in td.implements:
        tgt, conf, declared = resolver.resolve(iface, pf)
        graph.ensure_placeholder(tgt, "Type", _bare(iface))
        graph.add_edge(Edge("IMPLEMENTS", type_id, tgt, conf, {**ev, "raw": iface}))
    for anno in td.annotations:
        _annotated_with(graph, resolver, pf, type_id, anno.name, ev)

    for fld in td.fields:
        fid = f"field:{fld.fqn}"
        floc = fld.location.to_dict()
        graph.add_node(Node(fid, "Field", fld.name, {
            "fqn": fld.fqn, "type": fld.type_text, "modifiers": fld.modifiers,
            "annotations": [a.name for a in fld.annotations],
            "owner": fld.owner_fqn, "location": floc,
        }))
        graph.add_edge(Edge("DECLARES", type_id, fid, Confidence.HIGH, {
            "file": pf.relative_path, "line_start": floc["line_start"],
            "line_end": floc["line_end"]}))
        for anno in fld.annotations:
            _annotated_with(graph, resolver, pf, fid, anno.name, {
                "file": pf.relative_path, "line_start": floc["line_start"],
                "line_end": floc["line_end"]})

    for m in td.methods:
        mid = f"method:{m.fqn}"
        mloc = m.location.to_dict()
        graph.add_node(Node(mid, "Constructor" if m.kind == EntityKind.CONSTRUCTOR
                            else "Method", m.name, {
            "fqn": m.fqn, "signature": m.signature, "owner": m.owner_fqn,
            "return_type": m.return_type,
            "parameters": [p.to_dict() for p in m.parameters],
            "modifiers": m.modifiers,
            "annotations": [a.name for a in m.annotations],
            "throws": m.throws, "location": mloc,
        }))
        graph.add_edge(Edge("DECLARES", type_id, mid, Confidence.HIGH, {
            "file": pf.relative_path, "line_start": mloc["line_start"],
            "line_end": mloc["line_end"]}))
        mev = {"file": pf.relative_path, "line_start": mloc["line_start"],
               "line_end": mloc["line_end"]}
        for anno in m.annotations:
            _annotated_with(graph, resolver, pf, mid, anno.name, mev)
        for thrown in m.throws:
            tgt, conf, _ = resolver.resolve(thrown, pf)
            graph.ensure_placeholder(tgt, "Type", _bare(thrown))
            graph.add_edge(Edge("THROWS", mid, tgt, conf, {**mev, "raw": thrown}))

    for nested in td.nested:
        _emit_type(graph, resolver, pf, nested, parent_id=type_id,
                   parent_rel="DECLARES")


def _annotated_with(graph: CodeGraph, resolver: _Resolver, pf: ParsedFile,
                    src_id: str, anno_name: str, ev: dict) -> None:
    tgt, conf, _ = resolver.resolve(anno_name, pf)
    graph.ensure_placeholder(tgt, "Annotation", _bare(anno_name))
    graph.add_edge(Edge("ANNOTATED_WITH", src_id, tgt, conf,
                        {**ev, "raw": anno_name}))
