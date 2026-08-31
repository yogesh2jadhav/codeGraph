"""Scan orchestration: wires the inventory scanner to the metadata store.

Later phases (semantic scan, graph build, embeddings, markdown pack) hang off
the same :class:`ScanContext` so correlation ids and the metadata record are
shared across the whole run.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from code_memory import SCANNER_VERSION, SCHEMA_VERSION
from code_memory.config import Config
from code_memory.logging_setup import bind, get_logger, unbind
from code_memory.metadata import MetadataStore
from code_memory.scanner import InventoryScanner, ScanResult

log = get_logger("pipeline")


@dataclass
class ScanContext:
    scan_id: str
    mode: str
    config: Config
    store: MetadataStore


def run_scan(config: Config, *, mode: str = "full", semantic: bool = True,
             index: bool = True) -> tuple[ScanContext, ScanResult]:
    """Run inventory (+ Java semantic scan + graph/vector indexing).

    ``mode`` is ``full`` | ``incremental`` | ``rebuild``. For ``incremental`` the
    previous scan's file hashes are diffed so callers can act on just the delta.
    ``semantic`` controls the Java semantic scan / graph build; ``index``
    controls Phase 7/8 persistence (graph repository + vector index).
    """
    scan_id = uuid.uuid4().hex
    token = bind(scan_id=scan_id)
    try:
        store = MetadataStore(config.metadata_db)
        scanner = InventoryScanner(config)

        previous = store.known_file_hashes() if mode == "incremental" else {}

        # A rebuild wipes tracked file state so every file counts as "added".
        if mode == "rebuild":
            store.delete_files(list(store.known_file_hashes()))

        commit, branch = _peek_git(config)
        store.start_scan(
            scan_id=scan_id, project_root=str(config.project_root), mode=mode,
            scanner_version=SCANNER_VERSION, schema_version=SCHEMA_VERSION,
            git_commit=commit, git_branch=branch,
            embedding_model=config.get("embedding.model"),
            llm_model=config.get("llm.model"),
        )

        try:
            result = scanner.scan(scan_id=scan_id, previous_hashes=previous)
        except Exception as exc:
            store.record_event(scan_id, "error", f"scan aborted: {exc}",
                               phase="inventory")
            store.finish_scan(scan_id, "failed")
            log.error("scan failed", extra={"error": str(exc)})
            raise

        inv = result.inventory
        store.upsert_files(
            [e.to_dict() | {"relative_path": e.relative_path} for e in inv.files],
            scan_id=scan_id, scanner_version=SCANNER_VERSION,
        )
        if mode in ("incremental",):
            store.delete_files(result.deleted)

        for w in inv.warnings:
            store.record_event(scan_id, "warning", w, phase="inventory")

        # -- Phase 2: Java semantic scan + graph build --------------------
        java_result = None
        if semantic:
            from code_memory.scanner import JavaSemanticScanner

            try:
                java_result = JavaSemanticScanner(config).scan(inv)
                for s in java_result.skipped:
                    store.record_event(scan_id, "warning", s, phase="java")
            except Exception as exc:  # never let Phase 2 kill a scan
                store.record_event(scan_id, "error",
                                   f"java semantic scan failed: {exc}",
                                   phase="java")
                log.error("java semantic scan failed", extra={"error": str(exc)})

        # -- Phase 7/8: graph repository + vector index -----------------
        if java_result and index and java_result.graph.nodes:
            try:
                from code_memory.graph.repository import get_graph_repository

                get_graph_repository(config).replace_graph(java_result.graph)
            except Exception as exc:
                store.record_event(scan_id, "warning",
                                   f"graph persistence failed: {exc}", phase="graph")
                log.warning("graph persistence failed", extra={"error": str(exc)})
            try:
                from code_memory.embeddings import get_embedding_provider
                from code_memory.vector import build_vector_index, get_vector_store

                emb = get_embedding_provider(config)
                vstore = get_vector_store(config, dim=emb.dim,
                                          embedding_name=emb.name)
                vstats = build_vector_index(
                    java_result.graph, java_result.parsed_files,
                    config.project_root, emb, vstore)
                result.vector = vstats
            except Exception as exc:
                store.record_event(scan_id, "warning",
                                   f"vector index failed: {exc}", phase="vector")
                log.warning("vector index failed", extra={"error": str(exc)})

            # -- Phase 10: full markdown context pack --------------------
            try:
                from code_memory.context import generate_context_pack

                pack = generate_context_pack(config, java_result, inv, scan_id)
                result.context_pack = [str(p) for p in pack]
            except Exception as exc:
                store.record_event(scan_id, "warning",
                                   f"context pack failed: {exc}", phase="context")
                log.warning("context pack generation failed",
                            extra={"error": str(exc)})

        all_artifacts = list(result.artifacts)
        if java_result:
            all_artifacts += java_result.artifacts
            result.java = java_result
        for art in all_artifacts:
            try:
                store.record_artifact(scan_id, str(art),
                                      "json" if art.suffix == ".json" else "markdown",
                                      art.stat().st_size)
            except OSError:
                pass

        stats = {
            "file_count": len(inv.files),
            "java_loc": inv.total_java_loc(),
            "counts_by_kind": inv.counts_by_kind(),
            "added": len(result.added),
            "modified": len(result.modified),
            "deleted": len(result.deleted),
            "unchanged": result.unchanged,
            "warnings": len(inv.warnings),
            "duration_ms": result.duration_ms,
            "build_system": inv.build.build_system,
        }
        if java_result:
            stats["java"] = java_result.stats()
        if getattr(result, "vector", None):
            stats["vector"] = result.vector
        if getattr(result, "context_pack", None):
            stats["context_pack_files"] = len(result.context_pack)
        status = "partial" if inv.warnings else "success"
        store.finish_scan(scan_id, status, stats)

        return ScanContext(scan_id, mode, config, store), result
    finally:
        unbind(token)


def _peek_git(config: Config) -> tuple[str | None, str | None]:
    from code_memory.scanner.gitinfo import read_git_info

    return read_git_info(config.project_root)
