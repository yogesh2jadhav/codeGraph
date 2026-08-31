"""Walk a tree-sitter Java parse tree into :mod:`code_memory.models.code` objects.

Design notes / edge cases handled:
  * Multiple declarators in one field statement (``int a, b;``) -> one FieldDecl each.
  * ``spread_parameter`` (varargs) -> Parameter(varargs=True).
  * Nested / member types recurse with a dotted FQN (``Outer.Inner``).
  * Interface constants (``constant_declaration``) treated as fields.
  * Enum constants are emitted as fields with modifier ``["enum-constant"]``.
  * A file that fails to decode or parse yields ParsedFile(status=FAILED) with
    the reason in ``errors`` - the scan continues.
  * ERROR/MISSING nodes anywhere -> status PARTIAL (best-effort entities kept).
"""

from __future__ import annotations

import re

from code_memory.models.code import (
    Annotation,
    EntityKind,
    FieldDecl,
    ImportDecl,
    MethodDecl,
    Parameter,
    ParsedFile,
    ParseStatus,
    SourceLocation,
    TypeDecl,
)
from code_memory.parsers.java.tree_sitter_parser import (
    has_errors,
    node_text,
    parse_bytes,
)

_TYPE_DECL_NODES = {
    "class_declaration": EntityKind.CLASS,
    "interface_declaration": EntityKind.INTERFACE,
    "enum_declaration": EntityKind.ENUM,
    "record_declaration": EntityKind.RECORD,
    "annotation_type_declaration": EntityKind.ANNOTATION,
}
_BODY_NODES = {"class_body", "interface_body", "enum_body", "annotation_type_body"}
_MODIFIER_KEYWORDS = {
    "public", "private", "protected", "static", "final", "abstract",
    "synchronized", "native", "transient", "volatile", "strictfp", "default",
    "sealed", "non-sealed",
}
_WS_RE = re.compile(r"\s+")


def parse_java_source(relative_path: str, source: bytes) -> ParsedFile:
    try:
        tree = parse_bytes(source)
    except Exception as exc:  # pragma: no cover - parser should not raise
        return ParsedFile(relative_path, ParseStatus.FAILED,
                          errors=[f"tree-sitter failed: {exc}"])

    root = tree.root_node
    status = ParseStatus.PARTIAL if has_errors(root) else ParseStatus.SUCCESS
    pf = ParsedFile(relative_path, status)

    for child in root.children:
        if child.type == "package_declaration":
            pf.package = _dotted_name(source, child)
        elif child.type == "import_declaration":
            imp = _import(source, child)
            if imp:
                pf.imports.append(imp)
        elif child.type in _TYPE_DECL_NODES:
            pf.types.append(_type_decl(source, child, relative_path,
                                       pf.package, enclosing_fqn=None))
    return pf


# -- helpers -------------------------------------------------------------
def _loc(node, rel: str) -> SourceLocation:
    return SourceLocation(
        relative_path=rel,
        line_start=node.start_point[0] + 1,
        line_end=node.end_point[0] + 1,
        byte_start=node.start_byte,
        byte_end=node.end_byte,
    )


def _norm(text: str) -> str:
    return _WS_RE.sub(" ", text).strip()


def _dotted_name(source: bytes, node) -> str | None:
    """Text of the identifier / scoped_identifier inside a package decl."""
    for c in node.children:
        if c.type in ("scoped_identifier", "identifier"):
            return node_text(source, c)
    return None


def _import(source: bytes, node) -> ImportDecl | None:
    static = any(c.type == "static" for c in node.children)
    wildcard = any(c.type == "asterisk" for c in node.children)
    fqn = None
    for c in node.children:
        if c.type in ("scoped_identifier", "identifier"):
            fqn = node_text(source, c)
    if not fqn:
        return None
    return ImportDecl(fqn=fqn, static=static, wildcard=wildcard)


def _modifiers_and_annotations(source: bytes, node):
    """Return (modifier_keywords, [Annotation]) from an optional `modifiers`."""
    mods: list[str] = []
    annos: list[Annotation] = []
    mod_node = next((c for c in node.children if c.type == "modifiers"), None)
    if mod_node is None:
        return mods, annos
    for c in mod_node.children:
        if c.type in _MODIFIER_KEYWORDS or c.type in ("public", "private",
                                                      "protected"):
            mods.append(c.type)
        elif c.type == "marker_annotation":
            name = c.child_by_field_name("name")
            if name is not None:
                annos.append(Annotation(node_text(source, name)))
        elif c.type == "annotation":
            name = c.child_by_field_name("name")
            args = c.child_by_field_name("arguments")
            annos.append(Annotation(
                node_text(source, name) if name is not None else "?",
                _norm(node_text(source, args)) if args is not None else None,
            ))
    return mods, annos


def _type_params(source: bytes, node) -> list[str]:
    tp = node.child_by_field_name("type_parameters")
    if tp is None:
        return []
    return [node_text(source, c) for c in tp.children
            if c.type == "type_parameter"]


def _supertypes(source: bytes, node) -> tuple[list[str], list[str]]:
    """(extends[], implements[]) from a type declaration."""
    extends: list[str] = []
    implements: list[str] = []
    for c in node.children:
        if c.type == "superclass":                 # class extends X
            extends += _type_names(source, c)
        elif c.type == "super_interfaces":          # class implements ...
            implements += _type_names(source, c)
        elif c.type == "extends_interfaces":        # interface extends ...
            extends += _type_names(source, c)
        elif c.type == "permits":                   # sealed permits ... (ignore)
            pass
    return extends, implements


