"""Turn parsed Java files into a normalized :class:`CodeGraph`.

Phase 2 emits declaration structure (types / methods / fields) and the
declaration relationships (CONTAINS / DECLARES / EXTENDS / IMPLEMENTS / IMPORTS /
ANNOTATED_WITH / THROWS).

Phase 3 adds a syntactic **call graph** and the reference relationships
(CALLS / OVERRIDES / CREATES / CATCHES / USES_TYPE / RETURNS_TYPE) by
re-walking each method's body references and resolving them with local
heuristics only (no cross-file type system).

Every edge carries a confidence bucket so a consumer never mistakes an inferred
relationship for a proven one:

  HIGH    - target declared in this scan, resolved unambiguously
  MEDIUM  - resolved via a single heuristic (unique name, import, java.lang)
  LOW     - resolved but ambiguous (overload set) - all candidates linked
  UNKNOWN - not resolvable; a marked placeholder node is created

Placeholder nodes are listed in ``reports/unresolved_symbols.md``.
"""

from __future__ import annotations

import re

from code_memory.models.code import (
    EntityKind,
    MethodDecl,
    ParsedFile,
    TypeDecl,
)
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
    "SafeVarargs", "StringBuilder", "IllegalArgumentException",
    "IllegalStateException", "NullPointerException", "Cloneable",
}
_PRIMITIVES = {
    "int", "long", "double", "float", "boolean", "char", "byte", "short",
    "void", "var", "",
}
_GENERIC_RE = re.compile(r"<.*>", re.DOTALL)


def _bare(type_text: str) -> str:
    """Strip generics, arrays, varargs and annotations from a type reference."""
    t = _GENERIC_RE.sub("", type_text or "")
    t = t.replace("[]", "").replace("...", "").strip()
    if "@" in t:
        t = t.split()[-1]
    return t.split()[-1] if t.split() else t


# ---------------------------------------------------------------------------
class _Resolver:
    """Resolves a raw type reference to a graph node id + confidence."""

    def __init__(self, parsed: list[ParsedFile]) -> None:
        self.by_fqn: dict[str, TypeDecl] = {}
        self.by_simple: dict[str, set[str]] = {}
        self.file_of: dict[str, ParsedFile] = {}
        for pf in parsed:
            for td in pf.all_types():
                self.by_fqn[td.fqn] = td
                self.by_simple.setdefault(td.name, set()).add(td.fqn)
                self.file_of[td.fqn] = pf

    def resolve(self, ref: str, pf: ParsedFile) -> tuple[str, Confidence, bool]:
        """Return (node_id, confidence, resolved_to_declared)."""
        name = _bare(ref)
        if not name:
            return "type:<unknown>", Confidence.UNKNOWN, False

        if "." in name:
            if name in self.by_fqn:
                return f"type:{name}", Confidence.HIGH, True
            return f"type:{name}", Confidence.MEDIUM, False

        simple = name

        for imp in pf.imports:
            if not imp.wildcard and imp.fqn.rsplit(".", 1)[-1] == simple:
                if imp.fqn in self.by_fqn:
                    return f"type:{imp.fqn}", Confidence.HIGH, True
                return f"type:{imp.fqn}", Confidence.MEDIUM, False

        if pf.package:
            cand = f"{pf.package}.{simple}"
            if cand in self.by_fqn:
                return f"type:{cand}", Confidence.HIGH, True

        matches = self.by_simple.get(simple)
        if matches and len(matches) == 1:
            return f"type:{next(iter(matches))}", Confidence.MEDIUM, True

        if simple in _JAVA_LANG:
            return f"type:java.lang.{simple}", Confidence.MEDIUM, False

        return f"type:{simple}", Confidence.UNKNOWN, False

    def resolve_decl(self, ref: str, pf: ParsedFile) -> TypeDecl | None:
        """Return the in-scan TypeDecl for a reference, or None."""
        node_id, _, declared = self.resolve(ref, pf)
        if not declared:
            return None
        return self.by_fqn.get(node_id[len("type:"):])


