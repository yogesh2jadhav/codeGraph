import shutil
import time
from pathlib import Path
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from code_memory.api import create_app

FIXTURES = Path(__file__).parents[1] / "fixtures"


@pytest.fixture
def client(tmp_path, config_for):
    root = tmp_path / "spring_api_sample"
    shutil.copytree(FIXTURES / "spring_api_sample", root)
    cfg = config_for(root)
    cfg.data["llm"]["provider"] = "echo"   # keep advisor offline in tests
    app = create_app(cfg)
    return TestClient(app), root


def _wait_job(c: TestClient, job_id: str, timeout: float = 30) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = c.get(f"/api/jobs/{job_id}").json()
        if job["status"] != "running":
            return job
        time.sleep(0.1)
    raise TimeoutError(f"job {job_id} did not finish in {timeout}s")


def test_health_and_overview_before_scan(client):
    c, root = client
    h = c.get("/api/health").json()
    assert h["scanned"] is False
    assert h["project_root"] == str(root)

    ov = c.get("/api/overview").json()
    assert ov["scanned"] is False


def test_set_project_rejects_missing_dir(client):
    c, _ = client
    r = c.post("/api/project", json={"root": "/no/such/dir"})
    assert r.status_code == 400


def test_scan_job_and_overview(client):
    c, root = client
    job_id = c.post("/api/scan", json={"mode": "full"}).json()["job_id"]
    job = _wait_job(c, job_id)
    assert job["status"] == "done", job.get("error")
    assert job["result"]["files"] > 0
    assert job["result"]["graph"]["node_count"] > 0
    assert job["result"]["spring"]["endpoints"] == 2

    ov = c.get("/api/overview").json()
    assert ov["scanned"] is True
    assert ov["inventory"]["build"]["build_system"] == "maven"
    assert ov["manifest"]["scan_id"] == job["result"]["scan_id"]


def _scanned_client(client):
    c, root = client
    job_id = c.post("/api/scan", json={"mode": "full"}).json()["job_id"]
    _wait_job(c, job_id)
    return c, root


def test_context_docs(client):
    c, _ = _scanned_client(client)
    files = c.get("/api/context").json()
    assert any(f["name"] == "14_ai_coding_instructions.md" and f["present"] for f in files)

    body = c.get("/api/context/14_ai_coding_instructions.md").text
    assert "AI Coding Instructions" in body

    assert c.get("/api/context/../../etc/passwd").status_code == 404
    assert c.get("/api/context/not_a_real_file.md").status_code == 404


def test_search_and_graph(client):
    c, _ = _scanned_client(client)
    results = c.post("/api/search", json={"query": "place a new order", "k": 5}).json()
    assert results
    assert results[0]["node_id"].startswith(("method:", "endpoint:"))

    node = c.get("/api/graph/node/place").json()
    assert node["node"] is not None
    assert any(n["edge"] == "CALLS" for n in node["neighbors"])

    assert c.get("/api/graph/node/NoSuchSymbolAtAll").status_code == 404


def test_impact(client):
    c, _ = _scanned_client(client)
    body = c.get("/api/impact/place").json()
    assert "direct_callers" in body


def test_graph_node_lookup_with_slash_in_id(client):
    # Endpoint node ids contain "/" (e.g. "endpoint:GET /api/orders") - found
    # via manual UI testing that the plain {symbol} path param 404'd on these
    # because a percent-encoded "/" doesn't match a single path segment.
    c, _ = _scanned_client(client)
    node_id = "endpoint:GET /api/orders/{id}"
    r = c.get(f"/api/graph/node/{quote(node_id, safe='')}")
    assert r.status_code == 200
    body = r.json()
    assert body["node"]["id"] == node_id
    assert any(n["edge"] == "MAPPED_TO" for n in body["neighbors"])


def test_endpoints_sql_dashboards(client):
    c, _ = _scanned_client(client)
    eps = c.get("/api/endpoints").json()
    assert {e["path"] for e in eps} == {"/api/orders/{id}", "/api/orders"}

    sql = c.get("/api/sql").json()
    assert sql["statements"]
    assert any(t["name"] in ("orders", "customers") for t in sql["tables"])

    spark = c.get("/api/spark").json()
    assert spark == []   # this fixture has no Spark code


def test_task_creation_and_detail(client):
    c, _ = _scanned_client(client)
    job_id = c.post("/api/tasks", json={
        "task": "add a comment to OrderService", "ask": True, "patch": False,
        "mode": "implement_feature",
    }).json()["job_id"]
    job = _wait_job(c, job_id)
    assert job["status"] == "done", job.get("error")
    task_id = job["result"]["task_id"]
    assert job["result"]["advice"]["provider"] == "echo"

    listed = c.get("/api/tasks").json()
    assert any(t["id"] == task_id for t in listed)

    detail = c.get(f"/api/tasks/{task_id}").json()
    assert "task.md" in detail["files"]
    assert "advice.md" in detail["files"]
    assert detail["advice"]["parsed"] is not None

    # httpx/browsers normalise ".." out of the URL before it's ever sent, so
    # the traversal guard in get_task() is unreachable via a real client - it
    # exists as defence in depth. Just confirm an unknown id 404s cleanly.
    assert c.get("/api/tasks/does-not-exist").status_code == 404
