from code_memory.config import load_config
from code_memory.graph import build_graph
from code_memory.graph.memory_repository import InMemoryGraphRepository
from code_memory.graph.repository import get_graph_repository
from code_memory.parsers.java import parse_java_source

CTRL = b"""
package a.web;
import a.svc.S;
import org.springframework.web.bind.annotation.*;
@RestController @RequestMapping("/x")
public class C {
    private final S s;
    public C(S s){ this.s = s; }
    @GetMapping("/go") public String go(){ return s.work(); }
}
"""
SVC = b"""
package a.svc;
import a.repo.R;
import org.springframework.stereotype.Service;
@Service public class S {
    private final R r;
    public S(R r){ this.r = r; }
    public String work(){ return r.fetch(); }
}
"""
REPO = b"""
package a.repo;
import org.springframework.stereotype.Repository;
@Repository public interface R {
    @org.springframework.data.jpa.repository.Query(value="SELECT * FROM widgets", nativeQuery=true)
    String fetch();
}
"""


def _repo():
    parsed = [parse_java_source(f"F{i}.java", s)
              for i, s in enumerate((CTRL, SVC, REPO))]
    g = build_graph(parsed)
    from code_memory.analyzers.spring import analyze_spring
    analyze_spring(parsed, g)
    return InMemoryGraphRepository(graph=g), g


def test_callers_callees():
    repo, _ = _repo()
    callers = {c["id"] for c in repo.find_callers("method:a.svc.S#work()")}
    assert "method:a.web.C#go()" in callers
    callees = {c["id"] for c in repo.find_callees("method:a.svc.S#work()")}
    assert "method:a.repo.R#fetch()" in callees


def test_impact_transitive():
    repo, _ = _repo()
    imp = repo.find_impact("method:a.repo.R#fetch()")
    assert "method:a.svc.S#work()" in imp["transitive_callers"]
    assert "method:a.web.C#go()" in imp["transitive_callers"]
    assert imp["direct_callers"] == ["method:a.svc.S#work()"]


def test_endpoint_flow():
    repo, _ = _repo()
    ep = next(n["id"] for n in repo.find_nodes(kind="Endpoint"))
    flow = repo.find_endpoint_flow(ep)
    assert flow["handler"] == "method:a.web.C#go()"
    ids = [step["id"] for step in flow["flow"]]
    assert ids[:2] == ["method:a.svc.S#work()", "method:a.repo.R#fetch()"]


def test_find_nodes_and_neighbors():
    repo, _ = _repo()
    hits = repo.find_nodes(name_contains="work")
    assert any(h["id"] == "method:a.svc.S#work()" for h in hits)
    nb = repo.neighbors("type:a.svc.S", edge_types=("INJECTS",), direction="out")
    assert nb and nb[0]["id"] == "type:a.repo.R"


def test_factory_defaults_to_memory(tmp_path):
    cfg = load_config()
    cfg.data["project"]["root"] = str(tmp_path)
    (tmp_path / ".code-memory" / "graph").mkdir(parents=True)
    (tmp_path / ".code-memory" / "graph" / "nodes.json").write_text("[]")
    (tmp_path / ".code-memory" / "graph" / "edges.json").write_text("[]")
    repo = get_graph_repository(cfg)
    assert repo.stats()["backend"] == "memory"
