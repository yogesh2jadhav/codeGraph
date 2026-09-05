"""Guard against the Windows encoding bug: ``Path.read_text()`` /
``Path.write_text()`` / ``open()`` without an explicit ``encoding=`` use the
platform's *locale* default encoding, not UTF-8. On macOS/Linux that default
is (almost always) UTF-8, so this never showed up in development - but on
Windows it's commonly cp1252, which corrupted every generated JSON/Markdown
file and then crashed reading them back (MemoryError deep inside the cp1252
codec on a large vector/index.json).

Every text file this project writes is UTF-8 (JSON, Markdown); every text file
it reads back was written by itself. So every read_text/write_text/open call
on those files must pass ``encoding="utf-8"`` explicitly - this test statically
verifies that across the whole package rather than relying on someone
remembering it (or a Mac/Linux-only CI run masking a Windows-only crash).
"""

import ast
from pathlib import Path

SRC = Path(__file__).parents[2] / "src" / "code_memory"
_TEXT_IO_NAMES = {"read_text", "write_text", "open"}


def _find_encoding_issues(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    issues: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = (node.func.attr if isinstance(node.func, ast.Attribute)
                else node.func.id if isinstance(node.func, ast.Name) else None)
        if name not in _TEXT_IO_NAMES:
            continue
        has_encoding = any(kw.arg == "encoding" for kw in node.keywords)
        is_binary_mode = name == "open" and any(
            isinstance(a, ast.Constant) and isinstance(a.value, str) and "b" in a.value
            for a in node.args)
        if not has_encoding and not is_binary_mode:
            issues.append((node.lineno, name))
    return issues


def test_no_text_io_without_explicit_encoding():
    offenders = {}
    for f in SRC.rglob("*.py"):
        issues = _find_encoding_issues(f)
        if issues:
            offenders[str(f.relative_to(SRC))] = issues
    assert not offenders, (
        "found read_text()/write_text()/open() calls with no explicit "
        f"encoding= (breaks on Windows, whose default isn't UTF-8): {offenders}")
