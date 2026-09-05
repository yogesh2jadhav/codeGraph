import shutil
from pathlib import Path

from code_memory.cli.main import main
from code_memory.pipeline import run_scan

FIXTURES = Path(__file__).parents[1] / "fixtures"


def _scanned(tmp_path, config_for):
    root = tmp_path / "plain_etl_sample"
    shutil.copytree(FIXTURES / "plain_etl_sample", root)
    cfg = config_for(root)
    run_scan(cfg, mode="full")
    return cfg, root


def test_data_flow_doc_reports_plain_entrypoint(tmp_path, config_for):
    _, root = _scanned(tmp_path, config_for)
    doc = (root / ".code-memory/context/08_data_flow.md").read_text()
    assert "Standalone entrypoints" in doc
    assert "com.etl2.DataMigrationJob#main(String[])" in doc
    assert "run()" in doc and "extract()" in doc


def test_cli_flow_lists_and_traces(tmp_path, config_for, capsys):
    cfg, root = _scanned(tmp_path, config_for)
    args = ["-p", str(root)]

    assert main(args + ["flow"]) == 0
    out = capsys.readouterr().out
    assert "com.etl2.DataMigrationJob#main(String[])" in out

    assert main(args + ["flow", "DataMigrationJob#main"]) == 0
    out = capsys.readouterr().out
    assert "run()" in out and "extract()" in out and "load(" in out


def test_api_entrypoints_and_flow(tmp_path, config_for):
    from fastapi.testclient import TestClient
    from code_memory.api import create_app

    cfg, root = _scanned(tmp_path, config_for)
    c = TestClient(create_app(cfg))

    eps = c.get("/api/entrypoints").json()
    assert any("DataMigrationJob#main" in e["fqn"] for e in eps)

    r = c.get("/api/flow/" + "method%3Acom.etl2.DataMigrationJob%23main(String%5B%5D)")
    assert r.status_code == 200
    ids = {s["id"] for s in r.json()["flow"]}
    assert "method:com.etl2.DataMigrationJob#run()" in ids
    assert "method:com.etl2.DataMigrationJob#extract()" in ids
