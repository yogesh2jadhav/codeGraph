from code_memory.analyzers.spark import analyze_spark
from code_memory.graph import build_graph
from code_memory.parsers.java import parse_java_source

JOB = b"""
package etl;
import org.apache.spark.sql.Dataset;
import org.apache.spark.sql.SparkSession;

public class Job {
    public void run(SparkSession spark) {
        Dataset orders = spark.table("raw.orders");
        Dataset out = orders
            .filter("x = 1")
            .join(orders, "id")
            .groupBy("k")
            .withColumnRenamed("a", "b");
        out.write().saveAsTable("mart.summary");
        out.write().parquet("s3://b/out.parquet");
        out.show();
        out.count();
    }

    public void notSpark() {
        System.out.println("hi");
    }
}
"""
PLAIN = b"package p; class P { void m() { new java.util.ArrayList().add(1); } }"


def test_detects_spark_job_and_ops():
    pf = parse_java_source("Job.java", JOB)
    g = build_graph([pf])
    model = analyze_spark([pf], g)

    assert model.detected
    assert len(model.jobs) == 1                     # notSpark() excluded
    job = model.jobs[0]
    assert job.method_fqn == "etl.Job#run(SparkSession)"
    assert "filter" in job.transformations
    assert "join" in job.transformations
    assert "withColumnRenamed" in job.transformations
    assert set(job.actions) >= {"show", "count", "write", "saveAsTable"}
    assert job.reads_tables == ["raw.orders"]
    assert job.writes_tables == ["mart.summary"]
    assert "s3://b/out.parquet" in job.paths


def test_graph_edges_and_method_flags():
    pf = parse_java_source("Job.java", JOB)
    g = build_graph([pf])
    analyze_spark([pf], g)

    node = g.get("method:etl.Job#run(SparkSession)")
    assert node.properties.get("spark_job") is True
    assert "filter" in node.properties["spark_transformations"]

    reads = {(e.src, e.dst) for e in g.edges if e.type == "READS_TABLE"}
    writes = {(e.src, e.dst) for e in g.edges if e.type == "WRITES_TABLE"}
    assert ("method:etl.Job#run(SparkSession)", "table:raw.orders") in reads
    assert ("method:etl.Job#run(SparkSession)", "table:mart.summary") in writes


def test_non_spark_project_inert():
    pf = parse_java_source("P.java", PLAIN)
    model = analyze_spark([pf], build_graph([pf]))
    assert not model.detected
    assert not model.is_present()
