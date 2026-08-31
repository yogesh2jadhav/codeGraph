"""Find SQL text embedded in Java source.

Sources handled:
  * ``@Query("...")`` / ``@Query(value = "...", nativeQuery = true)``  (Spring Data)
  * String literals and ``+`` concatenation runs that start like a SQL statement
    (covers JDBC ``prepareStatement("...")``, ``jdbcTemplate.query("...")``,
    ``spark.sql("...")`` etc. without needing to model every API)
  * Java text blocks (``\"\"\" ... \"\"\"``)

This is a heuristic scanner over raw text - deliberately separate from the
tree-sitter parser (PLAN.md section 14: "SQL parsing should be separated into
its own module"). It returns ``SqlHit`` records with a 1-based line number so
callers can attribute each statement to the enclosing method.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_STRING_RUN = re.compile(r'"(?:\\.|[^"\\])*"(?:\s*\+\s*"(?:\\.|[^"\\])*")*', re.S)
_TEXT_BLOCK = re.compile(r'"""(.*?)"""', re.S)
_QUERY_ANNO = re.compile(
    r'@Query\s*\(\s*(?:value\s*=\s*)?'
    r'("""(?:.*?)"""|"(?:\\.|[^"\\])*"(?:\s*\+\s*"(?:\\.|[^"\\])*")*)'
    r'(?P<rest>[^)]*)\)',
    re.S,
)
_STRING_PIECE = re.compile(r'"((?:\\.|[^"\\])*)"')
_SQL_START = re.compile(
    r'^\s*(?:--[^\n]*\n|\s)*'
    r'(WITH|SELECT|INSERT\s+INTO|INSERT|UPDATE|DELETE\s+FROM|DELETE|MERGE\s+INTO|'
    r'REPLACE\s+INTO|CREATE\s+(?:TABLE|OR\s+REPLACE|VIEW|TEMPORARY|GLOBAL)|'
    r'DROP\s+TABLE|TRUNCATE\s+TABLE|TRUNCATE|ALTER\s+TABLE)\b',
    re.I | re.S,
)
_ESCAPES = {r"\n": "\n", r"\t": "\t", r"\r": "\r", r'\"': '"', r"\'": "'",
            r"\\": "\\", r"\b": "\b", r"\f": "\f"}


@dataclass
class SqlHit:
    sql: str
    line: int
    kind: str            # "jpa-query" | "jpa-query-native" | "java-literal"


def looks_like_sql(text: str) -> bool:
    return bool(_SQL_START.match(text or ""))


def _unescape(s: str) -> str:
    for k, v in _ESCAPES.items():
        s = s.replace(k, v)
    return s


def _decode_run(run_text: str) -> str:
    if run_text.startswith('"""'):
        return run_text.strip('"').strip()
    parts = _STRING_PIECE.findall(run_text)
    return _unescape(" ".join(p.strip() for p in parts)).strip()


def _line_of(source: str, index: int) -> int:
    return source.count("\n", 0, index) + 1


def extract_sql_from_java(source: str) -> list[SqlHit]:
    hits: list[SqlHit] = []
    spans: list[tuple[int, int]] = []       # consumed @Query spans

    for m in _QUERY_ANNO.finditer(source):
        spans.append(m.span())
        sql = _decode_run(m.group(1))
        native = bool(re.search(r"nativeQuery\s*=\s*true", m.group("rest")))
        if sql:
            hits.append(SqlHit(sql, _line_of(source, m.start()),
                               "jpa-query-native" if native else "jpa-query"))

    def _in_query(pos: int) -> bool:
        return any(a <= pos < b for a, b in spans)

    for m in _TEXT_BLOCK.finditer(source):
        if _in_query(m.start()):
            continue
        body = m.group(1).strip()
        if looks_like_sql(body):
            hits.append(SqlHit(body, _line_of(source, m.start()), "java-literal"))

    for m in _STRING_RUN.finditer(source):
        if _in_query(m.start()):
            continue
        decoded = _decode_run(m.group(0))
        if looks_like_sql(decoded):
            hits.append(SqlHit(decoded, _line_of(source, m.start()),
                               "java-literal"))

    return hits


def split_sql_file(text: str) -> list[tuple[str, int]]:
    """Split a .sql file into (statement, line) pairs on top-level ';'."""
    out: list[tuple[str, int]] = []
    buf: list[str] = []
    start_line = 1
    line = 1
    for ch in text:
        if ch == "\n":
            line += 1
        if ch == ";":
            stmt = "".join(buf).strip()
            if stmt:
                out.append((stmt, start_line))
            buf = []
            start_line = line
        else:
            buf.append(ch)
    tail = "".join(buf).strip()
    if tail:
        out.append((tail, start_line))

    cleaned: list[tuple[str, int]] = []
    for stmt, ln in out:
        # strip leading full-line -- comments
        lines = stmt.splitlines()
        while lines and lines[0].lstrip().startswith("--"):
            lines.pop(0)
            ln += 1
        body = "\n".join(lines).strip()
        if body:
            cleaned.append((body, ln))
    return cleaned
