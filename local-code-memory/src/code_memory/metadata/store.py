"""SQLite-backed metadata store.

Keeps everything that is *not* the code graph or the vector index:
  * scan records (id, timestamps, versions, git commit, status, stats)
  * file inventory + hashes (for incremental scanning)
  * warnings / errors captured during a scan
  * generated-artifact manifest

Deliberately independent of Neo4j and Qdrant. PostgreSQL can be added later
behind the same method surface.
"""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scans (
    scan_id          TEXT PRIMARY KEY,
    project_root     TEXT NOT NULL,
    started_at       REAL NOT NULL,
    finished_at      REAL,
    status           TEXT NOT NULL DEFAULT 'running',   -- running|success|partial|failed
    mode             TEXT NOT NULL DEFAULT 'full',      -- full|incremental|rebuild
    scanner_version  TEXT NOT NULL,
    schema_version   TEXT NOT NULL,
    git_commit       TEXT,
    git_branch       TEXT,
    embedding_model  TEXT,
    llm_model        TEXT,
    stats_json       TEXT
);

CREATE TABLE IF NOT EXISTS files (
    relative_path    TEXT PRIMARY KEY,
    kind             TEXT NOT NULL,
    size_bytes       INTEGER NOT NULL,
    sha256           TEXT NOT NULL,
    lines            INTEGER,
    scanner_version  TEXT NOT NULL,
    last_scan_id     TEXT NOT NULL,
    last_scanned_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS scan_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id    TEXT NOT NULL,
    level      TEXT NOT NULL,          -- warning|error
    phase      TEXT,
    path       TEXT,
    message    TEXT NOT NULL,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS artifacts (
    scan_id      TEXT NOT NULL,
    path         TEXT NOT NULL,
    kind         TEXT NOT NULL,        -- markdown|json
    bytes        INTEGER NOT NULL,
    created_at   REAL NOT NULL,
    PRIMARY KEY (scan_id, path)
);
"""

CURRENT_META = {"schema_version": "1"}


class MetadataStore:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    # -- lifecycle -------------------------------------------------------
    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.executescript(SCHEMA)
            for key, value in CURRENT_META.items():
                conn.execute(
                    "INSERT INTO schema_meta(key, value) VALUES(?, ?) "
                    "ON CONFLICT(key) DO NOTHING",
                    (key, value),
                )

    # -- scans ---------------------------------------------------------
    def start_scan(self, *, scan_id: str, project_root: str, mode: str,
                   scanner_version: str, schema_version: str,
                   git_commit: str | None, git_branch: str | None,
                   embedding_model: str | None = None,
                   llm_model: str | None = None) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO scans(scan_id, project_root, started_at, "
                "status, mode, scanner_version, schema_version, git_commit, "
                "git_branch, embedding_model, llm_model) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (scan_id, project_root, time.time(), "running", mode,
                 scanner_version, schema_version, git_commit, git_branch,
                 embedding_model, llm_model),
            )

    def finish_scan(self, scan_id: str, status: str,
                    stats: dict[str, Any] | None = None) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE scans SET finished_at = ?, status = ?, stats_json = ? "
                "WHERE scan_id = ?",
                (time.time(), status, json.dumps(stats or {}), scan_id),
            )

    def get_scan(self, scan_id: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM scans WHERE scan_id = ?", (scan_id,)
            ).fetchone()
        return dict(row) if row else None

    def latest_scan(self) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM scans ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None

    # -- files (incremental scan support) ----------------------------
    def known_file_hashes(self) -> dict[str, str]:
        """Return {relative_path: sha256} from the previous scan."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT relative_path, sha256 FROM files"
            ).fetchall()
        return {r["relative_path"]: r["sha256"] for r in rows}

    def upsert_files(self, entries: list[dict[str, Any]], *, scan_id: str,
                     scanner_version: str) -> None:
        now = time.time()
        with self._conn() as conn:
            conn.executemany(
                "INSERT INTO files(relative_path, kind, size_bytes, sha256, "
                "lines, scanner_version, last_scan_id, last_scanned_at) "
                "VALUES(:relative_path,:kind,:size_bytes,:sha256,:lines,"
                ":scanner_version,:scan_id,:now) "
                "ON CONFLICT(relative_path) DO UPDATE SET "
                "kind=excluded.kind, size_bytes=excluded.size_bytes, "
                "sha256=excluded.sha256, lines=excluded.lines, "
                "scanner_version=excluded.scanner_version, "
                "last_scan_id=excluded.last_scan_id, "
                "last_scanned_at=excluded.last_scanned_at",
                [
                    {**e, "scanner_version": scanner_version,
                     "scan_id": scan_id, "now": now}
                    for e in entries
                ],
            )

    def delete_files(self, relative_paths: list[str]) -> None:
        if not relative_paths:
            return
        with self._conn() as conn:
            conn.executemany(
                "DELETE FROM files WHERE relative_path = ?",
                [(p,) for p in relative_paths],
            )

    # -- events ------------------------------------------------------
    def record_event(self, scan_id: str, level: str, message: str,
                     phase: str | None = None, path: str | None = None) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO scan_events(scan_id, level, phase, path, message, "
                "created_at) VALUES(?,?,?,?,?,?)",
                (scan_id, level, phase, path, message, time.time()),
            )

    def events(self, scan_id: str) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM scan_events WHERE scan_id = ? ORDER BY id",
                (scan_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    # -- artifacts -------------------------------------------------
    def record_artifact(self, scan_id: str, path: str, kind: str,
                        num_bytes: int) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO artifacts(scan_id, path, kind, bytes, "
                "created_at) VALUES(?,?,?,?,?)",
                (scan_id, path, kind, num_bytes, time.time()),
            )

    # -- stats -----------------------------------------------------
    def summary(self) -> dict[str, Any]:
        with self._conn() as conn:
            scans = conn.execute("SELECT COUNT(*) AS c FROM scans").fetchone()["c"]
            files = conn.execute("SELECT COUNT(*) AS c FROM files").fetchone()["c"]
            latest = conn.execute(
                "SELECT * FROM scans ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
        return {
            "db_path": str(self.db_path),
            "scan_count": scans,
            "tracked_files": files,
            "latest_scan": dict(latest) if latest else None,
        }
