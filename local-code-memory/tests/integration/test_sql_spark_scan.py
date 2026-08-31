import json
import shutil
from pathlib import Path

from code_memory.pipeline import run_scan

FIXTURES = Path(__file__).parents[1] / "fixtures"


def _copy(name, tmp_path):
    dst = tmp_path / name
    shutil.copytree(FIXTURES / name, dst)
    return dst


def test_sql_from_native_query_in_spring_project(tmp_path, config_for):
    root = _copy("spring_api_sample", tmp_path)
    ctx, result = run_scan(config_for(root), mode="full")

    sql = result.java.sql
    assert sql.is_present()
    assert "orders" in sql.tables and "customers" in sql.tables

    edges = json.loads((root / ".code-memory/graph/edges.json").read_text())
    execs = {(e["src"], e["dst"].split(":")[0]) for e in edges
             if e["type"] == "EXECUTES_SQL"}
    # attributed to the repository method that carries the @Query
    assert any(src == "method:com.demo.repo.OrderRepository#findByStatus(String)"
               for src, _ in execs)
    reads = {e["dst"] for e in edges if e["type"] == "READS_TABLE"}
    assert {"table:orders", "table:customers"} <= reads

    assert (root / ".code-memory/context/13_sql.md").is_file()
    stats = json.loads(ctx.store.get_scan(ctx.scan_id)["stats_json"])
    assert stats["java"]["sql"]["sql_statements"] >= 1


def test_spark_etl_job_flow(tmp_path, config_for):
    root = _copy("spark_etl_sample", tmp_path)
    ctx, result = run_scan(config_for(root), mode="full")

    spark = result.java.spark
    assert spark.detected and spark.is_present()
    job = next(j for j in spark.jobs if j.method_fqn.endswith("run(SparkSession)"))
    assert {"filter", "join", "groupBy", "withColumnRenamed", "orderBy",
            "repartition"} <= set(job.transformations)
    assert job.reads_tables == ["raw.orders"]
    assert job.writes_tables == ["mart.daily_revenue"]
    assert job.sql_calls == 1

    # spark.sql("SELECT ... FROM raw.customers ...") picked up by the SQL analyzer
    assert "raw.customers" in result.java.sql.tables

    edges = json.loads((root / ".code-memory/graph/edges.json").read_text())
    writes = {(e["src"], e["dst"]) for e in edges if e["type"] == "WRITES_TABLE"}
    assert ("method:com.etl.DailyOrdersJob#run(SparkSession)",
            "table:mart.daily_revenue") in writes

    assert (root / ".code-memory/context/12_spark.md").is_file()
    overview = (root / ".code-memory/context/00_project_overview.md").read_text()
    assert "Spark" in overview
