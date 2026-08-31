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
| 2 | Java semantic scan (tree-sitter): packages, types, methods, constructors, fields, params, annotations, imports, inheritance — with source locations — into a normalized code graph (`graph/nodes.json`, `graph/edges.json`) | ✅ done |
| 3 | Syntactic call graph + reference edges (`CALLS`, `OVERRIDES`, `CREATES`, `CATCHES`, `USES_TYPE`, `RETURNS_TYPE`), graph query API (callers/callees/paths), `context/07_call_graph.md` | ✅ done |
| 4 | Spring analyzer: stereotypes (`@RestController`/`@Service`/`@Repository`/…), HTTP endpoints (`Endpoint` nodes, `EXPOSES`/`MAPPED_TO`), DI (`INJECTS`), `@Bean`/`@ExceptionHandler` (`HANDLES`), endpoint→service→repository flow in `context/06_api_endpoints.md` | ✅ done |
| 5 | Spark analyzer: detects Spark jobs, transformations/actions, input/output tables & paths, `spark.sql` bridge → `context/12_spark.md` | ✅ done |
| 6 | SQL analyzer: SQL from `@Query`/string literals/text blocks/`.sql` files, parsed with sqlglot → `SQLStatement`/`Table` nodes, `EXECUTES_SQL`/`READS_TABLE`/`WRITES_TABLE`, `context/13_sql.md` | ✅ done |
| 7 | `GraphRepository` (in-memory default, optional Neo4j) + `code-memory impact` / `graph` | ✅ done |
| 8 | Vector index: entity chunker, local embeddings (hashing default, optional Ollama/sentence-transformers), in-memory store (optional Qdrant) | ✅ done |
| 9 | Hybrid retrieval (lexical + vector + symbol + graph expansion, RRF fusion) + reranker, `code-memory search` | ✅ done |
| 10+ | Markdown context pack, task context generator, Ollama coding advisor, git integration, incremental, patches | ⏳ planned |

## Quick start

```bash
# from the repo that contains this folder, using the shared .venv
../.venv/bin/pip install -e "local-code-memory[dev]"

cd local-code-memory
../.venv/bin/python -m code_memory doctor
../.venv/bin/python -m code_memory --project /path/to/java-project scan
```

Outputs land in `<java-project>/.code-memory/`:

```
.code-memory/
├── project_inventory.json
├── context/
│   ├── 00_project_overview.md
│   ├── 06_api_endpoints.md  # Spring: endpoints + controller→service→repository flow
│   ├── 07_call_graph.md     # per-method calls / called-by, with confidence
│   ├── 12_spark.md          # Spark jobs: transformations, actions, table/path I/O
│   └── 13_sql.md            # SQL statements + tables (read/write)
├── graph/
│   ├── nodes.json          # types, methods, fields, packages, files, endpoints, SQL, tables
│   ├── edges.json          # CONTAINS/DECLARES/EXTENDS/IMPLEMENTS/IMPORTS/ANNOTATED_WITH/THROWS
│   │                       #  + CALLS/OVERRIDES/CREATES/CATCHES/USES_TYPE/RETURNS_TYPE
│   │                       #  + EXPOSES/MAPPED_TO/INJECTS/HANDLES (Spring)
│   │                       #  + EXECUTES_SQL/READS_TABLE/WRITES_TABLE (SQL + Spark)
│   └── graph_summary.json
├── vector/
│   └── index.json          # entity chunks + embeddings (in-memory store)
└── reports/
    ├── unresolved_symbols.md
    └── parse_report.md
```

Add `--inventory-only` to `scan` to skip the Phase 2 graph build.

## Querying a scanned repo

```bash
../.venv/bin/python -m code_memory --project /path search "add retry to payment calls"
../.venv/bin/python -m code_memory --project /path impact PaymentService.processPayment
../.venv/bin/python -m code_memory --project /path graph processPayment
```

`search` runs hybrid retrieval (lexical + vector + symbol + call-graph expansion,
reciprocal-rank fused, then reranked). `impact` reports direct/transitive callers,
callees, tests and related SQL/types. `graph` (no arg) prints backend stats;
with a symbol it prints that node and its neighbours.

Graph and vector default to in-memory (no servers). Set `graph.provider: neo4j`
/ `vector.provider: qdrant` (with `pip install "local-code-memory[neo4j,qdrant]"`
and `docker compose up -d`) to use the real backends. Embeddings default to
dependency-free feature-hashing; set `embedding.provider: ollama` for
`nomic-embed-text`.

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
