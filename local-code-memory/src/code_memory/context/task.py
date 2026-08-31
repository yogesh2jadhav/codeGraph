"""Phase 11 - task-specific context generator.

``code-memory context "Add retry handling to the payment service"`` ->
a compact, token-budgeted pack under ``.code-memory/tasks/<id>/`` that a local
coding LLM can consume without seeing the whole repository.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from code_memory.config import Config
from code_memory.context.util import estimate_tokens, scan_config_properties
from code_memory.logging_setup import get_logger
from code_memory.models.inventory import FileKind
from code_memory.retrieval import build_retriever

log = get_logger("context.task")


@dataclass
class TaskPack:
    task: str
    directory: Path
    files: list[Path] = field(default_factory=list)
    symbols: list[str] = field(default_factory=list)
    est_tokens: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {"task": self.task, "directory": str(self.directory),
                "files": [f.name for f in self.files],
                "symbols": self.symbols, "est_tokens": self.est_tokens}


def _budget(config: Config) -> dict[str, int]:
    ctx = config.get("context", {}) or {}
    return {
        "max_tokens": int(ctx.get("max_tokens", 24000)),
        "max_files": int(ctx.get("max_files", 30)),
        "max_methods": int(ctx.get("max_methods", 50)),
        "max_graph_hops": int(ctx.get("max_graph_hops", 3)),
        "top_k": int(ctx.get("rerank_results", 10)),
        "snippet_lines": 60,
    }


def _next_task_dir(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    n = 1 + sum(1 for p in root.iterdir()
                if p.is_dir() and p.name.startswith(f"task_{stamp}_"))
    return root / f"task_{stamp}_{n:03d}"


def _snippet(root: Path, rel: str, ls: int, le: int, limit: int) -> str:
    try:
        lines = (root / rel).read_text(encoding="utf-8",
                                       errors="replace").splitlines()
    except OSError:
        return ""
    lo = max(0, (ls or 1) - 1)
    hi = min(len(lines), lo + limit, (le or ls or 1))
    return "\n".join(f"{lo + i + 1:5}  {line}"
                     for i, line in enumerate(lines[lo:hi]))


def generate_task_context(config: Config, task: str) -> TaskPack:
    b = _budget(config)
    retriever = build_retriever(config)
    repo = retriever.graph
    root = config.project_root

    hits = retriever.retrieve(task, top_k=max(b["top_k"], 8),
                              vector_k=config.get("retrieval.vector_k", 30))
    tdir = _next_task_dir(config.output_dir / "tasks")
    tdir.mkdir(parents=True, exist_ok=True)

    seed_methods = [h.node_id for h in hits if h.node_id.startswith("method:")]
    seed_types = [h.node_id for h in hits if h.node_id.startswith("type:")]

    # -- expand the graph around the seeds -----------------------------
    callers, callees, tests, sql_ids, tables, related_types = (
        set(), set(), set(), set(), set(), set())
    for mid in seed_methods[:b["max_methods"]]:
        imp = repo.find_impact(mid, max_depth=b["max_graph_hops"])
        callers.update(imp["direct_callers"])
        callees.update(imp["callees"])
        tests.update(imp.get("tests", []))
        for etype, ids in (imp.get("related") or {}).items():
            for nid in ids:
                if nid.startswith("sql:"):
                    sql_ids.add(nid)
                elif nid.startswith("table:"):
                    tables.add(nid)
                elif nid.startswith("type:"):
                    related_types.add(nid)
        for nb in repo.neighbors(mid, edge_types=("EXECUTES_SQL",),
                                 direction="out"):
            sql_ids.add(nb["id"])

    focus_ids = _dedupe(seed_methods + list(callers) + list(callees)
                        + seed_types + list(related_types))
    pack = TaskPack(task, tdir, symbols=[_short(i) for i in seed_methods
                                         + seed_types])

    # -- files -----------------------------------------------------
    files: dict[str, str] = {}

    files["task.md"] = _md_task(task, hits, b)
    files["relevant_symbols.md"] = _md_symbols(repo, hits, callers, callees)
    files["relevant_files.md"] = _md_files(repo, focus_ids, b)
    files["call_graph.md"] = _md_callgraph(repo, seed_methods)
    files["data_flow.md"] = _md_dataflow(repo, seed_methods, tables)
    files["tests.md"] = _md_tests(repo, tests, root, b)
    files["configuration.md"] = _md_config(config)
    files["sql.md"] = _md_sql(repo, sql_ids)
    files["source_context.md"] = _md_source(repo, focus_ids, root, b)
    files["llm_prompt.md"] = _md_prompt(task, config)

    total = 0
    for name, body in files.items():
        p = tdir / name
        p.write_text(body, encoding="utf-8")
        pack.files.append(p)
        total += estimate_tokens(body)
    pack.est_tokens = total

    log.info("task context generated",
             extra={"dir": str(tdir), "est_tokens": total,
                    "over_budget": total > b["max_tokens"]})
    return pack


# -- renderers ------------------------------------------------------
def _short(node_id: str) -> str:
    return node_id.split(":", 1)[-1]


def _dedupe(seq):
    seen, out = set(), []
    for x in seq:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _md_task(task: str, hits, b) -> str:
    lines = ["# Task", "", f"> {task}", "",
             "## Retrieval seeds", "",
             "| # | score | kind | symbol | where | via |",
             "| --- | ---: | --- | --- | --- | --- |"]
    for i, h in enumerate(hits, 1):
        loc = f"{h.file}:{h.line}" if h.file else "-"
        lines.append(f"| {i} | {h.score:.3f} | {h.kind} | "
                     f"`{h.fqn or _short(h.node_id)}` | {loc} | "
                     f"{','.join(h.sources)} |")
    lines += ["", f"_Token budget: {b['max_tokens']}; "
              f"max files {b['max_files']}, max methods {b['max_methods']}._", ""]
    return "\n".join(lines) + "\n"


def _md_symbols(repo, hits, callers, callees) -> str:
    lines = ["# Relevant symbols", "", "## Primary (retrieved)", ""]
    for h in hits:
        node = repo.get_node(h.node_id) or {}
        lines.append(f"- **`{h.fqn or _short(h.node_id)}`** [{h.kind}] "
                     f"{('- ' + (node.get('return_type') or '')) if node.get('return_type') else ''}")
        if h.file:
            lines.append(f"  - {h.file}:{h.line}")
    lines += ["", "## Direct callers", ""]
    lines += [f"- `{_short(c)}`" for c in sorted(callers)[:40]] or ["_none_"]
    lines += ["", "## Callees", ""]
    lines += [f"- `{_short(c)}`" for c in sorted(callees)[:40]] or ["_none_"]
    lines.append("")
    return "\n".join(lines) + "\n"


def _md_files(repo, focus_ids, b) -> str:
    files: dict[str, int] = {}
    for nid in focus_ids:
        node = repo.get_node(nid) or {}
        loc = node.get("location") or {}
        rel = loc.get("relative_path")
        if rel:
            files[rel] = files.get(rel, 0) + 1
    ranked = sorted(files.items(), key=lambda kv: kv[1], reverse=True)
    lines = ["# Relevant files", "",
             "Inspect these before editing (most-referenced first):", ""]
    for rel, n in ranked[:b["max_files"]]:
        lines.append(f"- `{rel}`  ({n} relevant symbol(s))")
    lines.append("")
    return "\n".join(lines) + "\n"


def _md_callgraph(repo, seed_methods) -> str:
    lines = ["# Call graph (task scope)", ""]
    for mid in seed_methods[:15]:
        lines.append(f"## `{_short(mid)}`")
        callers = repo.find_callers(mid)
        callees = repo.find_callees(mid)
        if callers:
            lines.append("called by:")
            lines += [f"- `{_short(c['id'])}` ({c.get('confidence')})"
                      for c in callers[:15]]
        if callees:
            lines.append("calls:")
            lines += [f"- `{_short(c['id'])}` ({c.get('confidence')})"
                      for c in callees[:15]]
        if not callers and not callees:
            lines.append("_no resolved calls_")
        lines.append("")
    return "\n".join(lines) + "\n"


def _md_dataflow(repo, seed_methods, tables) -> str:
    lines = ["# Data flow (task scope)", ""]
    for mid in seed_methods[:15]:
        node = repo.get_node(mid) or {}
        if node.get("spark_job"):
            lines.append(f"- `{_short(mid)}` Spark job: "
                         f"transforms {node.get('spark_transformations')}")
        for nb in repo.neighbors(mid, edge_types=("EXECUTES_SQL", "READS_TABLE",
                                                  "WRITES_TABLE"),
                                 direction="out"):
            lines.append(f"- `{_short(mid)}` --{nb['edge']}--> {_short(nb['id'])}")
    if tables:
        lines += ["", "## Tables in scope", ""]
        for t in sorted(tables):
            u = repo.find_database_usage(_short(t))
            lines.append(f"- `{_short(t)}` read_by={len(u['read_by'])} "
                         f"write_by={len(u['written_by'])}")
    if len(lines) <= 2:
        lines.append("_No SQL / Spark / table flow in the task scope._")
    lines.append("")
    return "\n".join(lines) + "\n"


def _md_tests(repo, tests, root, b) -> str:
    lines = ["# Tests in scope", ""]
    if not tests:
        lines += ["_No reachable tests found for the seed symbols._",
                  "Consider adding tests for the changes."]
        return "\n".join(lines) + "\n"
    for tid in sorted(tests)[:20]:
        node = repo.get_node(tid) or {}
        loc = node.get("location") or {}
        lines.append(f"## `{_short(tid)}`")
        if loc.get("relative_path"):
            lines.append(f"_{loc['relative_path']}:{loc.get('line_start')}_")
            lines.append("```java")
            lines.append(_snippet(root, loc["relative_path"],
                                  loc.get("line_start", 1),
                                  loc.get("line_end", 1), 30))
            lines.append("```")
        lines.append("")
    return "\n".join(lines) + "\n"


def _md_config(config) -> str:
    rels = [e.relative_path for e in _iter_config(config)]
    rows = scan_config_properties(config.project_root, rels)
    lines = ["# Configuration in scope", ""]
    if not rows:
        lines.append("_No application configuration files._")
        return "\n".join(lines) + "\n"
    lines += ["| File | Key | Value |", "| --- | --- | --- |"]
    for r in rows[:120]:
        val = "_redacted_" if r["secret"] else f"`{r['value']}`"
        lines.append(f"| `{r['file']}` | `{r['key']}` | {val} |")
    lines.append("")
    return "\n".join(lines) + "\n"


def _iter_config(config):
    try:
        import json
        inv = json.loads((config.output_dir / "project_inventory.json")
                         .read_text())
    except OSError:
        return []
    out = []
    for f in inv.get("files", []):
        if f.get("kind") in (FileKind.APP_CONFIG.value, FileKind.SPARK_CONFIG.value):
            out.append(type("E", (), {"relative_path": f["relative_path"]}))
    return out


def _md_sql(repo, sql_ids) -> str:
    lines = ["# SQL in scope", ""]
    if not sql_ids:
        lines.append("_No SQL statements linked to the seed symbols._")
        return "\n".join(lines) + "\n"
    for sid in sorted(sql_ids):
        node = repo.get_node(sid) or {}
        lines.append(f"## {node.get('statement_type', '?')} "
                     f"(reads: {', '.join(node.get('tables_read', [])) or '-'}; "
                     f"writes: {', '.join(node.get('tables_written', [])) or '-'})")
        lines.append("```sql")
        lines.append(node.get("text", ""))
        lines.append("```")
        lines.append("")
    return "\n".join(lines) + "\n"


def _md_source(repo, focus_ids, root, b) -> str:
    lines = ["# Source context", "",
             "Exact snippets for the task-relevant symbols.", ""]
    shown = 0
    for nid in focus_ids:
        if shown >= b["max_methods"]:
            break
        node = repo.get_node(nid)
        if not node:
            continue
        loc = node.get("location") or {}
        rel = loc.get("relative_path")
        if not rel:
            continue
        lines.append(f"## `{node.get('fqn', nid)}`  [{node.get('kind')}]")
        lines.append(f"_{rel}:{loc.get('line_start')}-{loc.get('line_end')}_")
        lines.append("```java")
        lines.append(_snippet(root, rel, loc.get("line_start", 1),
                              loc.get("line_end", 1), b["snippet_lines"]))
        lines.append("```")
        lines.append("")
        shown += 1
    return "\n".join(lines) + "\n"


def _md_prompt(task: str, config) -> str:
    from code_memory.llm.prompts import render_task_prompt

    return render_task_prompt(task, config)
