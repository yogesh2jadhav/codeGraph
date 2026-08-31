import json
import shutil
from pathlib import Path

from code_memory.pipeline import run_scan

FIXTURES = Path(__file__).parents[1] / "fixtures"

_FULL_SET = {
    "00_project_overview.md", "01_architecture.md", "02_modules.md",
    "03_dependencies.md", "04_configuration.md", "05_database.md",
    "06_api_endpoints.md", "07_call_graph.md", "08_data_flow.md",
    "09_exception_flow.md", "10_logging.md", "11_tests.md", "13_sql.md",
    "14_ai_coding_instructions.md",
}


def test_full_context_pack(tmp_path, config_for):
    root = tmp_path / "spring_api_sample"
    shutil.copytree(FIXTURES / "spring_api_sample", root)
    ctx, result = run_scan(config_for(root), mode="full")

    cdir = root / ".code-memory" / "context"
    present = {p.name for p in cdir.glob("*.md")}
    assert _FULL_SET <= present

    arch = (cdir / "01_architecture.md").read_text()
    assert "Controller/API" in arch and "OrderController" in arch
    assert "Service" in arch and "OrderService" in arch

    db = (cdir / "05_database.md").read_text()
    assert "orders" in db and "customers" in db

    instr = (cdir / "14_ai_coding_instructions.md").read_text()
    assert "Spring conventions" in instr
    assert "confidence tag" in instr.lower()

    manifest = json.loads((root / ".code-memory" / "manifest.json").read_text())
    assert manifest["scan_id"] == ctx.scan_id
    assert "graph" in manifest and manifest["graph"]["nodes"] > 0
    assert any(a.endswith("14_ai_coding_instructions.md")
               for a in manifest["artifacts"])

    q = (root / ".code-memory" / "reports" / "quality_report.md").read_text()
    assert "Parse success rate" in q and "Graph nodes" in q

    stats = json.loads(ctx.store.get_scan(ctx.scan_id)["stats_json"])
    assert stats["context_pack_files"] >= len(_FULL_SET)


def test_config_secrets_redacted(tmp_path, config_for):
    root = tmp_path / "spring_api_sample"
    shutil.copytree(FIXTURES / "spring_api_sample", root)
    res_dir = root / "src/main/resources"
    res_dir.mkdir(parents=True)
    (res_dir / "application.yml").write_text(
        "app:\n  name: orders\n  db:\n    password: sup3rs3cret\n", encoding="utf-8")

    _, _ = run_scan(config_for(root), mode="full")
    cfg_md = (root / ".code-memory/context/04_configuration.md").read_text()
    assert "app.name" in cfg_md
    assert "sup3rs3cret" not in cfg_md
    assert "redacted" in cfg_md.lower()
