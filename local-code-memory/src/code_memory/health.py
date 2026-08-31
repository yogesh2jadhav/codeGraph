"""Environment / dependency health checks used by ``code-memory doctor``.

All network checks are plain TCP connects (no client libraries needed yet) and
are only attempted against hosts on the configured ``security.network_allowlist``
- consistent with the local-first, network-disabled-by-default rule.
"""

from __future__ import annotations

import socket
import sys
from dataclasses import dataclass
from urllib.parse import urlparse

from code_memory.config import Config


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    required: bool = False


def _host_port(raw: str, default_port: int) -> tuple[str, int]:
    if "://" not in raw:
        raw = "//" + raw
    parsed = urlparse(raw)
    return parsed.hostname or "localhost", parsed.port or default_port


def _allowed(cfg: Config, host: str, port: int) -> bool:
    allow = set(cfg.get("security.network_allowlist", []))
    return f"{host}:{port}" in allow


def _tcp_probe(host: str, port: int, timeout: float = 1.5) -> tuple[bool, str]:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, f"reachable at {host}:{port}"
    except OSError as exc:
        return False, f"{host}:{port} - {exc.__class__.__name__}: {exc}"


def run_health_checks(cfg: Config) -> list[Check]:
    checks: list[Check] = []

    # -- python -----------------------------------------------------
    v = sys.version_info
    checks.append(Check(
        "python", v >= (3, 11),
        f"{v.major}.{v.minor}.{v.micro}", required=True,
    ))

    # -- config ---------------------------------------------------
    checks.append(Check(
        "config", True,
        f"loaded from: {', '.join(cfg.sources) or 'defaults only'}",
        required=True,
    ))

    # -- project root --------------------------------------------
    root = cfg.project_root
    checks.append(Check(
        "project_root", root.is_dir(),
        str(root) + ("" if root.is_dir() else " (missing)"),
    ))

    # -- metadata db is writable -------------------------------
    db = cfg.metadata_db
    try:
        db.parent.mkdir(parents=True, exist_ok=True)
        probe = db.parent / ".cm_write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        checks.append(Check("metadata_db_writable", True, str(db), required=True))
    except OSError as exc:
        checks.append(Check("metadata_db_writable", False,
                            f"{db}: {exc}", required=True))

    # -- optional local services --------------------------------
    services = {
        "neo4j": (cfg.get("graph.uri", "bolt://localhost:7687"), 7687),
        "qdrant": (cfg.get("vector.url", "http://localhost:6333"), 6333),
        "ollama": (cfg.get("llm.base_url", "http://localhost:11434"), 11434),
    }
    for name, (raw, default_port) in services.items():
        host, port = _host_port(raw, default_port)
        if not _allowed(cfg, host, port):
            checks.append(Check(name, False,
                                f"{host}:{port} not on network allowlist - skipped"))
            continue
        ok, detail = _tcp_probe(host, port)
        checks.append(Check(name, ok, detail))

    return checks