# ---------------------------------------------------------------------------
def build_graph(parsed: list[ParsedFile]) -> CodeGraph:
    graph = CodeGraph()
    resolver = _Resolver(parsed)

    # Pass 1: declarations + declaration relationships.
    method_ctx: list[tuple[ParsedFile, TypeDecl, MethodDecl]] = []
    for pf in parsed:
        file_id = f"file:{pf.relative_path}"
        graph.add_node(Node(file_id, "SourceFile", pf.relative_path, {
            "parse_status": pf.status.value, "package": pf.package,
            "type_count": sum(1 for _ in pf.all_types())}))

        if pf.package:
            pkg_id = f"package:{pf.package}"
            graph.add_node(Node(pkg_id, "Package", pf.package))
            graph.add_edge(Edge("CONTAINS", pkg_id, file_id))

        for imp in pf.imports:
            ref_id = f"type:{imp.fqn}" if not imp.wildcard else f"package:{imp.fqn}"
            graph.ensure_placeholder(
                ref_id, "Package" if imp.wildcard else "Type", imp.fqn)
            graph.add_edge(Edge("IMPORTS", file_id, ref_id, Confidence.HIGH,
                                {"file": pf.relative_path, "static": imp.static,
                                 "wildcard": imp.wildcard}))

        for top in pf.types:
            _emit_type(graph, resolver, pf, top, file_id, "DECLARES", method_ctx)

    # Pass 2: call graph + reference relationships.
    for pf, td, m in method_ctx:
        _emit_method_refs(graph, resolver, pf, td, m)

    return graph


def _emit_type(graph, resolver, pf, td, parent_id, parent_rel, method_ctx):
    type_id = f"type:{td.fqn}"
    loc = td.location.to_dict()
    graph.add_node(Node(type_id, _KIND_TO_LABEL[td.kind], td.name, {
        "fqn": td.fqn, "package": td.package, "modifiers": td.modifiers,
        "type_parameters": td.type_parameters,
        "annotations": [a.name for a in td.annotations],
        "location": loc, "resolved": True}))
    graph.add_edge(Edge(parent_rel, parent_id, type_id, Confidence.HIGH, {
        "file": pf.relative_path, "line_start": loc["line_start"],
        "line_end": loc["line_end"]}))

    ev = {"file": pf.relative_path, "line_start": loc["line_start"],
          "line_end": loc["line_end"]}
    for sup in td.extends:
        tgt, conf, _ = resolver.resolve(sup, pf)
        graph.ensure_placeholder(tgt, "Type", _bare(sup))
        graph.add_edge(Edge("EXTENDS", type_id, tgt, conf, {**ev, "raw": sup}))
    for iface in td.implements:
        tgt, conf, _ = resolver.resolve(iface, pf)
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
            "owner": fld.owner_fqn, "location": floc}))
        graph.add_edge(Edge("DECLARES", type_id, fid, Confidence.HIGH, {
            "file": pf.relative_path, "line_start": floc["line_start"],
            "line_end": floc["line_end"]}))
        fev = {"file": pf.relative_path, "line_start": floc["line_start"],
               "line_end": floc["line_end"]}
        for anno in fld.annotations:
            _annotated_with(graph, resolver, pf, fid, anno.name, fev)
        if fld.type_text and _bare(fld.type_text) not in _PRIMITIVES:
            tgt, conf, _ = resolver.resolve(fld.type_text, pf)
            graph.ensure_placeholder(tgt, "Type", _bare(fld.type_text))
            graph.add_edge(Edge("USES_TYPE", fid, tgt, conf,
                                {**fev, "raw": fld.type_text}))

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
            "throws": m.throws, "location": mloc}))
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
        method_ctx.append((pf, td, m))

    for nested in td.nested:
        _emit_type(graph, resolver, pf, nested, type_id, "DECLARES", method_ctx)


