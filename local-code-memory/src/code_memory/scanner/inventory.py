"""Phase 1 - repository inventory scanner.

Walks a Java repository, classifies every file, hashes it (for incremental
scans), infers build facts, and emits:

  * ``<output_dir>/project_inventory.json``  - machine-readable
  * ``<output_dir>/context/00_project_overview.md`` - LLM/human readable

One unreadable file never aborts the scan; it is recorded as a warning.
"""

from __future__ import annotations

import hashlib
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from code_memory import SCANNER_VERSION
from code_memory.analyzers.dependencies import parse_build_files
from code_memory.config import Config
from code_memory.logging_setup import get_logger
from code_memory.models.inventory import (
    BuildInfo,
    FileEntry,
    FileKind,
    ProjectInventory,
)
from code_memory.scanner.classify import classify, is_text_ext
from code_memory.scanner.gitinfo import read_git_info
from code_memory.scanner.overview import render_overview

log = get_logger("scanner.inventory")


@dataclass
class ScanResult:
    inventory: ProjectInventory
    added: list[str] = field(default_factory=list)
    modified: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    unchanged: int = 0
    duration_ms: int = 0
    artifacts: list[Path] = field(default_factory=list)
    java: "object | None" = None  # JavaScanResult, set by the pipeline (Phase 2)
    vector: "dict | None" = None  # vector index stats, set by the pipeline (Phase 8)

    @property
    def changed(self) -> bool:
        return bool(self.added or self.modified or self.deleted)


class InventoryScanner:
    def __init__(self, config: Config):
        self.config = config
        self.root = config.project_root
        scfg = config.get("scanner", {})
        self.exclude_dirs = set(scfg.get("exclude_dirs", []))
        self.max_bytes = int(scfg.get("max_file_size_mb", 10)) * 1024 * 1024
        self.include_tests = bool(scfg.get("include_tests", True))
        self.include_resources = bool(scfg.get("include_resources", True))

    # -- public API -------------------------------------------------
    def scan(self, *, scan_id: str | None = None,
             previous_hashes: dict[str, str] | None = None,
             write_artifacts: bool = True) -> ScanResult:
        if not self.root.is_dir():
            raise FileNotFoundError(f"project root does not exist: {self.root}")

        started = time.perf_counter()
        scan_id = scan_id or uuid.uuid4().hex
        previous_hashes = previous_hashes or {}
        warnings: list[str] = []

        log.info("inventory scan start", extra={"scan_id": scan_id,
                                                "root": str(self.root)})

        entries: list[FileEntry] = []
        pom_paths: list[Path] = []
        gradle_paths: list[Path] = []

        for path in self._walk():
            try:
                stat = path.stat()
            except OSError as exc:
                warnings.append(f"cannot stat {path}: {exc}")
                continue
            rel = path.relative_to(self.root).as_posix()

            if stat.st_size > self.max_bytes:
                warnings.append(f"skipped oversized file ({stat.st_size} B): {rel}")
                continue

            kind = classify(rel)
            if kind == FileKind.JAVA_TEST and not self.include_tests:
                continue
            if kind in (FileKind.RESOURCE, FileKind.TEST_RESOURCE) and \
                    not self.include_resources:
                continue

            try:
                digest, lines = self._hash_and_count(path, kind)
            except OSError as exc:
                warnings.append(f"cannot read {rel}: {exc}")
                continue

            entries.append(FileEntry(
                relative_path=rel, kind=kind, size_bytes=stat.st_size,
                sha256=digest, lines=lines,
            ))
            if kind == FileKind.MAVEN_POM:
                pom_paths.append(path)
            elif kind in (FileKind.GRADLE_BUILD, FileKind.GRADLE_SETTINGS,
                          FileKind.GRADLE_PROPERTIES):
                gradle_paths.append(path)

        entries.sort(key=lambda e: e.relative_path)

        build, build_warnings = parse_build_files(pom_paths, gradle_paths, self.root)
        warnings.extend(build_warnings)

        commit, branch = read_git_info(self.root)

        inventory = ProjectInventory(
            project_root=str(self.root),
            scan_id=scan_id,
            scanner_version=SCANNER_VERSION,
            generated_at=datetime.now(timezone.utc).isoformat(),
            git_commit=commit,
            git_branch=branch,
            files=entries,
            build=build,
            warnings=warnings,
        )

        result = self._diff(inventory, previous_hashes)
        result.duration_ms = int((time.perf_counter() - started) * 1000)

        if write_artifacts:
            result.artifacts = self._write_artifacts(inventory)

        log.info(
            "inventory scan done",
            extra={"scan_id": scan_id, "files": len(entries),
                   "added": len(result.added), "modified": len(result.modified),
                   "deleted": len(result.deleted),
                   "duration_ms": result.duration_ms,
                   "warnings": len(warnings)},
        )
        return result

    # -- internals ------------------------------------------------
    def _walk(self):
        """Yield files under root, pruning excluded directories."""
        stack = [self.root]
        while stack:
            current = stack.pop()
            try:
                children = list(current.iterdir())
            except OSError as exc:
                log.warning("cannot list dir", extra={"dir": str(current),
                                                      "error": str(exc)})
                continue
            for child in children:
                if child.is_symlink():
                    continue
                if child.is_dir():
                    if child.name not in self.exclude_dirs:
                        stack.append(child)
                elif child.is_file():
                    yield child

    def _hash_and_count(self, path: Path, kind: FileKind) -> tuple[str, int | None]:
        hasher = hashlib.sha256()
        count_lines = is_text_ext(path.name)
        newlines = 0
        with path.open("rb") as fh:
            while chunk := fh.read(65536):
                hasher.update(chunk)
                if count_lines:
                    newlines += chunk.count(b"\n")
        lines = (newlines + 1) if count_lines else None
        return hasher.hexdigest(), lines

    def _diff(self, inventory: ProjectInventory,
              previous: dict[str, str]) -> ScanResult:
        result = ScanResult(inventory=inventory)
        current = {e.relative_path: e.sha256 for e in inventory.files}
        for rel, digest in current.items():
            if rel not in previous:
                result.added.append(rel)
            elif previous[rel] != digest:
                result.modified.append(rel)
            else:
                result.unchanged += 1
        result.deleted = sorted(set(previous) - set(current))
        return result

    def _write_artifacts(self, inventory: ProjectInventory) -> list[Path]:
        import json

        out_dir = self.config.output_dir
        context_dir = out_dir / "context"
        out_dir.mkdir(parents=True, exist_ok=True)
        context_dir.mkdir(parents=True, exist_ok=True)

        json_path = out_dir / "project_inventory.json"
        json_path.write_text(
            json.dumps(inventory.to_dict(), indent=2, sort_keys=False) + "\n",
            encoding="utf-8",
        )

        overview_path = context_dir / "00_project_overview.md"
        overview_path.write_text(render_overview(inventory), encoding="utf-8")

        return [json_path, overview_path]
