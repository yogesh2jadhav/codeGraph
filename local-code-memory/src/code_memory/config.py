"""Configuration loading.

Precedence (lowest -> highest):
  1. Built-in defaults (this module)
  2. config/application.yaml (or an explicit --config path)
  3. config/application.local.yaml sitting next to the loaded file
  4. Environment variables prefixed CODE_MEMORY__ with "__" as nesting separator

The result is exposed as a plain nested dict plus a small typed accessor
(`Config`) so callers do not sprinkle string keys everywhere.
"""

from __future__ import annotations

import copy
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

ENV_PREFIX = "CODE_MEMORY__"

DEFAULTS: dict[str, Any] = {
    "project": {"root": ".", "output_dir": ".code-memory"},
    "storage": {"metadata": "./data/metadata.db"},
    "graph": {
        "provider": "neo4j",
        "uri": "bolt://localhost:7687",
        "username": "neo4j",
        "password": "neo4j",
        "database": "neo4j",
    },
    "vector": {
        "provider": "qdrant",
        "url": "http://localhost:6333",
        "collection": "code_memory",
    },
    "llm": {
        "provider": "ollama",
        "base_url": "http://localhost:11434",
        "model": "qwen3-coder:30b",
        "context_window": 32768,
        "temperature": 0.1,
    },
    "embedding": {"provider": "local", "model": "configurable"},
    "scanner": {
        "incremental": True,
        "include_tests": True,
        "include_resources": True,
        "max_file_size_mb": 10,
        "exclude_dirs": [
            ".git", ".idea", ".code-memory", "target", "build", "out",
            "node_modules", ".gradle", ".mvn",
        ],
    },
    "context": {
        "max_tokens": 24000,
        "max_files": 30,
        "max_methods": 50,
        "max_graph_hops": 3,
        "max_vector_results": 30,
        "rerank_results": 10,
    },
    "security": {
        "network_allowlist": [
            "127.0.0.1:11434", "127.0.0.1:6333", "127.0.0.1:7687",
            "localhost:11434", "localhost:6333", "localhost:7687",
        ],
        "secret_markers": [
            "password", "secret", "token", "api_key", "apikey",
            "private_key", "credential",
        ],
    },
    "logging": {
        "level": "INFO",
        "console": True,
        "structured": True,
        "file": {"enabled": True, "path": "./logs/code-memory.log"},
        "levels": {},
    },
}


def _deep_merge(base: dict, overlay: dict) -> dict:
    """Recursively merge overlay into a copy of base (overlay wins)."""
    out = copy.deepcopy(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _coerce(raw: str) -> Any:
    """Best-effort typing of an env-var string (bool/int/float/yaml-list)."""
    low = raw.lower()
    if low in ("true", "false"):
        return low == "true"
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        pass
    if raw.startswith("[") or raw.startswith("{"):
        try:
            return yaml.safe_load(raw)
        except yaml.YAMLError:
            return raw
    return raw


def _env_overlay(environ: dict[str, str]) -> dict:
    """Turn CODE_MEMORY__A__B=val into {'a': {'b': val}}."""
    overlay: dict[str, Any] = {}
    for key, value in environ.items():
        if not key.startswith(ENV_PREFIX):
            continue
        path = [p.lower() for p in key[len(ENV_PREFIX):].split("__") if p]
        if not path:
            continue
        node = overlay
        for part in path[:-1]:
            node = node.setdefault(part, {})
        node[path[-1]] = _coerce(value)
    return overlay


def load_config(config_path: str | os.PathLike | None = None,
                environ: dict[str, str] | None = None) -> "Config":
    """Load and merge configuration from all sources."""
    environ = dict(os.environ if environ is None else environ)
    data = copy.deepcopy(DEFAULTS)

    path = Path(config_path) if config_path else _default_config_path()
    loaded_from: list[str] = []
    if path and path.is_file():
        with path.open("r", encoding="utf-8") as fh:
            file_data = yaml.safe_load(fh) or {}
        data = _deep_merge(data, file_data)
        loaded_from.append(str(path))

        local = path.with_name(path.stem + ".local" + path.suffix)
        if local.is_file():
            with local.open("r", encoding="utf-8") as fh:
                data = _deep_merge(data, yaml.safe_load(fh) or {})
            loaded_from.append(str(local))

    data = _deep_merge(data, _env_overlay(environ))
    return Config(data=data, sources=loaded_from,
                  base_dir=path.parent.parent if path else Path.cwd())


def _default_config_path() -> Path | None:
    """Look for config/application.yaml near CWD or the package root."""
    candidates = [
        Path.cwd() / "config" / "application.yaml",
        Path(__file__).resolve().parents[2] / "config" / "application.yaml",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[0]


@dataclass
class Config:
    """Typed-ish accessor over the merged configuration dict."""

    data: dict[str, Any]
    sources: list[str]
    base_dir: Path

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self.data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def __getitem__(self, key: str) -> Any:
        return self.data[key]

    # -- convenience resolved paths -----------------------------------------
    def resolve_path(self, raw: str) -> Path:
        """Resolve a possibly-relative path against CWD."""
        p = Path(raw).expanduser()
        return p if p.is_absolute() else (Path.cwd() / p).resolve()

    @property
    def project_root(self) -> Path:
        return self.resolve_path(self.get("project.root", "."))

    @property
    def output_dir(self) -> Path:
        return self.project_root / self.get("project.output_dir", ".code-memory")

    @property
    def metadata_db(self) -> Path:
        return self.resolve_path(self.get("storage.metadata", "./data/metadata.db"))
