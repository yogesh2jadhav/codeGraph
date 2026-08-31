import json
import shutil
from pathlib import Path

from code_memory.pipeline import run_scan

FIXTURES = Path(__file__).parents[1] / "fixtures"


def _spring_api(tmp_path):
    dst = tmp_path / "spring_api_sample"
    shutil.copytree(FIXTURES / "spring_api_sample", dst)
    return dst


def test_spring_pipeline_endpoints_and_flow(tmp_path, config_for):
    root = _spring_api(tmp_path)
    cfg = config_for(root)
    ctx, result = run_scan(cfg, mode="full")

    spring = result.java.spring
    assert spring.is_spring()
    assert spring.stereotypes["com.demo.web.OrderController"] == "RestController"
    assert spring.stereotypes["com.demo.svc.OrderService"] == "Service"

    paths = {(e["http_method"], e["path"]) for e in spring.endpoints}
    assert paths == {("GET", "/api/orders/{id}"), ("POST", "/api/orders")}

    edges = json.loads((root / ".code-memory/graph/edges.json").read_text())
    injects = {(e["src"], e["dst"]) for e in edges if e["type"] == "INJECTS"}
    assert ("type:com.demo.web.OrderController", "type:com.demo.svc.OrderService") in injects
    assert ("type:com.demo.svc.OrderService", "type:com.demo.repo.OrderRepository") in injects

    handles = {(e["src"], e["dst"]) for e in edges if e["type"] == "HANDLES"}
    assert ("method:com.demo.web.ApiExceptionHandler#onBadRequest(IllegalArgumentException)",
            "type:java.lang.IllegalArgumentException") in handles

    api_md = (root / ".code-memory/context/06_api_endpoints.md").read_text()
    assert "GET /api/orders/{id}" in api_md
    # end-to-end flow: controller -> service -> repository
    assert "OrderService#findOrder(String)" in api_md
    assert "OrderRepository#findById(String)" in api_md

    stats = json.loads(ctx.store.get_scan(ctx.scan_id)["stats_json"])
    assert stats["java"]["spring"]["endpoints"] == 2
