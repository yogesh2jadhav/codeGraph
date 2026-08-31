# Local Code Memory & Local LLM Coding Assistant

A fully local system that deeply scans a Java repository, builds a persistent
machine-readable **Code Memory** (graph + vector index + metadata), and produces
compact Markdown context packs for a local coding LLM (Ollama / Qwen3-Coder).

No cloud APIs. No source-code upload. See [PLAN.md](PLAN.md) for the full design.

## Status

| Phase | Scope | State |
| --- | --- | --- |
| 0 | Repo, config, structured logging, CLI skeleton, docker-compose, SQLite metadata, health checks | ✅ done |
| 1 | Repository inventory scanner (file classification, hashes, build/framework detection, `project_inventory.json`, `00_project_overview.md`) | ✅ done |
| 2+ | Java semantic scan, relationships, Spring/Spark/SQL analyzers, Neo4j, Qdrant, retrieval, context packs, Ollama | ⏳ planned |

## Quick start

```bash
# from the repo that contains this folder, using the shared .venv
../.venv/bin/pip install -e "local-code-memory[dev]"

cd local-code-memory
../.venv/bin/python -m code_memory.cli.main doctor
../.venv/bin/python -m code_memory.cli.main scan --project /path/to/java-project
```

Outputs land in `<java-project>/.code-memory/`:

```
.code-memory/
├── project_inventory.json
└── context/
    └── 00_project_overview.md
```

## Configuration

All behaviour is driven by [`config/application.yaml`](config/application.yaml).
Override any value with an env var, e.g.:

```bash
CODE_MEMORY__LLM__MODEL=qwen3-coder:14b code-memory scan
```

Machine-specific overrides go in `config/application.local.yaml` (git-ignored).

## Infrastructure

`docker compose up -d` starts Neo4j and Qdrant bound to `127.0.0.1` only.
Ollama runs directly on the host. None of these are required for Phase 0/1.

## Development

```bash
make install     # editable install + dev deps
make test        # pytest
make doctor      # environment health checks
```
