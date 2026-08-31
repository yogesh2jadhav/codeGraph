"""Phase 5 - Apache Spark analyzer.

Purely syntactic and heuristic. It reuses the call references already captured
by the Phase 3 body walk (``MethodDecl.references``) - no re-parsing - to:

  * decide which methods are Spark jobs (file imports ``org.apache.spark`` and
    the method uses Spark DataFrame/Dataset/SQL API calls)
  * list the transformations and actions each job uses
  * pull table names out of ``.table("t")`` / ``.saveAsTable("t")`` /
    ``.insertInto("t")`` and (best effort) file paths out of
    ``.parquet("p")`` / ``.csv("p")`` / ``.json("p")`` / ``.load("p")``

Graph additions:
  * ``spark_job`` / ``spark_transformations`` / ``spark_actions`` properties on
    the method node
  * ``READS_TABLE`` / ``WRITES_TABLE`` edges  method -> Table  (MEDIUM - heuristic)

``spark.sql("...")`` statements are left to the SQL analyzer (they are ordinary
SQL string literals).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from code_memory.models.code import ParsedFile
from code_memory.models.graph import CodeGraph, Confidence, Edge, Node

_TRANSFORMS = {
    "map", "flatMap", "mapPartitions", "filter", "select", "selectExpr",
    "where", "groupBy", "groupByKey", "agg", "join", "crossJoin", "union",
    "unionByName", "unionAll", "withColumn", "withColumnRenamed", "drop",
    "dropDuplicates", "distinct", "orderBy", "sort", "sortWithinPartitions",
    "limit", "repartition", "coalesce", "cache", "persist", "unpersist",
    "pivot", "rollup", "cube", "sample", "explode", "as", "alias", "toDF",
    "na", "fillna", "dropna", "replace",
}
_ACTIONS = {
    "collect", "collectAsList", "count", "show", "first", "head", "take",
    "takeAsList", "foreach", "foreachPartition", "reduce", "aggregate",
    "toLocalIterator", "isEmpty", "write", "save", "saveAsTable",
    "saveAsTextFile", "insertInto",
}
_IO_CALLS = {
    "sql", "table", "read", "write", "load", "parquet", "csv", "json", "orc",
    "text", "textFile", "jdbc", "format", "option", "saveAsTable", "insertInto",
}
_PATH_CALLS = {"parquet", "csv", "json", "orc", "text", "textFile", "load"}


@dataclass
class SparkJob:
    method_fqn: str
    location: str
    transformations: list[str] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)
    reads_tables: list[str] = field(default_factory=list)
    writes_tables: list[str] = field(default_factory=list)
    paths: list[str] = field(default_factory=list)
    sql_calls: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "method": self.method_fqn, "location": self.location,
            "transformations": self.transformations, "actions": self.actions,
            "reads_tables": self.reads_tables, "writes_tables": self.writes_tables,
            "paths": self.paths, "sql_calls": self.sql_calls,
        }


@dataclass
class SparkModel:
    jobs: list[SparkJob] = field(default_factory=list)
    detected: bool = False

    def is_present(self) -> bool:
        return self.detected and bool(self.jobs)

    def counts(self) -> dict[str, Any]:
        return {
            "spark_detected": self.detected,
            "spark_jobs": len(self.jobs),
            "spark_input_tables": sorted({t for j in self.jobs
                                          for t in j.reads_tables}),
            "spark_output_tables": sorted({t for j in self.jobs
                                           for t in j.writes_tables}),
        }


def _uses_spark(pf: ParsedFile) -> bool:
    return any(i.fqn.startswith("org.apache.spark") for i in pf.imports)


def analyze_spark(parsed: list[ParsedFile], graph: CodeGraph) -> SparkModel:
    model = SparkModel()
    model.detected = any(_uses_spark(pf) for pf in parsed)
    if not model.detected:
        return model

    for pf in parsed:
        if not _uses_spark(pf):
            continue
        for td in pf.all_types():
            for m in td.methods:
                job = _job_for_method(m, pf)
                if job is None:
                    continue
                model.jobs.append(job)
                _apply_to_graph(graph, job)
    return model


def _job_for_method(m, pf: ParsedFile) -> SparkJob | None:
    calls = [r for r in m.references if r.kind == "call"]
    if not calls:
        return None
    names = [c.name for c in calls]
    transforms = sorted({n for n in names if n in _TRANSFORMS})
    actions = sorted({n for n in names if n in _ACTIONS})
    io_hits = [n for n in names if n in _IO_CALLS]
    if not (transforms or actions or io_hits):
        return None

    job = SparkJob(
        method_fqn=m.fqn,
        location=f"{m.location.relative_path}:{m.location.line_start}",
        transformations=transforms, actions=actions,
    )
    reads, writes, paths = set(), set(), set()
    for c in calls:
        arg = (c.first_string_arg or "").strip()
        if c.name == "sql":
            job.sql_calls += 1
        elif c.name in ("saveAsTable", "insertInto") and arg:
            writes.add(arg.lower())
        elif c.name == "table" and arg:
            reads.add(arg.lower())
        elif c.name in _PATH_CALLS and arg and ("/" in arg or "." in arg):
            paths.add(arg)
    job.reads_tables = sorted(reads)
    job.writes_tables = sorted(writes)
    job.paths = sorted(paths)
    return job


def _apply_to_graph(graph: CodeGraph, job: SparkJob) -> None:
    node = graph.get(f"method:{job.method_fqn}")
    if node is not None:
        node.properties["spark_job"] = True
        node.properties["spark_transformations"] = job.transformations
        node.properties["spark_actions"] = job.actions
    ev = {"line_start": int(job.location.rsplit(":", 1)[-1])
          if ":" in job.location else 0,
          "file": job.location.rsplit(":", 1)[0]}
    for tbl in job.reads_tables:
        graph.add_node(Node(f"table:{tbl}", "Table", tbl, {"resolved": True}))
        graph.add_edge(Edge("READS_TABLE", f"method:{job.method_fqn}",
                            f"table:{tbl}", Confidence.MEDIUM, ev))
    for tbl in job.writes_tables:
        graph.add_node(Node(f"table:{tbl}", "Table", tbl, {"resolved": True}))
        graph.add_edge(Edge("WRITES_TABLE", f"method:{job.method_fqn}",
                            f"table:{tbl}", Confidence.MEDIUM, ev))
