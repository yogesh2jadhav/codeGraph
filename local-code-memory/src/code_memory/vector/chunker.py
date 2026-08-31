"""Turn the code graph + parsed files into embeddable chunks.

We do **not** embed whole files (PLAN.md section 4.5). One chunk per meaningful
entity - class, method/constructor, endpoint, SQL statement - each a short
natural-language-ish description plus a source snippet, with metadata that
points back at the graph node and the file:line.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from code_memory.models.code import ParsedFile
from code_memory.models.graph import CodeGraph

_MAX_SNIPPET_LINES = 40


@dataclass
class Chunk:
    id: str
    text: str
    node_id: str
    kind: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "text": self.text, "node_id": self.node_id,
                "kind": self.kind, "metadata": self.metadata}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Chunk":
        return cls(d["id"], d["text"], d["node_id"], d["kind"],
                   d.get("metadata", {}))


def _snippet(root: Path, rel: str, line_start: int, line_end: int) -> str:
    try:
        lines = (root / rel).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    lo = max(0, line_start - 1)
    hi = min(len(lines), lo + _MAX_SNIPPET_LINES, line_end or line_start)
    return "\n".join(lines[lo:hi])


def build_chunks(graph: CodeGraph, parsed: list[ParsedFile],
                 root: Path) -> list[Chunk]:
    chunks: list[Chunk] = []

    for node in graph.nodes:
        if node.properties.get("placeholder"):
            continue
        kind = node.kind
        loc = node.properties.get("location") or {}
        rel = loc.get("relative_path")
        ls, le = loc.get("line_start", 0), loc.get("line_end", 0)

        if kind in ("Class", "Interface", "Enum", "Record", "Annotation"):
            annos = ", ".join(node.properties.get("annotations", []))
            stereo = node.properties.get("spring_stereotype", "")
            head = (f"{kind} {node.properties.get('fqn', node.name)}. "
                    f"{('Spring ' + stereo + '. ') if stereo else ''}"
                    f"{('Annotations: ' + annos + '. ') if annos else ''}")
            body = _snippet(root, rel, ls, min(le, ls + 8)) if rel else ""
            chunks.append(Chunk(f"chunk:{node.id}", head + "\n" + body, node.id,
                                kind, _meta(node, rel, ls, le)))

        elif kind in ("Method", "Constructor"):
            sig = node.properties.get("signature", node.name)
            owner = node.properties.get("owner", "")
            ret = node.properties.get("return_type") or ""
            annos = ", ".join(node.properties.get("annotations", []))
            spark = " Spark job." if node.properties.get("spark_job") else ""
            head = (f"{kind} {owner}#{sig}"
                    f"{(' returns ' + ret) if ret else ''}."
                    f"{(' Annotations: ' + annos + '.') if annos else ''}{spark}")
            body = _snippet(root, rel, ls, le) if rel else ""
            chunks.append(Chunk(f"chunk:{node.id}", head + "\n" + body, node.id,
                                kind, _meta(node, rel, ls, le)))

        elif kind == "Endpoint":
            p = node.properties
            text = (f"HTTP endpoint {p.get('http_method')} {p.get('path')} "
                    f"handled by {p.get('handler')} in controller "
                    f"{p.get('controller')}.")
            chunks.append(Chunk(f"chunk:{node.id}", text, node.id, kind,
                                _meta(node, rel, ls, le)))

        elif kind == "SQLStatement":
            p = node.properties
            text = (f"SQL {p.get('statement_type')} statement. "
                    f"reads tables: {', '.join(p.get('tables_read', [])) or '-'}. "
                    f"writes tables: {', '.join(p.get('tables_written', [])) or '-'}.\n"
                    f"{p.get('text', '')}")
            chunks.append(Chunk(f"chunk:{node.id}", text, node.id, kind,
                                {"tables_read": p.get("tables_read", []),
                                 "tables_written": p.get("tables_written", [])}))

    return chunks


def _meta(node, rel, ls, le) -> dict[str, Any]:
    return {
        "fqn": node.properties.get("fqn"),
        "name": node.name,
        "file": rel,
        "line_start": ls,
        "line_end": le,
        "owner": node.properties.get("owner"),
    }
