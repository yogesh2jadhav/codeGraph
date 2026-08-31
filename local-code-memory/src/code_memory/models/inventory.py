"""Data models produced by Phase 1 (repository inventory)."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any


class FileKind(str, Enum):
    """Classification of a file discovered during inventory."""

    JAVA_MAIN = "java_main"
    JAVA_TEST = "java_test"
    SCALA = "scala"
    RESOURCE = "resource"
    TEST_RESOURCE = "test_resource"
    MAVEN_POM = "maven_pom"
    GRADLE_BUILD = "gradle_build"
    GRADLE_SETTINGS = "gradle_settings"
    GRADLE_PROPERTIES = "gradle_properties"
    APP_CONFIG = "app_config"          # application.yml / .properties (+ profiles)
    SPARK_CONFIG = "spark_config"
    SQL = "sql"
    DOCKER = "docker"
    README = "readme"
    DOC = "doc"
    SCRIPT = "script"
    BUILD_OTHER = "build_other"
    OTHER = "other"


@dataclass
class FileEntry:
    """One discovered file with enough metadata for incremental scanning."""

    relative_path: str
    kind: FileKind
    size_bytes: int
    sha256: str
    lines: int | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["kind"] = self.kind.value
        return d


@dataclass
class BuildInfo:
    """Facts inferred from build files. Every field is best-effort / optional."""

    build_system: str = "unknown"          # maven | gradle | mixed | unknown
    java_version: str | None = None
    spring_boot_version: str | None = None
    spark_version: str | None = None
    scala_version: str | None = None
    group_id: str | None = None
    artifact_id: str | None = None
    version: str | None = None
    database_drivers: list[str] = field(default_factory=list)
    logging_frameworks: list[str] = field(default_factory=list)
    testing_frameworks: list[str] = field(default_factory=list)
    dependencies: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ProjectInventory:
    """Complete Phase 1 output for a scanned repository."""

    project_root: str
    scan_id: str
    scanner_version: str
    generated_at: str
    git_commit: str | None
    git_branch: str | None
    files: list[FileEntry] = field(default_factory=list)
    build: BuildInfo = field(default_factory=BuildInfo)
    warnings: list[str] = field(default_factory=list)

    # -- derived helpers ---------------------------------------------------
    def counts_by_kind(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for entry in self.files:
            out[entry.kind.value] = out.get(entry.kind.value, 0) + 1
        return dict(sorted(out.items()))

    def total_java_loc(self) -> int:
        return sum(
            (e.lines or 0)
            for e in self.files
            if e.kind in (FileKind.JAVA_MAIN, FileKind.JAVA_TEST)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_root": self.project_root,
            "scan_id": self.scan_id,
            "scanner_version": self.scanner_version,
            "generated_at": self.generated_at,
            "git_commit": self.git_commit,
            "git_branch": self.git_branch,
            "build": self.build.to_dict(),
            "counts_by_kind": self.counts_by_kind(),
            "file_count": len(self.files),
            "java_loc": self.total_java_loc(),
            "files": [e.to_dict() for e in self.files],
            "warnings": self.warnings,
        }
