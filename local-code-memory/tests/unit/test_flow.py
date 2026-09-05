"""Execution-flow support for plain (non-Spring, non-Spark) entrypoints.

Covers find_entrypoints() / find_call_flow() on a batch/ETL-style method with
a main() -> run() -> extract()/transform()/load() chain and no framework
annotations at all - the case a user asked about directly.
"""

from pathlib import Path

from code_memory.graph import build_graph
from code_memory.graph.memory_repository import InMemoryGraphRepository
from code_memory.parsers.java import parse_java_source

FIXTURE = (Path(__file__).parents[1] / "fixtures" / "plain_etl_sample" /
          "src/main/java/com/etl2/DataMigrationJob.java")


def _repo():
    pf = parse_java_source("DataMigrationJob.java", FIXTURE.read_bytes())
    return InMemoryGraphRepository(graph=build_graph([pf]))


def test_finds_main_as_the_entrypoint():
    repo = _repo()
    eps = repo.find_entrypoints()
    fqns = {e["fqn"] for e in eps}
    assert "com.etl2.DataMigrationJob#main(String[])" in fqns
    main = next(e for e in eps if e["fqn"].startswith("com.etl2.DataMigrationJob#main"))
    assert main["name_hint"] is True
    # extract/transform/load all have a caller (run()) so they must NOT
    # themselves be reported as entrypoints
    assert not any("extract" in e["fqn"] or "load" in e["fqn"] for e in eps)


def test_call_flow_is_ordered_breadth_first():
    repo = _repo()
    main_id = "method:com.etl2.DataMigrationJob#main(String[])"
    flow = repo.find_call_flow(main_id, max_depth=6)
    by_id = {f["id"]: f for f in flow}

    run_id = "method:com.etl2.DataMigrationJob#run()"
    extract_id = "method:com.etl2.DataMigrationJob#extract()"
    load_id = "method:com.etl2.DataMigrationJob#load(List<String>)"

    assert run_id in by_id
    assert extract_id in by_id and load_id in by_id
    # run() is one hop from main(); extract/transform/load are one hop
    # further out (called by run(), not by main() directly)
    assert by_id[run_id]["depth"] < by_id[extract_id]["depth"]
    assert by_id[run_id]["confidence"] == "HIGH"


def test_call_flow_on_leaf_method_is_empty():
    repo = _repo()
    extract_id = "method:com.etl2.DataMigrationJob#extract()"
    assert repo.find_call_flow(extract_id) == []


def test_no_entrypoints_when_everything_has_a_caller():
    src = b"""
    package p;
    class A {
        void a() { b(); }
        void b() {}
    }
    """
    pf = parse_java_source("A.java", src)
    # 'a' has no caller in this snippet, so it *is* an entrypoint candidate;
    # only 'b' (called by 'a') must be excluded.
    repo = InMemoryGraphRepository(graph=build_graph([pf]))
    fqns = {e["fqn"] for e in repo.find_entrypoints()}
    assert "p.A#a()" in fqns
    assert "p.A#b()" not in fqns
