"""Shared helpers for context generation: token estimation, secret redaction,
lightweight config + logging scans."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

# ~4 chars/token is close enough for budgeting without a tokenizer dependency.
_CHARS_PER_TOKEN = 4

_DEFAULT_SECRET_MARKERS = (
    "password", "passwd", "secret", "token", "api_key", "apikey",
    "private_key", "privatekey", "credential", "access_key", "client_secret",
)
_LOG_RECEIVERS = {"log", "logger", "LOG", "LOGGER", "logging", "slf4jLogger"}
_LOG_LEVELS = {"trace", "debug", "info", "warn", "warning", "error", "fatal"}


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // _CHARS_PER_TOKEN)


def redact(value: str) -> str:
    return "***REDACTED***" if value else value


def is_secret_key(key: str, markers=_DEFAULT_SECRET_MARKERS) -> bool:
    low = key.lower()
    return any(m in low for m in markers)


# -- configuration properties -------------------------------------------
def scan_config_properties(root: Path, rel_paths: list[str],
                           markers=_DEFAULT_SECRET_MARKERS) -> list[dict[str, Any]]:
    """Flatten application.yml / .properties files to (file, key, value) rows,
    redacting anything that looks like a secret."""
    rows: list[dict[str, Any]] = []
    for rel in rel_paths:
        path = root / rel
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        pairs = (_flatten_yaml(text) if path.suffix in (".yml", ".yaml")
                 else _flatten_properties(text))
        for key, value in pairs:
            secret = is_secret_key(key, markers)
            rows.append({"file": rel, "key": key,
                         "value": redact(value) if secret else value,
                         "secret": secret})
    return rows


def _flatten_properties(text: str) -> list[tuple[str, str]]:
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "!")):
            continue
        if "=" in line:
            k, v = line.split("=", 1)
        elif ":" in line:
            k, v = line.split(":", 1)
        else:
            continue
        out.append((k.strip(), v.strip()))
    return out


def _flatten_yaml(text: str) -> list[tuple[str, str]]:
    try:
        import yaml
        data = yaml.safe_load(text)
    except Exception:
        return []
    out: list[tuple[str, str]] = []

    def walk(node, prefix):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, f"{prefix}.{k}" if prefix else str(k))
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{prefix}[{i}]")
        else:
            out.append((prefix, "" if node is None else str(node)))

    walk(data, "")
    return out


# -- logging statements ------------------------------------------------
def scan_logging(parsed_files) -> dict[str, Any]:
    """Count logging calls per level and per method from the Phase 3 call refs."""
    by_level: dict[str, int] = {}
    logged_methods: set[str] = set()
    method_total = 0
    interesting_unlogged: list[str] = []

    for pf in parsed_files:
        for td in pf.all_types():
            for m in td.methods:
                method_total += 1
                has_log = False
                for r in m.references:
                    if r.kind != "call":
                        continue
                    if r.receiver_text in _LOG_RECEIVERS and \
                            r.name.lower() in _LOG_LEVELS:
                        by_level[r.name.lower()] = by_level.get(r.name.lower(), 0) + 1
                        has_log = True
                if has_log:
                    logged_methods.add(m.fqn)
                elif _looks_interesting(m):
                    interesting_unlogged.append(m.fqn)

    return {
        "by_level": dict(sorted(by_level.items())),
        "logged_methods": sorted(logged_methods),
        "method_total": method_total,
        "candidates_without_logging": sorted(interesting_unlogged)[:80],
    }


def _looks_interesting(m) -> bool:
    """External call / DB / exception-path methods are logging candidates."""
    annos = {a.name.rsplit(".", 1)[-1] for a in m.annotations}
    if annos & {"GetMapping", "PostMapping", "PutMapping", "DeleteMapping",
                "RequestMapping", "Transactional", "Scheduled", "ExceptionHandler"}:
        return True
    if m.throws:
        return True
    names = {r.name for r in m.references if r.kind == "call"}
    if names & {"execute", "executeQuery", "executeUpdate", "query", "update",
                "save", "delete", "findAll", "sql", "prepareStatement", "call"}:
        return True
    return any(r.kind == "catch" for r in m.references)
