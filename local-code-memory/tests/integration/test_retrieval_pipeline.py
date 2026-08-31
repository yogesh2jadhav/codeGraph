import json
import shutil
from pathlib import Path

from code_memory.pipeline import run_scan
from code_memory.retrieval import build_retriever

FIXTURES = Path(__file__).parents[1] / "fixtures"


def test_scan_builds_vector_index_and_retriever_works(tmp_path, config_for):
    root = tmp_path / "spring_api_sample"
    shutil.copytree(FIXTURES / "spring_api_sample", root)
    cfg = config_for(root)

    ctx, result = run_scan(cfg, mode="full")
    assert result.vector and result.vector["chunks"] > 0

    index_path = root / ".code-memory" / "vector" / "index.json"
    assert index_path.is_file()
    data = json.loads(index_path.read_text())
    assert data["items"] and data["embedding"].startswith("hashing")

    stats = json.loads(ctx.store.get_scan(ctx.scan_id)["stats_json"])
    assert stats["vector"]["chunks"] == result.vector["chunks"]

    retriever = build_retriever(cfg)
    hits = retriever.retrieve("place a new order", top_k=5)
    assert hits
    assert hits[0].node_id in (
        "method:com.demo.svc.OrderService#place(Order)",
        "method:com.demo.web.OrderController#place(Order)",
    )
    # graph reachable through the same retriever
    imp = retriever.graph.find_impact("method:com.demo.svc.OrderService#place(Order)")
    assert "method:com.demo.web.OrderController#place(Order)" in imp["direct_callers"]


def test_scan_index_false_skips_vector(tmp_path, config_for):
    root = tmp_path / "spring_api_sample"
    shutil.copytree(FIXTURES / "spring_api_sample", root)
    _, result = run_scan(config_for(root), mode="full", index=False)
    assert result.vector is None
    assert not (root / ".code-memory" / "vector").exists()