# -- Phase 3: reference / call resolution -----------------------------------
def _supertype_decls(resolver, pf, td, _seen=None):
    """Yield resolvable in-scan TypeDecls that ``td`` extends/implements."""
    seen = _seen if _seen is not None else set()
    for ref in list(td.extends) + list(td.implements):
        sup = resolver.resolve_decl(ref, resolver.file_of.get(td.fqn, pf))
        if sup is not None and sup.fqn not in seen:
            seen.add(sup.fqn)
            yield sup
            yield from _supertype_decls(resolver, resolver.file_of.get(sup.fqn, pf),
                                        sup, seen)


def _methods_named(resolver, pf, td, name):
    """(owner_td, MethodDecl) for every method called ``name`` on td or a super."""
    out = []
    for cand in [td, *_supertype_decls(resolver, pf, td)]:
        for m in cand.methods:
            if m.name == name:
                out.append((cand, m))
    return out


def _name_type_map(resolver, pf, td, m):
    """Local name -> declared type text, for the body of method ``m``."""
    names: dict[str, str] = {}
    for cand in [td, *_supertype_decls(resolver, pf, td)]:
        for f in cand.fields:
            if f.type_text:
                names.setdefault(f.name, f.type_text)
    for p in m.parameters:
        names[p.name] = p.type_text
    for ref in m.references:
        if ref.kind == "localvar" and ref.type_text:
            names[ref.name] = ref.type_text
    return names


def _emit_method_refs(graph, resolver, pf, td, m):
    mid = f"method:{m.fqn}"
    names = _name_type_map(resolver, pf, td, m)
    used_types: set[str] = set()

    def use_type(raw: str):
        if not raw or _bare(raw) in _PRIMITIVES or raw in used_types:
            return
        used_types.add(raw)
        tgt, conf, _ = resolver.resolve(raw, pf)
        graph.ensure_placeholder(tgt, "Type", _bare(raw))
        graph.add_edge(Edge("USES_TYPE", mid, tgt, conf, {
            "file": pf.relative_path, "line_start": m.location.line_start,
            "raw": raw}))

    # RETURNS_TYPE + param/localvar USES_TYPE
    if m.return_type and _bare(m.return_type) not in _PRIMITIVES:
        tgt, conf, _ = resolver.resolve(m.return_type, pf)
        graph.ensure_placeholder(tgt, "Type", _bare(m.return_type))
        graph.add_edge(Edge("RETURNS_TYPE", mid, tgt, conf, {
            "file": pf.relative_path, "line_start": m.location.line_start,
            "raw": m.return_type}))
    for p in m.parameters:
        use_type(p.type_text)

    # OVERRIDES - same name + arg count in a resolvable supertype
    has_override_anno = any(a.name.rsplit(".", 1)[-1] == "Override"
                            for a in m.annotations)
    for sup in _supertype_decls(resolver, pf, td):
        for sm in sup.methods:
            if sm.name == m.name and len(sm.parameters) == len(m.parameters) \
                    and sm.kind == m.kind and sup.fqn != td.fqn:
                graph.add_edge(Edge(
                    "OVERRIDES", mid, f"method:{sm.fqn}",
                    Confidence.HIGH if has_override_anno else Confidence.MEDIUM,
                    {"file": pf.relative_path,
                     "line_start": m.location.line_start}))

    for ref in m.references:
        if ref.kind == "localvar":
            use_type(ref.type_text)
        elif ref.kind == "catch":
            for part in (ref.type_text or "").split("|"):
                part = part.strip()
                if not part:
                    continue
                tgt, conf, _ = resolver.resolve(part, pf)
                graph.ensure_placeholder(tgt, "Type", _bare(part))
                graph.add_edge(Edge("CATCHES", mid, tgt, conf, {
                    "file": pf.relative_path, "line_start": ref.line,
                    "raw": part}))
        elif ref.kind == "create":
            use_type(ref.type_text)
            tgt, conf, _ = resolver.resolve(ref.type_text or "", pf)
            graph.ensure_placeholder(tgt, "Type", _bare(ref.type_text or ""))
            graph.add_edge(Edge("CREATES", mid, tgt, conf, {
                "file": pf.relative_path, "line_start": ref.line,
                "raw": ref.type_text}))
            _link_constructor(graph, resolver, pf, mid, ref)
        elif ref.kind == "call":
            _resolve_call(graph, resolver, pf, td, m, mid, names, ref)