def _type_names(source: bytes, container) -> list[str]:
    out: list[str] = []
    for c in container.children:
        if c.type == "type_list":
            out += [node_text(source, t) for t in c.children
                    if t.type not in (",",)]
        elif c.type.endswith("type_identifier") or c.type in (
                "type_identifier", "generic_type", "scoped_type_identifier"):
            out.append(node_text(source, c))
    return [_norm(x) for x in out if x and x not in ("extends", "implements")]


def _type_decl(source: bytes, node, rel: str, package: str | None,
               enclosing_fqn: str | None) -> TypeDecl:
    name_node = node.child_by_field_name("name")
    name = node_text(source, name_node) if name_node is not None else "<anon>"
    base = enclosing_fqn or package
    fqn = f"{base}.{name}" if base else name

    mods, annos = _modifiers_and_annotations(source, node)
    extends, implements = _supertypes(source, node)

    td = TypeDecl(
        name=name,
        kind=_TYPE_DECL_NODES[node.type],
        fqn=fqn,
        location=_loc(node, rel),
        package=package,
        enclosing_fqn=enclosing_fqn,
        modifiers=mods,
        annotations=annos,
        type_parameters=_type_params(source, node),
        extends=extends,
        implements=implements,
    )

    # record components (record Point(int x, int y)) count as fields.
    params_node = node.child_by_field_name("parameters")
    if params_node is not None and node.type == "record_declaration":
        for p in _parameters(source, params_node):
            td.fields.append(FieldDecl(
                name=p.name, owner_fqn=fqn, fqn=f"{fqn}#{p.name}",
                location=td.location, type_text=p.type_text,
                modifiers=["record-component"],
            ))

    body = next((c for c in node.children if c.type in _BODY_NODES), None)
    if body is not None:
        _members(source, body, td, rel)
    return td


def _members(source: bytes, body, td: TypeDecl, rel: str) -> None:
    for c in body.children:
        if c.type == "field_declaration" or c.type == "constant_declaration":
            td.fields.extend(_fields(source, c, td.fqn, rel))
        elif c.type == "method_declaration":
            td.methods.append(_method(source, c, td.fqn, rel,
                                      EntityKind.METHOD))
        elif c.type in ("constructor_declaration", "compact_constructor_declaration"):
            td.methods.append(_method(source, c, td.fqn, rel,
                                      EntityKind.CONSTRUCTOR))
        elif c.type == "enum_constant":
            nm = c.child_by_field_name("name")
            if nm is not None:
                name = node_text(source, nm)
                td.fields.append(FieldDecl(
                    name=name, owner_fqn=td.fqn, fqn=f"{td.fqn}#{name}",
                    location=_loc(c, rel), type_text=td.name,
                    modifiers=["enum-constant"],
                ))
        elif c.type in _TYPE_DECL_NODES:
            td.nested.append(_type_decl(source, c, rel, td.package,
                                        enclosing_fqn=td.fqn))


def _fields(source: bytes, node, owner_fqn: str, rel: str) -> list[FieldDecl]:
    mods, annos = _modifiers_and_annotations(source, node)
    type_node = node.child_by_field_name("type")
    type_text = _norm(node_text(source, type_node)) if type_node is not None else None
    out: list[FieldDecl] = []
    for c in node.children:
        if c.type != "variable_declarator":
            continue
        nm = c.child_by_field_name("name")
        if nm is None:
            continue
        name = node_text(source, nm)
        out.append(FieldDecl(
            name=name, owner_fqn=owner_fqn, fqn=f"{owner_fqn}#{name}",
            location=_loc(c, rel), type_text=type_text,
            modifiers=list(mods), annotations=list(annos),
        ))
    return out


def _method(source: bytes, node, owner_fqn: str, rel: str,
            kind: EntityKind) -> MethodDecl:
    mods, annos = _modifiers_and_annotations(source, node)
    name_node = node.child_by_field_name("name")
    name = node_text(source, name_node) if name_node is not None else "<init>"

    ret_node = node.child_by_field_name("type")
    return_type = None
    if kind == EntityKind.METHOD and ret_node is not None:
        return_type = _norm(node_text(source, ret_node))

    params_node = node.child_by_field_name("parameters")
    params = _parameters(source, params_node) if params_node is not None else []

    throws: list[str] = []
    throws_node = next((c for c in node.children if c.type == "throws"), None)
    if throws_node is not None:
        throws = [_norm(node_text(source, t)) for t in throws_node.children
                  if t.type not in ("throws", ",")]

    param_types = ",".join(p.type_text for p in params)
    fqn = f"{owner_fqn}#{name}({param_types})"
    return MethodDecl(
        name=name, kind=kind, owner_fqn=owner_fqn, fqn=fqn,
        location=_loc(node, rel), return_type=return_type, parameters=params,
        throws=throws, modifiers=mods, annotations=annos,
        type_parameters=_type_params(source, node),
    )


def _parameters(source: bytes, params_node) -> list[Parameter]:
    out: list[Parameter] = []
    idx = 0
    for c in params_node.children:
        if c.type == "formal_parameter":
            t = c.child_by_field_name("type")
            n = c.child_by_field_name("name")
            out.append(Parameter(
                name=node_text(source, n) if n is not None else f"arg{idx}",
                type_text=_norm(node_text(source, t)) if t is not None else "?",
                index=idx,
            ))
            idx += 1
        elif c.type == "spread_parameter":
            # <type> ... <variable_declarator name>
            type_node = next((x for x in c.children
                              if x.type not in ("...", "variable_declarator")), None)
            vd = next((x for x in c.children if x.type == "variable_declarator"), None)
            nm = vd.child_by_field_name("name") if vd is not None else None
            out.append(Parameter(
                name=node_text(source, nm) if nm is not None else f"arg{idx}",
                type_text=(_norm(node_text(source, type_node)) + "...")
                if type_node is not None else "?...",
                index=idx, varargs=True,
            ))
            idx += 1
    return out
