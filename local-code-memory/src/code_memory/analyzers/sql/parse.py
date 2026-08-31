"""Parse a single SQL statement into (type, tables_read, tables_written).

Uses sqlglot when available, with a regex fallback so a dialect sqlglot can't
handle still yields table names. Read/write classification:

  INSERT / REPLACE  -> target table is a write; tables in the SELECT are reads
  UPDATE / DELETE   -> target table is a write
  MERGE             -> target is a write, USING source is a read
  CREATE / DROP / TRUNCATE / ALTER (DDL) -> target is a write
  SELECT / WITH     -> every table is a read
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

try:  # sqlglot is a hard dependency, but stay import-safe
    import sqlglot
    from sqlglot import exp
    _SQLGLOT = True
except Exception:  # pragma: no cover
    _SQLGLOT = False

_TYPE_MAP = {
    "Select": "SELECT", "Union": "SELECT", "Insert": "INSERT",
    "Update": "UPDATE", "Delete": "DELETE", "Merge": "MERGE",
    "Create": "CREATE", "Drop": "DROP", "Alter": "ALTER",
    "TruncateTable": "TRUNCATE", "Command": "COMMAND",
}
_FALLBACK_TYPE = re.compile(
    r"^\s*(?:--[^\n]*\n|\s)*(WITH|SELECT|INSERT|UPDATE|DELETE|MERGE|REPLACE|"
    r"CREATE|DROP|TRUNCATE|ALTER)", re.I)
_FALLBACK_FROM = re.compile(
    r"\b(?:FROM|JOIN|INTO|UPDATE|TABLE)\s+([A-Za-z_][\w.]*)", re.I)
_FALLBACK_WRITE = re.compile(
    r"\b(?:INSERT\s+INTO|REPLACE\s+INTO|UPDATE|DELETE\s+FROM|MERGE\s+INTO|"
    r"CREATE\s+TABLE|DROP\s+TABLE|TRUNCATE\s+TABLE|ALTER\s+TABLE|INTO)\s+"
    r"([A-Za-z_][\w.]*)", re.I)


@dataclass
class SqlParse:
    statement_type: str
    tables_read: list[str] = field(default_factory=list)
    tables_written: list[str] = field(default_factory=list)
    parsed_ok: bool = False


def _table_names(node) -> set[str]:
    out: set[str] = set()
    for t in node.find_all(exp.Table):
        if not t.name:
            continue
        parts = [p for p in (t.catalog, t.db, t.name) if p]
        out.add(".".join(parts).lower())
    return out


# JPA positional (?1) / named (:name) / MyBatis (#{x}) params -> plain ? so
# sqlglot can parse the statement structurally.
_PARAM_RE = re.compile(r"\?\d+|:[A-Za-z_]\w*|#\{[^}]+\}|\$\{[^}]+\}")


def parse_sql(text: str, dialects: tuple[str, ...] = ("", "spark", "mysql",
                                                     "postgres", "tsql")) -> SqlParse:
    normalized = _PARAM_RE.sub("?", text)
    if _SQLGLOT:
        for dialect in dialects:
            try:
                tree = sqlglot.parse_one(normalized, read=dialect or None)
            except Exception:
                continue
            if tree is None:
                continue
            return _from_sqlglot(tree)
    return _fallback(text)


def _from_sqlglot(tree) -> SqlParse:
    kind = _TYPE_MAP.get(type(tree).__name__, type(tree).__name__.upper())
    all_tables = _table_names(tree)
    writes: set[str] = set()

    if isinstance(tree, exp.Insert):
        tgt = tree.this
        if tgt is not None:
            writes |= _table_names(tgt)
    elif isinstance(tree, (exp.Update, exp.Delete)):
        if tree.this is not None:
            writes |= _table_names(tree.this)
    elif isinstance(tree, exp.Merge):
        if tree.this is not None:
            writes |= _table_names(tree.this)
    elif isinstance(tree, (exp.Create, exp.Drop, exp.Alter)):
        if tree.this is not None:
            writes |= _table_names(tree.this)
    elif type(tree).__name__ == "TruncateTable":
        writes |= all_tables

    reads = all_tables - writes
    return SqlParse(kind, sorted(reads), sorted(writes), parsed_ok=True)


def _fallback(text: str) -> SqlParse:
    tm = _FALLBACK_TYPE.match(text)
    kind = (tm.group(1).upper() if tm else "UNKNOWN")
    if kind == "WITH":
        kind = "SELECT"
    writes = {m.group(1).lower() for m in _FALLBACK_WRITE.finditer(text)}
    everything = {m.group(1).lower() for m in _FALLBACK_FROM.finditer(text)}
    reads = everything - writes
    return SqlParse(kind, sorted(reads), sorted(writes), parsed_ok=False)