def _owner_for_call(resolver, pf, td, names, ref):
    """Return (owner_TypeDecl | None, owner_guess_text)."""
    recv = ref.receiver_text
    if recv in (None, "", "this"):
        return td, td.name
    if recv == "super":
        supers = list(_supertype_decls(resolver, pf, td))
        return (supers[0] if supers else None), "super"
    if recv in names:
        raw = names[recv]
        return resolver.resolve_decl(raw, pf), _bare(raw)
    # static call on a type name, e.g. Foo.bar()
    if recv and recv[:1].isupper() and "." not in recv and "(" not in recv:
        return resolver.resolve_decl(recv, pf), recv
    return None, (recv or "?")


def _resolve_call(graph, resolver, pf, td, m, mid, names, ref):
    owner, guess = _owner_for_call(resolver, pf, td, names, ref)
    ev = {"file": pf.relative_path, "line_start": ref.line,
          "call": f"{ref.receiver_text + '.' if ref.receiver_text else ''}"
                  f"{ref.name}({ref.arg_count})"}

    if owner is not None:
        cands = _methods_named(resolver, pf, owner, ref.name)
        exact = [c for c in cands if len(c[1].parameters) == (ref.arg_count or 0)]
        chosen, conf = [], Confidence.UNKNOWN
        if len(exact) == 1:
            chosen, conf = exact, Confidence.HIGH
        elif exact:
            chosen, conf = exact, Confidence.LOW
        elif len(cands) == 1:
            chosen, conf = cands, Confidence.MEDIUM
        elif cands:
            chosen, conf = cands, Confidence.LOW
        if chosen:
            for _owner_td, cm in chosen:
                graph.add_edge(Edge("CALLS", mid, f"method:{cm.fqn}", conf, ev))
            return

    # unresolved - collapse external calls onto one placeholder per owner+name
    ext_id = f"extmethod:{guess}.{ref.name}"
    graph.ensure_placeholder(ext_id, "Method", f"{guess}.{ref.name}")
    node = next(n for n in graph.nodes if n.id == ext_id)
    node.properties["external"] = True
    graph.add_edge(Edge("CALLS", mid, ext_id, Confidence.UNKNOWN, ev))


def _link_constructor(graph, resolver, pf, mid, ref):
    owner = resolver.resolve_decl(ref.type_text or "", pf)
    if owner is None:
        return
    ctors = [c for c in owner.methods if c.kind == EntityKind.CONSTRUCTOR]
    exact = [c for c in ctors if len(c.parameters) == (ref.arg_count or 0)]
    target = exact[0] if len(exact) == 1 else (ctors[0] if len(ctors) == 1 else None)
    if target is not None:
        graph.add_edge(Edge("CALLS", mid, f"method:{target.fqn}",
                            Confidence.MEDIUM if not exact else Confidence.HIGH,
                            {"file": pf.relative_path, "line_start": ref.line,
                             "call": f"new {ref.type_text}({ref.arg_count})"}))


def _annotated_with(graph, resolver, pf, src_id, anno_name, ev):
    tgt, conf, _ = resolver.resolve(anno_name, pf)
    graph.ensure_placeholder(tgt, "Annotation", _bare(anno_name))
    graph.add_edge(Edge("ANNOTATED_WITH", src_id, tgt, conf,
                        {**ev, "raw": anno_name}))
