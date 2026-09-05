"""FastAPI app exposing the Code Memory CLI surface to a browser UI.

Every route is a thin wrapper over the same functions the CLI calls
(``run_scan``, ``build_retriever``, ``get_graph_repository``,
``generate_task_context``, ``CodingAdvisor``) - no logic is duplicated here.
Scans and LLM calls run as background jobs (``code_memory.api.jobs``) so the
UI polls instead of holding a request open.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from code_memory.api.jobs import JobRunner
from code_memory.config import Config, load_config
from code_memory.graph.repository import get_graph_repository
from code_memory.graph.resolve import resolve_symbol
from code_memory.logging_setup import configure_logging, get_logger

log = get_logger("api")
_STATIC_DIR = Path(__file__).resolve().parent / "static"

_CONTEXT_FILES = [
    "00_project_overview.md", "01_architecture.md", "02_modules.md",
    "03_dependencies.md", "04_configuration.md", "05_database.md",
    "06_api_endpoints.md", "07_call_graph.md", "08_data_flow.md",
    "09_exception_flow.md", "10_logging.md", "11_tests.md", "12_spark.md",
    "13_sql.md", "14_ai_coding_instructions.md",
]
_TASK_FILES = [
    "task.md", "relevant_symbols.md", "relevant_files.md", "call_graph.md",
    "data_flow.md", "tests.md", "configuration.md", "sql.md",
    "source_context.md", "llm_prompt.md", "advice.md", "patch.diff",
]


# -- request bodies -------------------------------------------------------
class ProjectIn(BaseModel):
    root: str


class ScanIn(BaseModel):
    mode: str = "full"          # full | incremental | rebuild


class SearchIn(BaseModel):
    query: str
    k: int = 10


class TaskIn(BaseModel):
    task: str
    ask: bool = False
    mode: str = "implement_feature"
    patch: bool = False


def create_app(config: Config | None = None) -> FastAPI:
    app = FastAPI(title="Local Code Memory", version="0.1.0")
    app.state.config = config or load_config()
    app.state.jobs = JobRunner()
    configure_logging(app.state.config.get("logging", {}))

    def cfg() -> Config:
        return app.state.config

    def repo():
        return get_graph_repository(cfg())

    def _safe_name(name: str, allowed: list[str]) -> str:
        if name not in allowed:
            raise HTTPException(404, f"unknown file: {name}")
        return name

    # -- health / project -------------------------------------------
    @app.get("/api/health")
    def health():
        c = cfg()
        return {"status": "ok", "project_root": str(c.project_root),
                "output_dir": str(c.output_dir),
                "scanned": (c.output_dir / "manifest.json").is_file()}

    @app.post("/api/project")
    def set_project(body: ProjectIn):
        root = Path(body.root).expanduser()
        if not root.is_dir():
            raise HTTPException(400, f"not a directory: {root}")
        app.state.config.data["project"]["root"] = str(root)
        return health()

    @app.get("/api/overview")
    def overview():
        c = cfg()
        inv_path = c.output_dir / "project_inventory.json"
        manifest_path = c.output_dir / "manifest.json"
        if not inv_path.is_file():
            return {"scanned": False}
        inv = json.loads(inv_path.read_text())
        manifest = (json.loads(manifest_path.read_text())
                   if manifest_path.is_file() else None)
        return {"scanned": True, "inventory": inv, "manifest": manifest}

    # -- scan / jobs --------------------------------------------------
    @app.post("/api/scan")
    def start_scan(body: ScanIn):
        from code_memory.pipeline import run_scan

        c = cfg()

        def run():
            _, result = run_scan(c, mode=body.mode)
            inv = result.inventory
            return {
                "scan_id": inv.scan_id, "files": len(inv.files),
                "warnings": len(inv.warnings),
                "graph": (result.java.graph.counts() if result.java else None),
                "spring": (result.java.spring.counts()
                          if result.java and result.java.spring else None),
                "sql": (result.java.sql.counts()
                       if result.java and result.java.sql
                       and result.java.sql.is_present() else None),
                "spark": (result.java.spark.counts()
                         if result.java and result.java.spark
                         and result.java.spark.detected else None),
                "vector": result.vector,
                "context_pack_files": len(result.context_pack or []),
            }

        job_id = app.state.jobs.submit("scan", run)
        return {"job_id": job_id}

    @app.get("/api/jobs/{job_id}")
    def job_status(job_id: str):
        job = app.state.jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "unknown job")
        return job.to_dict()

    @app.get("/api/jobs")
    def job_list():
        return [j.to_dict() for j in app.state.jobs.list()]

    # -- context pack --------------------------------------------------
    @app.get("/api/context")
    def context_list():
        cdir = cfg().output_dir / "context"
        return [{"name": n, "present": (cdir / n).is_file()}
                for n in _CONTEXT_FILES]

    @app.get("/api/context/{name}", response_class=PlainTextResponse)
    def context_file(name: str):
        name = _safe_name(name, _CONTEXT_FILES)
        p = cfg().output_dir / "context" / name
        if not p.is_file():
            raise HTTPException(404, "not generated yet - run a scan")
        return p.read_text(encoding="utf-8")

    # -- search / impact / graph -----------------------------------
    @app.post("/api/search")
    def search(body: SearchIn):
        from code_memory.retrieval import build_retriever

        retriever = build_retriever(cfg())
        items = retriever.retrieve(body.query, top_k=body.k)
        return [i.to_dict() for i in items]

    @app.get("/api/impact/{symbol:path}")
    def impact(symbol: str):
        r = repo()
        node_id = resolve_symbol(r, symbol)
        if node_id is None:
            raise HTTPException(404, f"symbol not found: {symbol}")
        return r.find_impact(node_id)

    @app.get("/api/graph/stats")
    def graph_stats():
        return repo().stats()

    @app.get("/api/graph/node/{symbol:path}")
    def graph_node(symbol: str):
        r = repo()
        node_id = resolve_symbol(r, symbol)
        if node_id is None:
            raise HTTPException(404, f"symbol not found: {symbol}")
        return {"node": r.get_node(node_id),
                "neighbors": r.neighbors(node_id, direction="both")}

    # -- dashboards: endpoints / sql / spark -------------------------
    @app.get("/api/endpoints")
    def endpoints():
        r = repo()
        out = []
        for ep in r.find_nodes(kind="Endpoint"):
            flow = r.find_endpoint_flow(ep["id"])
            out.append({"id": ep["id"], "http_method": ep.get("http_method"),
                       "path": ep.get("path"), "controller": ep.get("controller"),
                       "handler": ep.get("handler"),
                       "flow": [s["id"] for s in flow["flow"][:10]]})
        return sorted(out, key=lambda e: (e["path"] or "", e["http_method"] or ""))

    @app.get("/api/sql")
    def sql():
        r = repo()
        statements = [{"id": n["id"], "type": n.get("statement_type"),
                      "text": n.get("text"), "reads": n.get("tables_read", []),
                      "writes": n.get("tables_written", []),
                      "parsed_ok": n.get("parsed_ok")}
                     for n in r.find_nodes(kind="SQLStatement")]
        tables = []
        for t in r.find_nodes(kind="Table"):
            usage = r.find_database_usage(t["name"])
            tables.append({"name": t["name"], "read_by": usage["read_by"],
                          "written_by": usage["written_by"]})
        return {"statements": statements, "tables": tables}

    @app.get("/api/spark")
    def spark():
        r = repo()
        jobs = []
        for n in r.find_nodes(kind="Method"):
            if not n.get("spark_job"):
                continue
            reads = [nb["id"] for nb in r.neighbors(
                n["id"], edge_types=("READS_TABLE",), direction="out")]
            writes = [nb["id"] for nb in r.neighbors(
                n["id"], edge_types=("WRITES_TABLE",), direction="out")]
            jobs.append({"id": n["id"], "fqn": n.get("fqn"),
                        "transformations": n.get("spark_transformations", []),
                        "actions": n.get("spark_actions", []),
                        "reads_tables": reads, "writes_tables": writes})
        return jobs

    # -- task packs / ask / patch -----------------------------------
    @app.post("/api/tasks")
    def create_task(body: TaskIn):
        c = cfg()

        def run():
            from code_memory.context import generate_task_context

            pack = generate_task_context(c, body.task)
            out: dict[str, Any] = {"task_id": pack.directory.name,
                                   "est_tokens": pack.est_tokens,
                                   "symbols": pack.symbols}
            if body.ask or body.patch:
                from code_memory.llm import CodingAdvisor

                advice = CodingAdvisor(c).advise(pack.directory, body.task,
                                                 mode=body.mode, patch=body.patch)
                out["advice"] = advice.to_dict()
            return out

        job_id = app.state.jobs.submit("task", run)
        return {"job_id": job_id}

    @app.get("/api/tasks")
    def list_tasks():
        tdir = cfg().output_dir / "tasks"
        if not tdir.is_dir():
            return []
        out = []
        for d in sorted(tdir.iterdir(), reverse=True):
            if not d.is_dir():
                continue
            task_md = d / "task.md"
            first_line = ""
            if task_md.is_file():
                for line in task_md.read_text(encoding="utf-8").splitlines():
                    if line.strip().startswith(">"):
                        first_line = line.strip().lstrip("> ").strip()
                        break
            out.append({"id": d.name, "task": first_line,
                       "has_advice": (d / "advice.md").is_file(),
                       "has_patch": (d / "patch.diff").is_file()})
        return out

    @app.get("/api/tasks/{task_id}")
    def get_task(task_id: str):
        if "/" in task_id or ".." in task_id:
            raise HTTPException(400, "invalid task id")
        d = cfg().output_dir / "tasks" / task_id
        if not d.is_dir():
            raise HTTPException(404, "unknown task")
        files = {}
        for name in _TASK_FILES:
            p = d / name
            if p.is_file():
                files[name] = p.read_text(encoding="utf-8")
        advice_json = d / "advice.json"
        advice = (json.loads(advice_json.read_text())
                 if advice_json.is_file() else None)
        return {"id": task_id, "files": files, "advice": advice}

    # -- static UI ----------------------------------------------------
    if _STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)),
                  name="static")

        @app.get("/")
        def index():
            from fastapi.responses import FileResponse

            return FileResponse(str(_STATIC_DIR / "index.html"))

    return app
