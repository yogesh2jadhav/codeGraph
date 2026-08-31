"""Build-file dependency parsing (Maven POM + Gradle build scripts)."""

from code_memory.analyzers.dependencies.build_parser import parse_build_files

__all__ = ["parse_build_files"]
