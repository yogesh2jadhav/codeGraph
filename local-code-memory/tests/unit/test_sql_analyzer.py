from pathlib import Path

from code_memory.analyzers.sql import analyze_sql, looks_like_sql
from code_memory.analyzers.sql.extract import extract_sql_from_java, split_sql_file
from code_memory.analyzers.sql.parse import parse_sql
from code_memory.graph import build_graph
from code_memory.models.inventory import FileKind, ProjectInventory
from code_memory.parsers.java import parse_java_source


# -- extraction ---------------------------------------------------------
def test_looks_like_sql():
    assert looks_like_sql("  SELECT 1")
    assert looks_like_sql("INSERT INTO t VALUES (1)")
    assert looks_like_sql("with cte as (select 1) select * from cte")
    assert not looks_like_sql('"just a message"')
    assert not looks_like_sql("selectItem()")


def test_extract_query_annotation_and_literals():
    src = '''
    package r;
    interface Repo {
        @Query(value = "SELECT * FROM users WHERE id = ?1", nativeQuery = true)
        User find(String id);

        @Query("SELECT u FROM User u")
        java.util.List<User> all();
    }
    class Dao {
        void run(java.sql.Connection c) throws Exception {
            String q = "UPDATE accounts SET balance = 0 " +
                       "WHERE owner = ?";
            c.prepareStatement(q);
            c.prepareStatement("DELETE FROM sessions WHERE expired = 1");
        }
    }
    '''
    hits = extract_sql_from_java(src)
    kinds = sorted((h.kind, h.sql.split()[0].upper()) for h in hits)
    assert ("jpa-query-native", "SELECT") in kinds
    assert ("jpa-query", "SELECT") in kinds
    assert ("java-literal", "UPDATE") in kinds
    assert ("java-literal", "DELETE") in kinds


def test_split_sql_file():
    text = "-- a comment\nCREATE TABLE t (id int);\nINSERT INTO t VALUES (1);\n"
    stmts = split_sql_file(text)
    assert [s.split()[0] for s, _ in stmts] == ["CREATE", "INSERT"]


# -- parsing ----------------------------------------------------------
def test_parse_select_reads():
    p = parse_sql("SELECT a FROM users u JOIN orders o ON u.id = o.uid")
    assert p.statement_type == "SELECT"
    assert set(p.tables_read) == {"users", "orders"}
    assert p.tables_written == []


def test_parse_insert_select_read_write_split():
    p = parse_sql("INSERT INTO audit (x) SELECT y FROM events")
    assert p.statement_type == "INSERT"
    assert p.tables_written == ["audit"]
    assert p.tables_read == ["events"]


def test_parse_update_and_delete():
    assert parse_sql("UPDATE accounts SET n = 1 WHERE id = 2").tables_written == ["accounts"]
    assert parse_sql("DELETE FROM sessions WHERE x = 1").tables_written == ["sessions"]


def test_parse_fallback_on_garbage_dialect():
    p = parse_sql("SELECT * FROM weird `backtick`.tbl SAMPLE (10)")
    assert p.statement_type in ("SELECT", "UNKNOWN")
    assert any("tbl" in t or "weird" in t for t in p.tables_read) or not p.parsed_ok


# -- full analyzer over the graph -----------------------------------
def _inv_with_sql_file(tmp_path: Path) -> ProjectInventory:
    (tmp_path / "schema.sql").write_text(
        "CREATE TABLE users (id int);\nINSERT INTO users VALUES (1);",
        encoding="utf-8")
    inv = ProjectInventory(project_root=str(tmp_path), scan_id="s",
                           scanner_version="1", generated_at="now",
                           git_commit=None, git_branch=None)
    from code_memory.models.inventory import FileEntry
    inv.files.append(FileEntry("schema.sql", FileKind.SQL, 10, "h"))
    return inv


def test_analyze_sql_builds_graph(tmp_path):
    java = '''package d;
    class Dao {
      void q(java.sql.Connection c) throws Exception {
        c.prepareStatement("SELECT name FROM users WHERE id = ?");
        c.prepareStatement("INSERT INTO audit (msg) VALUES (?)");
      }
    }'''
    (tmp_path / "Dao.java").write_text(java, encoding="utf-8")
    pf = parse_java_source("Dao.java", java.encode())
    graph = build_graph([pf])
    inv = _inv_with_sql_file(tmp_path)

    model = analyze_sql(tmp_path, [pf], inv, graph)
    assert model.is_present()
    assert "users" in model.tables and "audit" in model.tables

    exec_edges = [e for e in graph.edges if e.type == "EXECUTES_SQL"]
    # the two java statements attribute to the enclosing method
    assert any(e.src == "method:d.Dao#q(java.sql.Connection)" for e in exec_edges)
    # the .sql file statements attribute to the file
    assert any(e.src == "file:schema.sql" for e in exec_edges)

    reads = {e.dst for e in graph.edges if e.type == "READS_TABLE"}
    writes = {e.dst for e in graph.edges if e.type == "WRITES_TABLE"}
    assert "table:users" in reads
    assert "table:audit" in writes
