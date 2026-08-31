"""``code-memory`` CLI entry point.

Phase 0/1 implement: init, scan, rebuild, stats, doctor, validate.
Later-phase commands (search, impact, context, graph, export) are registered
but report the phase that will deliver them, so the surface is stable.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from code_memory import __version__
from code_memory.config import Config, load_config
from code_memory.health import run_health_checks
from code_memory.logging_setup import configure_logging, get_logger

log = get_logger("cli")

_PENDING = {
    "search": "Phase 9 (hybrid retrieval)",
    "impact": "Phase 3+ (call graph / impact analysis)",
    "context": "Phase 11 (task-specific context generator)",
    "graph": "Phase 7 (graph DB)",
    "export": "Phase 10 (markdown memory generator)",
}


def _load(args: argparse.Namespace) -> Config:
    cfg = load_config(getattr(args, "config", None))
    if getattr(args, "project", None):
        cfg.data["project"]["root"] = args.project
    configure_logging(cfg.get("logging", {}))
    return cfg


# -- commands ---------------------------------------------------------
def cmd_init(args: argparse.Namespace) -> int:
    cfg = _load(args)
    out = cfg.output_dir
    for sub in ("", "context", "graph", "symbols", "reports", "tasks"):
        (out / sub).mkdir(parents=True, exist_ok=True)
    (cfg.metadata_db).parent.mkdir(parents=True, exist_ok=True)
    print(f"initialised code memory under: {out}")
    print(f"metadata db path:              {cfg.metadata_db}")
    print(f"config sources:                {', '.join(cfg.sources) or 'defaults'}")
    return 0


def cmd_scan(args: argparse.Namespace) -> int:
    from code_memory.pipeline import run_scan

    cfg = _load(args)
    mode = "incremental" if args.incremental else "full"
    ctx, result = run_scan(cfg, mode=mode, semantic=not args.inventory_only)
    _print_scan_summary(ctx, result)
    return 0


def cmd_rebuild(args: argparse.Namespace) -> int:
    from code_memory.pipeline import run_scan

    cfg = _load(args)
    ctx, result = run_scan(cfg, mode="rebuild")
    _print_scan_summary(ctx, result)
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    from code_memory.metadata import MetadataStore

    cfg = _load(args)
    store = MetadataStore(cfg.metadata_db)
    summary = store.summary()
    if args.json:
        print(json.dumps(summary, indent=2, default=str))
        return 0
    print(f"metadata db:   {summary['db_path']}")
    print(f"scans:         {summary['scan_count']}")
    print(f"tracked files: {summary['tracked_files']}")
    latest = summary["latest_scan"]
    if latest:
        print(f"latest scan:   {latest['scan_id']} [{latest['status']}] "
              f"mode={latest['mode']} commit={latest['git_commit']}")
        if latest.get("stats_json"):
            stats = json.loads(latest["stats_json"])
            for key in ("file_count", "java_loc", "added", "modified", "deleted",
                        "warnings", "duration_ms", "build_system"):
                if key in stats:
                    print(f"  {key}: {stats[key]}")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    cfg = _load(args)
    checks = run_health_checks(cfg)
    worst_ok = True
    for c in checks:
        mark = "ok  " if c.ok else ("FAIL" if c.required else "warn")
        if not c.ok and c.required:
            worst_ok = False
        print(f"[{mark}] {c.name:24} {c.detail}")
    print()
    print("doctor:", "healthy" if worst_ok else "problems found (see FAIL lines)")
    return 0 if worst_ok else 1


def cmd_validate(args: argparse.Namespace) -> int:
    """Validate that config loads and the inventory artifacts are well-formed."""
    cfg = _load(args)
    problems: list[str] = []

    inv_path = cfg.output_dir / "project_inventory.json"
    if not inv_path.is_file():
        problems.append(f"no inventory yet at {inv_path} - run `code-memory scan`")
    else:
        try:
            data = json.loads(inv_path.read_text(encoding="utf-8"))
            for req in ("scan_id", "files", "build", "counts_by_kind"):
                if req not in data:
                    problems.append(f"inventory missing key: {req}")
        except json.JSONDecodeError as exc:
            problems.append(f"inventory is not valid JSON: {exc}")

    for p in problems:
        print("problem:", p)
    print("validate:", "ok" if not problems else f"{len(problems)} problem(s)")
    return 0 if not problems else 1


def cmd_pending(name: str):
    def _run(args: argparse.Namespace) -> int:
        print(f"`code-memory {name}` is not implemented yet - arrives in {_PENDING[name]}.")
        return 2
    return _run


# -- helpers --------------------------------------------------------
def _print_scan_summary(ctx, result) -> None:
    inv = result.inventory
    print(f"scan {ctx.scan_id} [{ctx.mode}] - {result.duration_ms} ms")
    print(f"  root:          {inv.project_root}")
    print(f"  git:           {inv.git_branch} @ {inv.git_commit}")
    print(f"  build system:  {inv.build.build_system}")
    print(f"  files:         {len(inv.files)}  "
          f"(+{len(result.added)} ~{len(result.modified)} "
          f"-{len(result.deleted)} ={result.unchanged})")
    print(f"  java loc:      {inv.total_java_loc()}")
    for kind, n in inv.counts_by_kind().items():
        print(f"      {kind:16} {n}")
    if inv.warnings:
        print(f"  warnings:      {len(inv.warnings)} (see reports)")

    java = getattr(result, "java", None)
    if java is not None:
        c = java.graph.counts()
        print(f"  java parse:    {java.status_counts}")
        print(f"  graph:         {c['node_count']} nodes, {c['edge_count']} edges, "
              f"{c['unresolved_count']} unresolved")
        if c.get("call_edges"):
            print(f"  calls:         {c['call_edges']} edges, "
                  f"resolution {c['call_resolution_rate']} {c['calls_by_confidence']}")
        spring = getattr(java, "spring", None)
        if spring is not None and spring.is_spring():
            sc = spring.counts()
            print(f"  spring:        {sc['components']} components "
                  f"{sc['components_by_stereotype']}, {sc['endpoints']} endpoints, "
                  f"{sc['beans']} beans, {sc['injections']} injections")
        sql = getattr(java, "sql", None)
        if sql is not None and sql.is_present():
            qc = sql.counts()
            print(f"  sql:           {qc['sql_statements']} statements "
                  f"{qc['sql_by_type']}, {qc['tables']} tables")
        spark = getattr(java, "spark", None)
        if spark is not None and spark.is_present():
            kc = spark.counts()
            print(f"  spark:         {kc['spark_jobs']} jobs, "
                  f"in={kc['spark_input_tables']} out={kc['spark_output_tables']}")
        for kind, n in c["nodes_by_kind"].items():
            print(f"      {kind:16} {n}")

    for art in result.artifacts:
        print(f"  wrote:         {art}")
    if java is not None:
        for art in java.artifacts:
            print(f"  wrote:         {art}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="code-memory",
                                description="Local Code Memory for Java repos")
    p.add_argument("--version", action="version",
                   version=f"code-memory {__version__}")
    p.add_argument("-c", "--config", help="path to application.yaml")
    p.add_argument("-p", "--project", help="override project.root")

    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="create .code-memory dirs and metadata db"
                   ).set_defaults(func=cmd_init)

    sp = sub.add_parser("scan", help="scan the repository (inventory + Java graph)")
    sp.add_argument("--incremental", action="store_true",
                    help="only diff against the previous scan")
    sp.add_argument("--inventory-only", action="store_true",
                    help="skip the Phase 2 Java semantic scan / graph build")
    sp.set_defaults(func=cmd_scan)

    sub.add_parser("rebuild", help="discard tracked state and full-scan"
                   ).set_defaults(func=cmd_rebuild)

    spx = sub.add_parser("stats", help="show scan / metadata statistics")
    spx.add_argument("--json", action="store_true")
    spx.set_defaults(func=cmd_stats)

    sub.add_parser("doctor", help="environment & local-service health checks"
                   ).set_defaults(func=cmd_doctor)
    sub.add_parser("validate", help="validate config and generated artifacts"
                   ).set_defaults(func=cmd_validate)

    for name in _PENDING:
        sub.add_parser(name, help=f"[pending: {_PENDING[name]}]"
                       ).set_defaults(func=cmd_pending(name))

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
    except Exception as exc:  # top-level guard - log and exit non-zero
        log.error("command failed", extra={"error": str(exc)}, exc_info=True)
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
