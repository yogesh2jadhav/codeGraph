"""Turn a user-typed string into a graph node id.

Shared by the CLI (``impact``/``graph``) and the local API so both accept the
same forms: an exact node id, an FQN, or a bare name substring.
"""

from __future__ import annotations

from code_memory.graph.repository import GraphRepository


def resolve_symbol(repo: GraphRepository, term: str) -> str | None:
    if repo.get_node(term):
        return term
    for prefix in ("method:", "type:", "field:", "endpoint:", "table:"):
        if repo.get_node(prefix + term):
            return prefix + term
    matches = repo.find_nodes(name_contains=term)
    exact = [n for n in matches if n.get("fqn") == term or n["name"] == term]
    pool = exact or matches
    pool.sort(key=lambda n: len(n["id"]))
    return pool[0]["id"] if pool else None
