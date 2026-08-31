"""Java code-entity models produced by the Phase 2 semantic scanner.

These are *syntactic* facts extracted from a single file with a tolerant parser.
Type references are kept as raw source text (e.g. ``"List<String>"``); real
symbol resolution across files is a later phase. Every entity carries a
:class:`SourceLocation` so downstream context can cite ``file:line``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class EntityKind(str, Enum):
    CLASS = "class"
    INTERFACE = "interface"
    ENUM = "enum"
    RECORD = "record"
    ANNOTATION = "annotation"
    METHOD = "method"
    CONSTRUCTOR = "constructor"
    FIELD = "field"


class ParseStatus(str, Enum):
    SUCCESS = "success"       # parsed with no error nodes
    PARTIAL = "partial"       # parsed but the tree contains ERROR/MISSING nodes
    FAILED = "failed"         # could not parse at all (I/O, decode)


@dataclass
class SourceLocation:
    relative_path: str
    line_start: int
    line_end: int
    byte_start: int = 0
    byte_end: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Annotation:
    name: str                       # simple or qualified as written
    arguments_text: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Parameter:
    name: str
    type_text: str
    index: int
    varargs: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MethodDecl:
    name: str
    kind: EntityKind                # METHOD or CONSTRUCTOR
    owner_fqn: str
    fqn: str                        # owner_fqn + "#" + signature
    location: SourceLocation
    return_type: str | None = None
    parameters: list[Parameter] = field(default_factory=list)
    throws: list[str] = field(default_factory=list)
    modifiers: list[str] = field(default_factory=list)
    annotations: list[Annotation] = field(default_factory=list)
    type_parameters: list[str] = field(default_factory=list)

    @property
    def signature(self) -> str:
        params = ",".join(p.type_text for p in self.parameters)
        return f"{self.name}({params})"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["kind"] = self.kind.value
        d["signature"] = self.signature
        return d


@dataclass
class FieldDecl:
    name: str
    owner_fqn: str
    fqn: str
    location: SourceLocation
    type_text: str | None = None
    modifiers: list[str] = field(default_factory=list)
    annotations: list[Annotation] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TypeDecl:
    name: str
    kind: EntityKind
    fqn: str                        # package + enclosing types + name
    location: SourceLocation
    package: str | None = None
    enclosing_fqn: str | None = None            # None for top-level types
    modifiers: list[str] = field(default_factory=list)
    annotations: list[Annotation] = field(default_factory=list)
    type_parameters: list[str] = field(default_factory=list)
    extends: list[str] = field(default_factory=list)     # raw type names
    implements: list[str] = field(default_factory=list)  # raw type names
    methods: list[MethodDecl] = field(default_factory=list)
    fields: list[FieldDecl] = field(default_factory=list)
    nested: list["TypeDecl"] = field(default_factory=list)

    def iter_types(self):
        """Yield this type and all nested types depth-first."""
        yield self
        for child in self.nested:
            yield from child.iter_types()

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind.value,
            "fqn": self.fqn,
            "package": self.package,
            "enclosing_fqn": self.enclosing_fqn,
            "location": self.location.to_dict(),
            "modifiers": self.modifiers,
            "annotations": [a.to_dict() for a in self.annotations],
            "type_parameters": self.type_parameters,
            "extends": self.extends,
            "implements": self.implements,
            "methods": [m.to_dict() for m in self.methods],
            "fields": [f.to_dict() for f in self.fields],
            "nested": [n.to_dict() for n in self.nested],
        }


@dataclass
class ImportDecl:
    fqn: str
    static: bool = False
    wildcard: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ParsedFile:
    relative_path: str
    status: ParseStatus
    package: str | None = None
    imports: list[ImportDecl] = field(default_factory=list)
    types: list[TypeDecl] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def all_types(self):
        for top in self.types:
            yield from top.iter_types()

    def to_dict(self) -> dict[str, Any]:
        return {
            "relative_path": self.relative_path,
            "status": self.status.value,
            "package": self.package,
            "imports": [i.to_dict() for i in self.imports],
            "types": [t.to_dict() for t in self.types],
            "errors": self.errors,
        }
