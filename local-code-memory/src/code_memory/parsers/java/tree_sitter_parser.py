"""Thin singleton wrapper around the tree-sitter Java grammar.

Isolated here so the rest of the codebase never imports tree-sitter directly and
a different parser backend could be swapped in. ``java_available()`` lets the
scanner degrade gracefully (inventory still works) if the optional dependency
is missing.
"""

from __future__ import annotations

import threading
from functools import lru_cache
from typing import Any

_lock = threading.Lock()


@lru_cache(maxsize=1)
def _load() -> Any:
    from tree_sitter import Language, Parser
    import tree_sitter_java

    language = Language(tree_sitter_java.language())
    # tree-sitter 0.22+ takes the language in the constructor.
    return Parser(language)


def java_available() -> bool:
    try:
        _load()
        return True
    except Exception:  # ImportError, ABI mismatch, ...
        return False


def parse_bytes(source: bytes):
    """Return a tree-sitter ``Tree`` for the given source bytes."""
    parser = _load()
    with _lock:  # tree-sitter Parser objects are not thread-safe
        return parser.parse(source)


def node_text(source: bytes, node) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8", "replace")


def has_errors(root) -> bool:
    """True if the parse tree contains ERROR or MISSING nodes."""
    if not root.has_error:
        return False
    stack = [root]
    while stack:
        n = stack.pop()
        if n.is_error or n.is_missing:
            return True
        stack.extend(n.children)
    return True
