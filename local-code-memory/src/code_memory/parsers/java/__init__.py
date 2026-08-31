"""Java parsing via tree-sitter (structure extraction only for now).

Rationale: tree-sitter is pip-installable, needs no JVM, is error-tolerant
(recovers from a broken method to keep parsing the rest of the file), and is
fast enough for repo-scale scans. Cross-file symbol resolution and call/data
flow are added later (Joern is the planned engine for that).
"""

from code_memory.parsers.java.extractor import parse_java_source
from code_memory.parsers.java.tree_sitter_parser import java_available

__all__ = ["parse_java_source", "java_available"]
