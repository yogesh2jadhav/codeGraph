"""A tiny in-memory background job runner for the local API.

Scans and LLM calls can take from seconds to minutes - the UI submits them as
jobs and polls ``GET /api/jobs/{id}`` instead of holding an HTTP request open.
Single-process, single-machine, no queue broker: this is a local tool for one
user, not a distributed system.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from code_memory.logging_setup import get_logger

log = get_logger("api.jobs")


@dataclass
class Job:
    id: str
    kind: str
    status: str = "running"          # running | done | error
    result: Any = None
    error: str | None = None
    created_at: float = field(default_factory=time.time)
    finished_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "kind": self.kind, "status": self.status,
                "result": self.result, "error": self.error,
                "created_at": self.created_at, "finished_at": self.finished_at}


class JobRunner:
    """Runs one job per repo project at a time to avoid concurrent scans
    stepping on the same SQLite metadata DB / graph files."""

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def submit(self, kind: str, fn: Callable[[], Any]) -> str:
        job = Job(id=uuid.uuid4().hex, kind=kind)
        with self._lock:
            self._jobs[job.id] = job

        def run():
            try:
                job.result = fn()
                job.status = "done"
            except Exception as exc:  # pragma: no cover - defensive
                job.error = str(exc)
                job.status = "error"
                log.error(f"{kind} job failed", extra={"job_id": job.id,
                                                        "error": str(exc)})
            finally:
                job.finished_at = time.time()

        threading.Thread(target=run, name=f"job-{kind}-{job.id[:8]}",
                         daemon=True).start()
        return job.id

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self) -> list[Job]:
        with self._lock:
            return sorted(self._jobs.values(), key=lambda j: j.created_at,
                          reverse=True)
