import json

from code_memory.pipeline import run_scan


def test_pipeline_writes_graph_artifacts(spring_sample, config_for):
    cfg = config_for(spring_sample)
    ctx, result = run_scan(cfg, mode="full")

    assert result.java is not None
    out = spring_sample / ".code-memory"
    for rel in ("graph/nodes.json", "graph/edges.json", "graph/graph_summary.json",
                "reports/unresolved_symbols.md", "reports/parse_report.md"):
        assert (out / rel).is_file(), rel

    nodes = json.loads((out / "graph/nodes.json").read_text())
    edges = json.loads((out / "graph/edges.json").read_text())
    ids = {n["id"] for n in nodes}

    # ground truth: UserService declares createUser at a known location
    assert "type:com.example.UserService" in ids
    assert "method:com.example.UserService#createUser(String)" in ids
    declares = {(e["src"], e["dst"]) for e in edges if e["type"] == "DECLARES"}
    assert ("type:com.example.UserService",
            "method:com.example.UserService#createUser(String)") in declares

    svc = next(n for n in nodes if n["id"] == "type:com.example.UserService")
    assert svc["location"]["relative_path"].endswith("UserService.java")

    # Phase 3 ground truth: createUser -> UserRepository.save, resolved
    calls = {(e["src"], e["dst"]) for e in edges if e["type"] == "CALLS"}
    assert ("method:com.example.UserService#createUser(String)",
            "method:com.example.UserRepository#save(User)") in calls
    assert (out / "context" / "07_call_graph.md").is_file()

    # summary persisted into scan stats
    scan = ctx.store.get_scan(ctx.scan_id)
    stats = json.loads(scan["stats_json"])
    assert stats["java"]["node_count"] == len(nodes)


def test_inventory_only_skips_graph(spring_sample, config_for):
    cfg = config_for(spring_sample)
    _, result = run_scan(cfg, mode="full", semantic=False)
    assert result.java is None
    assert not (spring_sample / ".code-memory" / "graph").exists()
